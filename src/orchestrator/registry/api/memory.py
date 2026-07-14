"""Cross-run memory API (Phase E2): what the engineer has learned across runs.

Read view over the ``agent_memory`` table (``MemoryRepo``): the conventions,
pitfalls, and facts consolidated from prior runs, scoped per repo (plus global
memories). Browse a repo's memories, or search them by keyword (the same
overlap ranking the codegen loop uses at recall time).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import distinct, select

from orchestrator.registry.api.deps import PrincipalDep, SessionDep
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo

router = APIRouter(prefix="/v1/memory", tags=["memory"])

_MAX = 200


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pk: uuid.UUID
    repo_key: str
    kind: str
    scope: str
    statement: str
    confidence: float
    hits: int
    created_at: datetime
    trace_id: str | None
    evidence: dict[str, Any] | None


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MemoryItem]


class ReposResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repos: list[str]


def _to_item(r: MemoryRow) -> MemoryItem:
    return MemoryItem(
        pk=r.pk,
        repo_key=r.repo_key,
        kind=r.kind,
        scope=r.scope,
        statement=r.statement,
        confidence=r.confidence,
        hits=r.hits,
        created_at=r.created_at,
        trace_id=r.trace_id,
        evidence=r.evidence,
    )


@router.get("/repos", response_model=ReposResponse)
async def list_repos(session: SessionDep, principal: PrincipalDep) -> ReposResponse:
    """The distinct repo keys that have memories, for the browser's picker."""
    stmt = (
        select(distinct(MemoryRow.repo_key))
        .where(MemoryRow.tenant_id == principal.tenant_id)
        .order_by(MemoryRow.repo_key)
    )
    return ReposResponse(repos=list((await session.execute(stmt)).scalars().all()))


@router.get("", response_model=MemoryListResponse)
async def list_memory(
    session: SessionDep,
    principal: PrincipalDep,
    repo_key: str | None = None,
    kind: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> MemoryListResponse:
    """Browse or search cross-run memories for the caller's tenant.

    With ``query`` + ``repo_key`` it uses the recall ranking (keyword overlap,
    then confidence — the same the loop uses, and includes global memories).
    Otherwise it lists by confidence, optionally filtered to a repo and/or kind."""
    limit = min(max(limit, 1), _MAX)
    if query and repo_key:
        rows = await MemoryRepo(session).search(
            query=query, repo_key=repo_key, tenant_id=principal.tenant_id, kind=kind, limit=limit
        )
        return MemoryListResponse(items=[_to_item(r) for r in rows])

    stmt = select(MemoryRow).where(MemoryRow.tenant_id == principal.tenant_id)
    if repo_key:
        stmt = stmt.where(MemoryRow.repo_key == repo_key)
    if kind:
        stmt = stmt.where(MemoryRow.kind == kind)
    stmt = stmt.order_by(MemoryRow.confidence.desc(), MemoryRow.created_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return MemoryListResponse(items=[_to_item(r) for r in rows])
