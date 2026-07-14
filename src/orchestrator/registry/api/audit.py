"""Governance & trust API (Phase C): audit log, policy/budget, run export.

All three read the append-only ``audit_log`` — the single source of truth:

- ``GET /v1/audit``                    — C1: filterable audit-log query.
- ``GET /v1/audit/{run_id}/governance`` — C2: per-run spend vs cap + policy/approval decisions.
- ``GET /v1/audit/{run_id}/export``     — C3: the run's full timeline as a downloadable bundle.

Honesty notes baked into the responses: the *agentic tool-call* allow/deny policy
is trace/OTel-only (never persisted), so governance surfaces only what the audit
log actually holds — the output-policy verifier, budget breaches, and approvals.
Replay isn't exposed (no turnkey entry point; it re-executes tools live).
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from orchestrator.registry.api.deps import PrincipalDep, SessionDep
from orchestrator.registry.db.models import AuditLogRow

router = APIRouter(prefix="/v1/audit", tags=["audit"])

_MAX_SCAN = 5000
# SDLC runs terminate with one of these actions → a derived run state.
_TERMINAL_STATE = {
    "sdlc_prs_merged": "merged",
    "sdlc_features_failed": "failed",
    "sdlc_integration_failed": "failed",
    "sdlc_merge_failed": "failed",
    "sdlc_merge_denied": "denied",
    "sdlc_intents_denied": "denied",
    "sdlc_cancelled": "cancelled",
    "capability_completed": "completed",
    "capability_failed": "failed",
}

_POLICY_NOTE = (
    "Agentic tool-call allow/deny decisions are traced (OpenTelemetry) but not persisted to the "
    "audit log; this view shows what the log holds — the output-policy verifier, budget breaches, "
    "and human approvals."
)


def _budget_cap_usd() -> float:
    try:
        return float(os.getenv("SDLC_RUN_BUDGET_USD", "25") or 25)
    except ValueError:
        return 25.0


def _iso(row: AuditLogRow) -> str:
    return row.timestamp.isoformat() if row.timestamp else ""


# --------------------------------------------------------------------------- #
# C1 — audit-log query
# --------------------------------------------------------------------------- #
class AuditRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    trace_id: str | None
    after: dict[str, Any] | None


class AuditPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditRow]


def _to_row(r: AuditLogRow) -> AuditRow:
    return AuditRow(
        timestamp=_iso(r),
        actor=r.actor,
        action=r.action,
        resource_type=r.resource_type,
        resource_id=r.resource_id,
        trace_id=r.trace_id,
        after=r.after_json,
    )


@router.get("", response_model=AuditPage)
async def query_audit(
    session: SessionDep,
    principal: PrincipalDep,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
) -> AuditPage:
    """Filterable view of the append-only audit log for the caller's tenant
    (most-recent first). ``run_id`` matches either a row's resource_id or its
    trace_id, so it returns a whole run's activity."""
    limit = min(max(limit, 1), 500)
    stmt = select(AuditLogRow).where(AuditLogRow.tenant_id == principal.tenant_id)
    if actor:
        stmt = stmt.where(AuditLogRow.actor == actor)
    if action:
        stmt = stmt.where(AuditLogRow.action == action)
    if resource_type:
        stmt = stmt.where(AuditLogRow.resource_type == resource_type)
    if run_id:
        stmt = stmt.where(or_(AuditLogRow.resource_id == run_id, AuditLogRow.trace_id == run_id))
    stmt = stmt.order_by(AuditLogRow.timestamp.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return AuditPage(items=[_to_row(r) for r in rows])


# --------------------------------------------------------------------------- #
# Shared: fetch one run's rows (oldest-first)
# --------------------------------------------------------------------------- #
async def _fetch_run(session: Any, tenant: str, run_id: str) -> list[AuditLogRow]:
    stmt = (
        select(AuditLogRow)
        .where(
            AuditLogRow.tenant_id == tenant,
            or_(AuditLogRow.resource_id == run_id, AuditLogRow.trace_id == run_id),
        )
        .order_by(AuditLogRow.timestamp)
        .limit(_MAX_SCAN)
    )
    return list((await session.execute(stmt)).scalars().all())


def _derive_state(rows: list[AuditLogRow]) -> str:
    for r in reversed(rows):  # newest-last list → scan newest-first
        mapped = _TERMINAL_STATE.get(r.action)
        if mapped:
            return mapped
    return "running"


def _governance(run_id: str, rows: list[AuditLogRow]) -> dict[str, Any]:
    tool_cost = 0.0
    tool_calls = 0
    breaches: list[dict[str, Any]] = []
    policy: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    for r in rows:
        after = r.after_json or {}
        if r.action == "tool_invocation":
            tool_calls += 1
            with contextlib.suppress(TypeError, ValueError):
                tool_cost += float(after.get("cost_usd") or 0)
        elif r.action == "sdlc_budget_exhausted":
            breaches.append(
                {
                    "stage": after.get("stage"),
                    "spent_usd": after.get("spent_usd"),
                    "max_cost_usd": after.get("max_cost_usd"),
                    "at": _iso(r),
                }
            )
        elif r.action == "verifier_execution" and after.get("verifier_id") == "policy":
            payload = after.get("payload") or {}
            rules = list(payload.keys()) if isinstance(payload, dict) else []
            policy.append({"outcome": after.get("outcome"), "rules": rules, "at": _iso(r)})
        elif r.action.startswith("approval_") or r.resource_type in ("approval", "sdlc_approval"):
            approvals.append({"action": r.action, "actor": r.actor, "at": _iso(r)})

    cap = _budget_cap_usd()
    return {
        "run_id": run_id,
        "spend": {
            "tool_cost_usd": round(tool_cost, 4),
            "tool_calls": tool_calls,
            "budget_cap_usd": cap,
            "over_cap": bool(cap > 0 and tool_cost > cap),
            "breaches": breaches,
        },
        "policy": policy,
        "approvals": approvals,
        "note": _POLICY_NOTE,
    }


# --------------------------------------------------------------------------- #
# C2 — per-run policy & budget
# --------------------------------------------------------------------------- #
class GovernanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    spend: dict[str, Any]
    policy: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    note: str


@router.get("/{run_id}/governance", response_model=GovernanceResponse)
async def run_governance(run_id: str, session: SessionDep, principal: PrincipalDep) -> GovernanceResponse:
    rows = await _fetch_run(session, principal.tenant_id, run_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r}")
    return GovernanceResponse(**_governance(run_id, rows))


# --------------------------------------------------------------------------- #
# C3 — export a run bundle (from the persisted audit log)
# --------------------------------------------------------------------------- #
@router.get("/{run_id}/export")
async def export_run(run_id: str, session: SessionDep, principal: PrincipalDep) -> Response:
    """Download the run's full audit timeline as a JSON bundle — the persisted
    "receipt" for a run (state, governance summary, every event). Replay is not
    exposed here: it has no turnkey entry point and re-executes tools live."""
    rows = await _fetch_run(session, principal.tenant_id, run_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no run {run_id!r}")
    bundle = {
        "run_id": run_id,
        "state": _derive_state(rows),
        "started_at": _iso(rows[0]),
        "updated_at": _iso(rows[-1]),
        "events": len(rows),
        "governance": _governance(run_id, rows),
        "timeline": [
            {
                "timestamp": _iso(r),
                "actor": r.actor,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "before": r.before_json,
                "after": r.after_json,
            }
            for r in rows
        ],
    }
    import json

    body = json.dumps(bundle, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.bundle.json"'},
    )
