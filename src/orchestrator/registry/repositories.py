"""Async repositories for versioned registry entities.

Both ``agent_templates`` and ``tool_contracts`` share the same table
shape, so one parametric repository serves both. The audit_log has
its own append-only helper.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.registry._common import LifecycleState
from orchestrator.registry.db.models import (
    AgentTemplateRow,
    AuditLogRow,
    GlossaryEntryRow,
    MemoryRow,
    ToolContractRow,
)

VersionedRow = TypeVar("VersionedRow", AgentTemplateRow, ToolContractRow, GlossaryEntryRow)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class NotFoundError(LookupError):
    """Raised when a versioned row is requested but absent."""


class AlreadyExistsError(ValueError):
    """Raised on attempt to create a row that already exists at (id, version)."""


class ImmutablePublishedError(ValueError):
    """Raised on attempt to modify a row that is already published."""


class VersionedRepo(Generic[VersionedRow]):
    """Async repository for a versioned registry entity."""

    def __init__(self, session: AsyncSession, model: type[VersionedRow]) -> None:
        self._session = session
        self._model: type[VersionedRow] = model

    async def create(
        self,
        *,
        id: str,
        version: str,
        description: str,
        tags: list[str],
        spec: dict[str, Any],
        status: LifecycleState = LifecycleState.DRAFT,
    ) -> VersionedRow:
        row: VersionedRow = self._model(
            id=id,
            version=version,
            description=description,
            tags=list(tags),
            spec_json=spec,
            status=status.value,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AlreadyExistsError(f"{self._model.__tablename__}: {id}@{version} exists") from exc
        return row

    async def get_by_id_version(self, id: str, version: str) -> VersionedRow | None:
        stmt = select(self._model).where(self._model.id == id, self._model.version == version)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_versions_for_id(self, id: str) -> list[VersionedRow]:
        stmt = select(self._model).where(self._model.id == id).order_by(self._model.version)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_page(
        self,
        *,
        tag: str | None = None,
        status: LifecycleState | None = None,
        after_pk: uuid.UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[VersionedRow], uuid.UUID | None]:
        """Keyset-paginated list ordered by ``pk DESC``.

        Returns ``(rows, next_cursor)``. ``next_cursor`` is the last row's
        pk when more pages exist, otherwise ``None``.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        stmt = select(self._model).order_by(self._model.pk.desc()).limit(limit + 1)
        if tag is not None:
            stmt = stmt.where(self._model.tags.contains([tag]))
        if status is not None:
            stmt = stmt.where(self._model.status == status.value)
        if after_pk is not None:
            stmt = stmt.where(self._model.pk < after_pk)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        next_cursor = rows[limit - 1].pk if len(rows) > limit else None
        return rows[:limit], next_cursor

    async def get_latest_published(self, id: str) -> VersionedRow | None:
        stmt = (
            select(self._model)
            .where(self._model.id == id, self._model.status == LifecycleState.PUBLISHED.value)
            .order_by(self._model.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def publish(self, id: str, version: str) -> VersionedRow:
        row = await self.get_by_id_version(id, version)
        if row is None:
            raise NotFoundError(f"{self._model.__tablename__}: {id}@{version} not found")
        if row.status == LifecycleState.DEPRECATED.value:
            raise ImmutablePublishedError(f"{id}@{version} is deprecated and cannot be republished")
        row.status = LifecycleState.PUBLISHED.value
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def mark_deprecated(self, id: str, version: str) -> VersionedRow:
        row = await self.get_by_id_version(id, version)
        if row is None:
            raise NotFoundError(f"{self._model.__tablename__}: {id}@{version} not found")
        row.status = LifecycleState.DEPRECATED.value
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def update_spec_if_draft(
        self, id: str, version: str, *, spec: dict[str, Any], description: str, tags: list[str]
    ) -> VersionedRow:
        """Allow edits only while a version is still in draft."""
        row = await self.get_by_id_version(id, version)
        if row is None:
            raise NotFoundError(f"{self._model.__tablename__}: {id}@{version} not found")
        if row.status != LifecycleState.DRAFT.value:
            raise ImmutablePublishedError(f"{id}@{version} is {row.status}; only draft rows are mutable.")
        row.spec_json = spec
        row.description = description
        row.tags = list(tags)
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row


class AuditLogRepo:
    """Append-only writer for the audit log."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def write(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        trace_id: str | None = None,
        tenant_id: str = "default",
    ) -> AuditLogRow:
        row = AuditLogRow(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_json=before,
            after_json=after,
            trace_id=trace_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row


def _tokenize(text: str) -> set[str]:
    """Lowercase word set for keyword-overlap ranking (Phase 1 retrieval)."""
    return {t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(t) > 2}


class MemoryRepo:
    """Cross-run semantic memory: write (seed/consolidate) + read (recall).

    Phase 1 retrieval ranks candidates by keyword overlap in Python, so it runs
    identically on SQLite (tests) and Postgres (prod) with no pgvector. The
    ``search`` signature is the contract a future embedding/ANN backend swaps
    in behind (docs/specs/cross-run-semantic-memory.md).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        repo_key: str,
        kind: str,
        statement: str,
        scope: str = "repo",
        evidence: dict[str, Any] | None = None,
        confidence: float = 0.5,
        trace_id: str | None = None,
        tenant_id: str = "default",
    ) -> MemoryRow:
        row = MemoryRow(
            tenant_id=tenant_id,
            repo_key=repo_key,
            kind=kind,
            scope=scope,
            statement=statement,
            evidence=evidence,
            confidence=confidence,
            trace_id=trace_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def search(
        self,
        *,
        query: str,
        repo_key: str,
        tenant_id: str = "default",
        kind: str | None = None,
        limit: int = 5,
    ) -> list[MemoryRow]:
        """Top memories for ``query``, scoped to this repo plus global memories.

        Ranked by keyword overlap with the statement, then confidence, then
        hits. Returns at most ``limit`` rows. Does not mutate; call
        ``record_hit`` on the ones the agent actually uses.
        """
        stmt = select(MemoryRow).where(
            MemoryRow.tenant_id == tenant_id,
            or_(MemoryRow.repo_key == repo_key, MemoryRow.scope == "global"),
        )
        if kind is not None:
            stmt = stmt.where(MemoryRow.kind == kind)
        rows = list((await self._session.execute(stmt)).scalars().all())

        q_tokens = _tokenize(query)

        def score(row: MemoryRow) -> tuple[int, float, int]:
            overlap = len(q_tokens & _tokenize(row.statement)) if q_tokens else 0
            return (overlap, row.confidence, row.hits)

        ranked = sorted(rows, key=score, reverse=True)
        # Drop zero-overlap rows only when the query had usable tokens — an empty
        # query falls back to confidence-ranked top-N.
        if q_tokens:
            ranked = [r for r in ranked if score(r)[0] > 0]
        return ranked[:limit]

    async def record_hit(self, pk: uuid.UUID) -> None:
        """Mark a memory as retrieved-and-used: bump hits + last_used_at."""
        row = await self._session.get(MemoryRow, pk)
        if row is not None:
            row.hits += 1
            row.last_used_at = datetime.now(UTC)
            await self._session.flush()

    async def reinforce(
        self, pk: uuid.UUID, *, run_id: str | None = None, delta: float = 0.1, ceiling: float = 0.99
    ) -> None:
        """A new run re-derived an existing memory: nudge confidence up and append
        the run to its evidence (the consolidation dedup-hit path, Phase 2)."""
        row = await self._session.get(MemoryRow, pk)
        if row is None:
            return
        row.confidence = min(ceiling, row.confidence + delta)
        row.last_used_at = datetime.now(UTC)  # a reinforcement is a use → spares it from decay
        if run_id:
            evidence = dict(row.evidence or {})
            run_ids = list(evidence.get("run_ids") or [])
            if run_id not in run_ids:
                run_ids.append(run_id)
            evidence["run_ids"] = run_ids
            row.evidence = evidence  # reassign so SQLAlchemy detects the JSON change
        await self._session.flush()

    async def decay(
        self,
        *,
        repo_key: str,
        tenant_id: str = "default",
        cutoff: datetime,
        delta: float = 0.05,
        floor: float = 0.15,
    ) -> dict[str, int]:
        """Age out memories unused since ``cutoff`` (Phase 3): drop confidence by
        ``delta``, and delete any that fall below ``floor``.

        Staleness uses ``coalesce(last_used_at, created_at)`` filtered DB-side, so
        freshly inserted/reinforced/recalled rows (timestamp ≈ now) are spared and
        in-memory staleness can't mislead it. Hard-delete below floor is safe: a
        memory is a lossy, regenerable index over the run bundles (canonical).
        """
        stmt = select(MemoryRow).where(
            MemoryRow.tenant_id == tenant_id,
            MemoryRow.repo_key == repo_key,
            func.coalesce(MemoryRow.last_used_at, MemoryRow.created_at) < cutoff,
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        decayed = deleted = 0
        for row in rows:
            row.confidence -= delta
            if row.confidence < floor:
                await self._session.delete(row)
                deleted += 1
            else:
                decayed += 1
        await self._session.flush()
        return {"decayed": decayed, "deleted": deleted}
