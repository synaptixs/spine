"""Control a long-running, gated SDLC workflow as a *job*.

The autonomous ``sdlc run`` workflow is long and pauses at two human gates, so
it can't be a single blocking call. These four operations let a caller (the MCP
plugin, a UI, a script) drive it over fast, non-blocking steps:

  start_run   → kick off the Temporal workflow, return a run id immediately
  run_status  → poll: workflow status + any pending gate
  decide_gate → approve / reject / modify a pending gate (records + signals the workflow)
  run_result  → the final result once COMPLETED

Backed by the same Temporal worker + approval repo the CLI/REST API use; needs
Mode-B infra (Temporal + Postgres). ``decide_gate`` mirrors the REST approval
decision path (repo.decide + audit + workflow signal).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger("orchestrator.sdlc.run_control")

_SIGNAL_BY_ACTION = {"approve": "approve", "reject": "deny", "modify_input": "modify_input"}


def _db_url() -> str:
    return os.getenv(
        "ORCHESTRATOR_DATABASE_URL",
        "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
    )


async def _client() -> Any:
    from orchestrator.temporal import connect_client
    from orchestrator.temporal.config import TemporalConfig

    return await connect_client(TemporalConfig.from_env())


def _resolve_gate(sdlc_id: str, gate: str) -> str:
    """Map a friendly gate name to its approval id (or pass an id through)."""
    return {"intents": f"sdlc-{sdlc_id}-0", "merge": f"sdlc-{sdlc_id}-1"}.get(gate, gate)


async def start_run(
    *,
    source: str,
    actor: str = "plugin",
    tenant_id: str = "default",
    create_jira: bool = False,
    max_features: int = 0,
    max_parallel: int = 2,
) -> dict[str, Any]:
    """Start ``SDLCWorkflow`` on the sdlc-tasks queue. Returns immediately.

    ``tenant_id`` (Bet 2c-ii) scopes the run's approval + audit rows; defaults
    to ``"default"`` for single-tenant installs.
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.intake.factory import SUPPORTED_SOURCE_KINDS
    from orchestrator.intake.service import parse_source_uri
    from orchestrator.sdlc.types import SDLCWorkflowInput
    from orchestrator.sdlc.worker import sdlc_task_queue
    from orchestrator.sdlc.workflows import SDLCWorkflow

    load_local_env()
    kind, _ = parse_source_uri(source)
    if kind not in SUPPORTED_SOURCE_KINDS:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_KINDS))
        raise ValueError(f"Unsupported source kind {kind!r} (supported: {supported}).")
    sdlc_id = uuid.uuid4().hex[:16]
    queue = sdlc_task_queue()
    client = await _client()
    await client.start_workflow(
        SDLCWorkflow.run,
        SDLCWorkflowInput(
            sdlc_id=sdlc_id,
            source_uri=source,
            actor=actor,
            tenant_id=tenant_id,
            trace_id=sdlc_id,
            dry_run_jira=not create_jira,
            max_features=max_features,
            max_parallel_features=max_parallel,
        ),
        id=f"task-{sdlc_id}",
        task_queue=queue,
    )
    return {
        "sdlc_id": sdlc_id,
        "workflow_id": f"task-{sdlc_id}",
        "task_queue": queue,
        "gates": {"intents": f"sdlc-{sdlc_id}-0", "merge": f"sdlc-{sdlc_id}-1"},
    }


async def run_status(sdlc_id: str) -> dict[str, Any]:
    """Workflow status + the pending gate (if any) awaiting a human decision."""
    from orchestrator.approval import ApprovalRequestRepo
    from orchestrator.registry.db.session import make_engine, make_session_factory

    client = await _client()
    desc = await client.get_workflow_handle(f"task-{sdlc_id}").describe()
    status = desc.status.name if desc.status else "UNKNOWN"

    engine = make_engine(_db_url())
    try:
        async with make_session_factory(engine)() as session:
            pending = [
                a for a in await ApprovalRequestRepo(session).list_pending(limit=500) if a.task_id == sdlc_id
            ]
    finally:
        await engine.dispose()

    gate = pending[0] if pending else None
    return {
        "sdlc_id": sdlc_id,
        "status": status,
        "awaiting_gate": gate.id if gate else None,
        "gate_title": gate.title if gate else None,
        "gate_description": gate.description if gate else None,
    }


async def decide_gate(
    sdlc_id: str,
    gate: str,
    action: str,
    *,
    actor: str = "plugin",
    rationale: str | None = None,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide a pending gate: records the decision (+ audit) and signals the workflow."""
    action = action.lower()
    if action not in _SIGNAL_BY_ACTION:
        raise ValueError(f"action must be one of {sorted(_SIGNAL_BY_ACTION)}, got {action!r}")
    if action == "modify_input" and not patch:
        raise ValueError("modify_input needs a non-empty patch")

    from orchestrator.approval import ApprovalDecision, ApprovalRequestRepo, ApprovalState
    from orchestrator.registry.db.session import make_engine, make_session_factory
    from orchestrator.registry.repositories import AuditLogRepo

    gate_id = _resolve_gate(sdlc_id, gate)
    new_state = ApprovalState.REJECTED if action == "reject" else ApprovalState.APPROVED
    decision = ApprovalDecision(rationale=rationale, modified_input=patch)

    engine = make_engine(_db_url())
    try:
        async with make_session_factory(engine)() as session:
            repo = ApprovalRequestRepo(session)
            existing = await repo.get(gate_id)
            if existing is None:
                raise KeyError(f"gate {gate_id!r} not found")
            if existing.state is not ApprovalState.PENDING:
                raise ValueError(f"gate {gate_id!r} already {existing.state.value}; decisions are immutable")
            updated = await repo.decide(gate_id, state=new_state, decided_by=actor, decision=decision)
            await AuditLogRepo(session).write(
                actor=actor,
                action=f"approval_{_SIGNAL_BY_ACTION[action]}",
                resource_type="approval",
                resource_id=gate_id,
                after={"state": (updated.state if updated else new_state).value, "decided_by": actor},
                trace_id=sdlc_id,
                tenant_id=existing.tenant_id,  # keep the audit row on the run's tenant
            )
            await session.commit()
    finally:
        await engine.dispose()

    await _signal(sdlc_id, _SIGNAL_BY_ACTION[action], patch if action == "modify_input" else None)
    return {"gate": gate_id, "action": action, "state": new_state.value}


async def run_result(sdlc_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """The workflow's final result once COMPLETED; status only otherwise."""
    client = await _client()
    handle = client.get_workflow_handle(f"task-{sdlc_id}")
    desc = await handle.describe()
    status = desc.status.name if desc.status else "UNKNOWN"
    if status != "COMPLETED":
        return {"sdlc_id": sdlc_id, "status": status, "result": None}
    result = await asyncio.wait_for(handle.result(), timeout=timeout)
    return {"sdlc_id": sdlc_id, "status": status, "result": result}


async def _signal(task_id: str, signal_name: str, arg: object | None) -> None:
    """Best-effort Temporal signal to ``task-{task_id}`` (decision is already durable)."""
    try:
        handle = (await _client()).get_workflow_handle(f"task-{task_id}")
        if arg is None:
            await handle.signal(signal_name)
        else:
            await handle.signal(signal_name, arg)
    except Exception as exc:  # noqa: BLE001 — the row is saved; signal is best-effort
        logger.warning("run_control.signal_failed", extra={"task_id": task_id, "error": str(exc)[:200]})


__all__ = ["decide_gate", "run_result", "run_status", "start_run"]
