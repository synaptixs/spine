"""Sprint 14.2: ApprovalRequestRepo — Postgres-backed CRUD for approvals.

Two access patterns drive the index choices on ``approval_requests``:

  - **List pending** (REST queue UI): filter on ``state='pending'``,
    paginate by ``created_at DESC``.
  - **Lookup by id** (decision arrives at REST): ``id`` is unique + indexed.

Sprint 14.9: every state-changing write computes a ``before_hash`` over the
previous approval row for the same task, so the approval-decision chain
for a task is gap-detectable without extending Merkle-style chaining to
all of audit_log.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.approval.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    ApprovalTimeout,
    Approver,
    RiskClassification,
)
from orchestrator.registry.db.models import ApprovalRequestRow


class ApprovalRequestRepo:
    """Async repository over ApprovalRequestRow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, request: ApprovalRequest) -> ApprovalRequest:
        """Insert a new pending request. Computes ``before_hash`` over the
        prior approval row for the same task before persisting."""
        prior_hash = await self._latest_hash_for_task(request.task_id)
        request = request.model_copy(update={"before_hash": prior_hash})
        row = _to_row(request)
        self._session.add(row)
        await self._session.flush()
        return _to_model(row)

    async def get(self, approval_id: str, *, tenant_id: str | None = None) -> ApprovalRequest | None:
        """Lookup by id, optionally scoped to a tenant (Bet 2c-ii). When
        ``tenant_id`` is given, a row owned by another tenant reads as missing —
        the caller surfaces 404, never leaking cross-tenant ids."""
        stmt = select(ApprovalRequestRow).where(ApprovalRequestRow.id == approval_id)
        if tenant_id is not None:
            stmt = stmt.where(ApprovalRequestRow.tenant_id == tenant_id)
        result = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_model(result) if result is not None else None

    async def list_timed_out(self, *, now: datetime | None = None) -> list[ApprovalRequest]:
        """Return pending approvals whose ``timeout.after_seconds`` has
        elapsed since ``created_at``. Sprint 14.8's sweep workflow uses this
        to decide which rows need an auto_action applied."""
        threshold = now or datetime.now(UTC)
        stmt = select(ApprovalRequestRow).where(
            ApprovalRequestRow.state == ApprovalState.PENDING.value,
            ApprovalRequestRow.timeout_json.is_not(None),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        timed_out: list[ApprovalRequest] = []
        for row in rows:
            timeout_cfg = row.timeout_json or {}
            after = timeout_cfg.get("after_seconds")
            if not isinstance(after, int):
                continue
            if (threshold - row.created_at).total_seconds() >= after:
                timed_out.append(_to_model(row))
        return timed_out

    async def list_pending(self, *, limit: int = 100, tenant_id: str | None = None) -> list[ApprovalRequest]:
        """Pending-queue lookup: latest first, capped for the UI. Scoped to
        ``tenant_id`` when given (Bet 2c-ii) so the queue shows only the caller's
        tenant."""
        stmt = (
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.state == ApprovalState.PENDING.value)
            .order_by(desc(ApprovalRequestRow.created_at))
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(ApprovalRequestRow.tenant_id == tenant_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_model(r) for r in rows]

    async def decide(
        self,
        approval_id: str,
        *,
        state: ApprovalState,
        decided_by: str,
        decision: ApprovalDecision | None = None,
        tenant_id: str | None = None,
    ) -> ApprovalRequest | None:
        """Apply a decision. Returns the updated row, or None if missing.

        Refuses to transition out of a terminal state — the API surface
        catches this and returns 409. Scoped to ``tenant_id`` when given so a
        cross-tenant decide reads as missing (404).
        """
        stmt = select(ApprovalRequestRow).where(ApprovalRequestRow.id == approval_id)
        if tenant_id is not None:
            stmt = stmt.where(ApprovalRequestRow.tenant_id == tenant_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if row.state != ApprovalState.PENDING.value:
            return _to_model(row)

        row.state = state.value
        row.decided_by = decided_by
        row.decided_at = datetime.now(UTC)
        if decision is not None:
            row.decision_rationale = decision.rationale
            row.modified_input_json = decision.modified_input
        await self._session.flush()
        return _to_model(row)

    async def _latest_hash_for_task(self, task_id: str) -> str | None:
        """Return the hash-digest of the most recent approval row for
        ``task_id``, or None if this is the first approval on the task.

        Hash is computed over a stable JSON projection of the prior row,
        so re-ordering or insertion shows up at verification time.
        """
        stmt = (
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.task_id == task_id)
            .order_by(desc(ApprovalRequestRow.created_at))
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _row_hash(row)


# ---- row ↔ model helpers ---------------------------------------------------


def _row_hash(row: ApprovalRequestRow) -> str:
    """Stable digest over the row's identifying + state fields."""
    payload = {
        "id": row.id,
        "task_id": row.task_id,
        "state": row.state,
        "before_node_id": row.before_node_id,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "before_hash": row.before_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _to_row(model: ApprovalRequest) -> ApprovalRequestRow:
    return ApprovalRequestRow(
        id=model.id,
        task_id=model.task_id,
        tenant_id=model.tenant_id,
        before_node_id=model.before_node_id,
        title=model.title,
        description=model.description,
        action_summary=model.action_summary,
        risk_classification=model.risk_classification.value,
        affected_resources=list(model.affected_resources),
        approvers_json=[a.model_dump() for a in model.approvers],
        timeout_json=model.timeout.model_dump() if model.timeout else None,
        notification_channels=list(model.notification_channels),
        state=model.state.value,
        decided_by=model.decided_by,
        decision_rationale=model.decision_rationale,
        modified_input_json=model.modified_input,
        decided_at=model.decided_at,
        before_hash=model.before_hash,
        trace_id=model.trace_id,
    )


def _to_model(row: ApprovalRequestRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=row.id,
        task_id=row.task_id,
        tenant_id=row.tenant_id,
        before_node_id=row.before_node_id,
        title=row.title,
        description=row.description,
        action_summary=row.action_summary,
        risk_classification=RiskClassification(row.risk_classification),
        affected_resources=list(row.affected_resources or []),
        approvers=[Approver(**a) for a in (row.approvers_json or [])],
        timeout=ApprovalTimeout(**row.timeout_json) if row.timeout_json else None,
        notification_channels=list(row.notification_channels or []),
        state=ApprovalState(row.state),
        decided_by=row.decided_by,
        decision_rationale=row.decision_rationale,
        modified_input=dict(row.modified_input_json) if row.modified_input_json else None,
        decided_at=row.decided_at,
        before_hash=row.before_hash,
        trace_id=row.trace_id,
        created_at=row.created_at,
    )


def _coerce_state(raw: str | Any) -> ApprovalState:
    """Convenience for callers loading from JSON-y dicts."""
    return raw if isinstance(raw, ApprovalState) else ApprovalState(str(raw))
