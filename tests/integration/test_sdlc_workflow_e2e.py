"""Block C: full-loop SDLC integration test (real Temporal + real Postgres).

Unlike ``tests/sdlc/test_workflows.py`` (in-process time-skipping server, stub
activities), this drives the **whole** loop the way production does:

  - a real Temporal server (docker-compose ``temporal`` service on :7233),
  - a real in-process SDLC worker running the **real** ``SDLCActivities``
    (real git worktrees in a tmp dir, stub adapters, and an injected
    fake ``service_builder`` so intake stays offline — no Confluence creds),
  - real Postgres rows: each gate persists a decidable ``ApprovalRequest`` and
    every stage writes an ``audit_log`` row.

The gates are decided exactly the way the REST ``/v1/approvals/*`` API decides
them: poll Postgres until the gate's row is ``pending``, then signal the
workflow handle ``task-{sdlc_id}`` — the id convention the API routes on.

Skips cleanly when Temporal isn't reachable or ``git`` is missing, so the
default ``-m 'not integration'`` run never touches it. Manual run::

    docker compose -f docker-compose.dev.yml up -d temporal temporal-postgres postgres
    uv run pytest tests/integration/test_sdlc_workflow_e2e.py -m integration -v
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator.approval.repository import ApprovalRequestRepo
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.specs import FeatureSpec
from orchestrator.sdlc.activities import SDLCActivities
from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.types import SDLCWorkflowInput
from orchestrator.sdlc.worker import sdlc_activity_methods
from orchestrator.sdlc.workflows import FeatureImplementationWorkflow, SDLCWorkflow
from orchestrator.sdlc.workspace import WorkspaceManager
from orchestrator.temporal.config import TemporalConfig, connect_client

pytestmark = pytest.mark.integration


class _FakeBacklogService:
    """Quacks like BacklogService.analyze so intake stays offline."""

    def __init__(self, specs: list[FeatureSpec]) -> None:
        self._specs = specs

    async def analyze(self, root_id: str) -> BacklogPlan:
        _ = root_id
        return BacklogPlan(specs=self._specs, blocked=False, truncated=False)


async def _temporal_client_or_skip() -> Client:
    try:
        return await asyncio.wait_for(connect_client(TemporalConfig.from_env()), timeout=5)
    except (TimeoutError, OSError, RuntimeError) as exc:  # pragma: no cover - env-gated
        pytest.skip(f"no reachable Temporal server: {exc}")


async def _wait_for_pending_approval(
    factory: async_sessionmaker[Any], approval_id: str, *, timeout: float = 20.0
) -> None:
    """Poll Postgres until the gate's ApprovalRequest row exists and is pending."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with factory() as s:
            repo = ApprovalRequestRepo(s)
            row = await repo.get(approval_id)
        if row is not None and row.state.value == "pending":
            return
        await asyncio.sleep(0.25)
    pytest.fail(f"approval {approval_id} never reached pending within {timeout}s")


async def _audit_actions(factory: async_sessionmaker[Any], trace_id: str) -> list[str]:
    async with factory() as s:
        rows = await s.execute(
            text("SELECT action FROM audit_log WHERE trace_id = :t ORDER BY timestamp"),
            {"t": trace_id},
        )
        return [r[0] for r in rows.all()]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_sdlc_e2e_marches_through_both_gates(_migrated_database: str, tmp_path: Path) -> None:
    """A real worker carries the workflow A→Z: gate 1 approve, gate 2
    modify_input → stub merge, with real approval rows and an audit trail
    in Postgres."""
    client = await _temporal_client_or_skip()

    engine = create_async_engine(_migrated_database, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sdlc_id = uuid.uuid4().hex[:12]
    task_queue = f"sdlc-it-{sdlc_id}"

    specs = [FeatureSpec(intent_id="i1", title="Spec A"), FeatureSpec(intent_id="i2", title="Spec B")]

    # The fake quacks like BacklogService for the one method intake calls; route
    # it through Any so the injection point stays offline without a real service.
    def _build_fake(**_: Any) -> _FakeBacklogService:
        return _FakeBacklogService(specs)

    service_builder: Any = _build_fake
    deps = SDLCDeps(
        session_factory=factory,
        workspace=WorkspaceManager(root=tmp_path / "ws"),
        service_builder=service_builder,
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[SDLCWorkflow, FeatureImplementationWorkflow],
        activities=sdlc_activity_methods(SDLCActivities(deps)),
    )

    try:
        async with worker:
            handle = await client.start_workflow(
                SDLCWorkflow.run,
                SDLCWorkflowInput(
                    sdlc_id=sdlc_id,
                    source_uri="confluence://123",
                    trace_id=sdlc_id,
                    dry_run_jira=True,
                ),
                id=f"task-{sdlc_id}",
                task_queue=task_queue,
            )

            # Gate 1: wait for the real pending row, then decide via the
            # task-{sdlc_id} handle exactly as the REST approval API would.
            await _wait_for_pending_approval(factory, f"sdlc-{sdlc_id}-0")
            await client.get_workflow_handle(f"task-{sdlc_id}").signal("approve")

            # Gate 2: approve the merge (modify_input also counts as approval).
            await _wait_for_pending_approval(factory, f"sdlc-{sdlc_id}-1")
            await client.get_workflow_handle(f"task-{sdlc_id}").signal(
                "modify_input", {"release_notes": "v1 ships"}
            )

            result = await asyncio.wait_for(handle.result(), timeout=60)
    finally:
        await engine.dispose()

    assert result.terminated is False
    assert result.issue_keys == ["SDLC-1", "SDLC-2"]
    assert len(result.feature_results) == 2
    assert result.stage_outcomes["merge"]["verdict"] == "pass"
    assert result.gate_decisions[1]["action"] == "modify_input"

    # Both gates left decidable rows in Postgres, now consumed.
    async with factory() as s:
        repo = ApprovalRequestRepo(s)
        gate0 = await repo.get(f"sdlc-{sdlc_id}-0")
        gate1 = await repo.get(f"sdlc-{sdlc_id}-1")
    assert gate0 is not None and gate0.before_node_id == "intents"
    assert gate1 is not None and gate1.before_node_id == "merge"

    # The full stage trail landed in the audit log, ending at the merge.
    actions = await _audit_actions(factory, sdlc_id)
    assert "sdlc_prs_merged" in actions
    assert actions.count("feature_workspace_created") == 2
    assert actions.count("feature_pr_opened") == 2
