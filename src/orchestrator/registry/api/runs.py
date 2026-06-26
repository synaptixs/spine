"""SDLC runs listing — the data behind the console's runs dashboard (G12).

``GET /v1/runs`` summarizes SDLC pipeline runs from the append-only audit
log: one row per ``sdlc_id`` with its derived state, last action, and
timestamps. The console renders this as a dashboard; each run links to the
existing ``/trace/{id}`` timeline for the full step-by-step view.

A run's *state* is derived from its terminal audit action (the workflow
writes exactly one): merged / failed / denied / cancelled, else running.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from orchestrator.registry.api.deps import PrincipalDep, SessionDep
from orchestrator.registry.db.models import AuditLogRow

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# Terminal audit actions → run state. First match (latest-first scan) wins.
_TERMINAL_STATE = {
    "sdlc_prs_merged": "merged",
    "sdlc_features_failed": "failed",
    "sdlc_integration_failed": "failed",
    "sdlc_merge_failed": "failed",
    "sdlc_merge_denied": "denied",
    "sdlc_intents_denied": "denied",
    "sdlc_cancelled": "cancelled",
}
# Cap the audit scan so one query can't read an unbounded log.
_MAX_SCAN = 4000


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdlc_id: str
    state: str  # merged | failed | denied | cancelled | running
    last_action: str
    started_at: str
    updated_at: str
    events: int


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunSummary]


@router.get("", response_model=RunListResponse)
async def list_runs(session: SessionDep, principal: PrincipalDep, limit: int = 50) -> RunListResponse:
    """List recent SDLC runs for the caller's tenant (most-recently-active
    first), derived from audit (Bet 2c-ii tenant scoping)."""
    limit = min(max(limit, 1), 200)
    stmt = (
        select(AuditLogRow)
        .where(AuditLogRow.resource_type == "sdlc", AuditLogRow.tenant_id == principal.tenant_id)
        .order_by(AuditLogRow.timestamp.desc())
        .limit(_MAX_SCAN)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    # Group by sdlc_id (resource_id). Rows arrive newest-first.
    by_run: dict[str, list[AuditLogRow]] = {}
    for row in rows:
        by_run.setdefault(row.resource_id, []).append(row)

    summaries = [_summarize(run_id, events) for run_id, events in by_run.items()]
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return RunListResponse(items=summaries[:limit])


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        ..., description="Source URI, e.g. confluence://<id>, mcp-confluence://<id>, file://..."
    )
    create_jira: bool = Field(default=False, description="Write real Jira issues (default: dry-run).")


class StartRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdlc_id: str
    workflow_id: str
    task_queue: str
    gates: dict[str, str]


@router.post("/start", response_model=StartRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: StartRunRequest, principal: PrincipalDep) -> StartRunResponse:
    """Delegate a feature run to the SDLC pipeline (the inbox composer's action).

    Kicks off ``SDLCWorkflow`` on the Temporal queue and returns immediately with
    the run id + gate ids; the inbox then tracks it live via ``/v1/stream``. The
    run is scoped to the caller's tenant and attributed to their id."""
    from orchestrator.sdlc.run_control import start_run as _start

    try:
        result = await _start(
            source=body.source,
            actor=principal.id,
            tenant_id=principal.tenant_id,
            create_jira=body.create_jira,
        )
    except ValueError as exc:  # unsupported / malformed source URI
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # Temporal unreachable, etc. — surface, don't 500-trace
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"could not start run (is the Temporal worker up?): {exc}",
        ) from exc
    return StartRunResponse(**result)


def _summarize(run_id: str, events_newest_first: list[AuditLogRow]) -> RunSummary:
    newest = events_newest_first[0]
    oldest = events_newest_first[-1]
    state = "running"
    for row in events_newest_first:  # newest-first → first terminal action wins
        mapped = _TERMINAL_STATE.get(row.action)
        if mapped is not None:
            state = mapped
            break
    return RunSummary(
        sdlc_id=run_id,
        state=state,
        last_action=newest.action,
        started_at=_iso(oldest),
        updated_at=_iso(newest),
        events=len(events_newest_first),
    )


def _iso(row: AuditLogRow) -> str:
    return row.timestamp.isoformat() if row.timestamp else ""
