"""Sprint 14.3 (partial): REST surface for approval requests.

Bundle 1 lands the read endpoints (list pending, get detail). Bundle 2
adds the decision endpoints (approve, reject, modify_input) and audit
chaining. Bundle 3 wires the Temporal-side workflow to actually pause
on these requests.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict

from orchestrator.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestRepo,
    ApprovalState,
)
from orchestrator.registry.api.deps import Principal, PrincipalDep, SessionDep, TraceIdDep
from orchestrator.registry.repositories import AuditLogRepo

logger = logging.getLogger("orchestrator.registry.approvals")

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


class ApprovalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalRequest]


@router.get("", response_model=ApprovalListResponse)
async def list_pending_approvals(
    session: SessionDep,
    principal: PrincipalDep,
    limit: int = 100,
) -> ApprovalListResponse:
    """List pending approvals for the caller's tenant, latest first (Bet 2c-ii).
    A wildcard/default principal (single-key install) sees its ``"default"``
    tenant — i.e. everything, as before."""
    items = await ApprovalRequestRepo(session).list_pending(
        limit=min(max(limit, 1), 500), tenant_id=principal.tenant_id
    )
    return ApprovalListResponse(items=items)


@router.get("/{approval_id}", response_model=ApprovalRequest)
async def get_approval(
    approval_id: str,
    session: SessionDep,
    principal: PrincipalDep,
) -> ApprovalRequest:
    """Detail view, scoped to the caller's tenant. A row owned by another
    tenant reads as 404 (no cross-tenant id leakage)."""
    record = await ApprovalRequestRepo(session).get(approval_id, tenant_id=principal.tenant_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found.")
    return record


@router.post("/{approval_id}/approve", response_model=ApprovalRequest)
async def approve(
    approval_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    trace_id: TraceIdDep,
    payload: ApprovalDecision = Body(default_factory=ApprovalDecision),  # noqa: B008 — FastAPI dep
) -> ApprovalRequest:
    """Approve a pending request. Sends a Temporal ``approve`` signal to the
    waiting workflow once the row is updated."""
    return await _decide(
        approval_id=approval_id,
        new_state=ApprovalState.APPROVED,
        signal_name="approve",
        decision=payload,
        session=session,
        principal=principal,
        trace_id=trace_id,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalRequest)
async def reject(
    approval_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    trace_id: TraceIdDep,
    payload: ApprovalDecision = Body(default_factory=ApprovalDecision),  # noqa: B008
) -> ApprovalRequest:
    """Reject a pending request. Workflow terminates after the ``deny``
    signal lands."""
    return await _decide(
        approval_id=approval_id,
        new_state=ApprovalState.REJECTED,
        signal_name="deny",
        decision=payload,
        session=session,
        principal=principal,
        trace_id=trace_id,
    )


@router.post("/{approval_id}/modify_input", response_model=ApprovalRequest)
async def modify_input(
    approval_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    trace_id: TraceIdDep,
    payload: ApprovalDecision = Body(...),
) -> ApprovalRequest:
    """Approve with a modified input. The patch ships to the workflow via
    the ``modify_input`` signal — the next pass merges it into the
    upcoming activity request before resuming."""
    if not payload.modified_input:
        raise HTTPException(
            status_code=400,
            detail="modify_input requires a non-empty modified_input patch in the body.",
        )
    return await _decide(
        approval_id=approval_id,
        new_state=ApprovalState.APPROVED,
        signal_name="modify_input",
        signal_arg=payload.modified_input,
        decision=payload,
        session=session,
        principal=principal,
        trace_id=trace_id,
    )


async def _decide(
    *,
    approval_id: str,
    new_state: ApprovalState,
    signal_name: str,
    decision: ApprovalDecision,
    session: object,  # AsyncSession; typed loosely so the route module stays slim
    principal: Principal,
    trace_id: str | None,
    signal_arg: object | None = None,
) -> ApprovalRequest:
    """Shared decision path. Enforces tenant + role (Bet 2c-ii), validates the
    row is still pending, applies the decision, writes the audit row, then
    signals the workflow.

    Workflow-signal dispatch is best-effort: if Temporal isn't reachable
    (e.g. local dev without docker), we still record the decision and
    audit row so a human operator can resume manually. The endpoint
    returns a 200 with a warning logged.
    """
    repo = ApprovalRequestRepo(session)  # type: ignore[arg-type]
    # Tenant scope: a row owned by another tenant reads as missing → 404, so we
    # never reveal that a cross-tenant approval id exists.
    existing = await repo.get(approval_id, tenant_id=principal.tenant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found.")
    if existing.state is not ApprovalState.PENDING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Approval {approval_id!r} is already {existing.state.value!r}; decisions are immutable."
            ),
        )
    # Role enforcement: the caller must hold one of the approval's required
    # roles. ``"any"`` (the default when the run named no roles) and a wildcard
    # principal both pass — preserving single-tenant behavior.
    required = [a.role for a in existing.approvers]
    if not principal.has_role(*required):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Principal {principal.id!r} (roles {sorted(principal.roles)}) may not decide "
                f"approval {approval_id!r}; one of {required} is required."
            ),
        )

    updated = await repo.decide(
        approval_id,
        state=new_state,
        decided_by=principal.id,
        decision=decision,
        tenant_id=principal.tenant_id,
    )
    if updated is None:  # race with deletion — extremely unlikely
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found.")

    await AuditLogRepo(session).write(  # type: ignore[arg-type]
        actor=principal.id,
        action=f"approval_{signal_name}",
        resource_type="approval",
        resource_id=approval_id,
        tenant_id=principal.tenant_id,
        before={"state": existing.state.value, "before_hash": existing.before_hash},
        after={
            "state": updated.state.value,
            "decided_by": updated.decided_by,
            "decided_roles": sorted(r for r in principal.roles if r in required) or sorted(principal.roles),
            "rationale": updated.decision_rationale,
            "modified_input": updated.modified_input,
            "before_hash": updated.before_hash,
        },
        trace_id=trace_id,
    )
    await session.commit()  # type: ignore[attr-defined]

    # Best-effort workflow signal. We don't fail the REST call if Temporal
    # is unreachable — the decision is already durably recorded.
    await _send_workflow_signal(updated.task_id, signal_name, signal_arg)
    return updated


async def _send_workflow_signal(task_id: str, signal_name: str, signal_arg: object | None) -> None:
    """Dispatch a Temporal signal to ``task-{task_id}`` workflow.

    Lazy-imports the Temporal client so the synchronous path doesn't pay
    the SDK import cost when no approval is in flight. Failures log + swallow:
    REST clients see the decision recorded even if the worker is down.
    """
    if os.getenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL"):
        return  # Tests opt out of touching Temporal here.

    try:
        from orchestrator.temporal import connect_client
        from orchestrator.temporal.config import TemporalConfig

        cfg = TemporalConfig.from_env()
        client = await connect_client(cfg)
        handle = client.get_workflow_handle(workflow_id=f"task-{task_id}")
        if signal_arg is None:
            await handle.signal(signal_name)
        else:
            await handle.signal(signal_name, signal_arg)
    except Exception as exc:  # noqa: BLE001 — best-effort; the row is already saved
        logger.warning(
            "approval.signal_dispatch_failed",
            extra={"task_id": task_id, "signal": signal_name, "error": str(exc)},
        )
