"""Unit tests for OrchestratorWorkflow.

Uses ``WorkflowEnvironment.from_local()`` which spins up a time-skipping
test server in-process — no docker required. We register stub activity
implementations against the same names the workflow calls; the workflow
logic itself runs unchanged.

These are the lightweight workflow tests. The end-to-end integration
test against a real Temporal docker-compose lands in Bundle 5 (Sprint 13.6
worker-restart scenario).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.temporal.workflow import OrchestratorWorkflow, TaskWorkflowInput


def _stub_ir(template_id: str = "agent.x") -> dict[str, Any]:
    """Minimal IR dump the workflow understands."""
    return {
        "metadata": {"id": "plan.single_agent", "version": "0.1.0", "description": "x"},
        "spec": {
            "objective": "x",
            "workflow_pattern": "single_agent",
            "task_glossary": {},
            "nodes": [
                {
                    "id": "n_agent",
                    "type": "agent",
                    "template_id": template_id,
                    "template_version": "0.1.0",
                    "config": {},
                }
            ],
            "edges": [],
            "approval_points": [],
            "budget": {"max_replan_count": 0},
            "constraints": {},
        },
    }


def _stub_pass_result(*, with_replan: bool = False) -> dict[str, Any]:
    return {
        "final_state": {},
        "node_outputs": {
            "agent": {"confidence": 0.9, "findings": "ok"},
            "verify": {"outcome": "pass", "failures": []},
            "chain_agent": {
                "dispatch": {"next_step": "replan" if with_replan else "continue"},
                **(
                    {
                        "replan_request": {
                            "failing_node": "agent",
                            "verifier_id": "chain_agent",
                            "outcome": "fail",
                            "rationale": "test",
                            "failures": [],
                        }
                    }
                    if with_replan
                    else {}
                ),
            },
        },
        "replan_request": (
            {
                "failing_node": "agent",
                "verifier_id": "chain_agent",
                "outcome": "fail",
                "rationale": "test",
                "failures": [],
            }
            if with_replan
            else None
        ),
        "templates": [{"id": "agent.x", "version": "0.1.0"}],
    }


# Module-level activity stubs. They close over a small registry dict that
# tests mutate to set the canned response for each test case.
_RESPONSES: dict[str, list[Any]] = {}


def _take(key: str) -> Any:
    """Pop the next canned response for an activity, or fall back to the last."""
    queue = _RESPONSES.get(key) or []
    if not queue:
        raise RuntimeError(f"no canned response registered for activity {key!r}")
    if len(queue) == 1:
        return queue[0]
    return queue.pop(0)


@activity.defn(name="plan_initial_ir")
async def _stub_plan_initial_ir(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    result: dict[str, Any] = _take("plan_initial_ir")
    return result


@activity.defn(name="validate_ir")
async def _stub_validate_ir(ir_dump: dict[str, Any]) -> dict[str, Any]:
    _ = ir_dump
    result: dict[str, Any] = _take("validate_ir")
    return result


@activity.defn(name="execute_graph_pass")
async def _stub_execute_graph_pass(request: dict[str, Any]) -> dict[str, Any]:
    _ = request
    result: dict[str, Any] = _take("execute_graph_pass")
    return result


@activity.defn(name="replan_ir")
async def _stub_replan_ir(request: dict[str, Any]) -> dict[str, Any]:
    _ = request
    result: dict[str, Any] = _take("replan_ir")
    return result


@activity.defn(name="record_audit")
async def _stub_record_audit(request: dict[str, Any]) -> None:
    _ = request


@activity.defn(name="raise_approval_request")
async def _stub_raise_approval_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Echo back an approval-shaped dict so the workflow can keep moving;
    real persistence is the integration test's job, not this unit test."""
    return {
        "id": payload["approval_id"],
        "task_id": payload["task_id"],
        "before_node_id": payload["before_node_id"],
        "state": "pending",
    }


_ACTIVITIES = [
    _stub_plan_initial_ir,
    _stub_validate_ir,
    _stub_execute_graph_pass,
    _stub_replan_ir,
    _stub_raise_approval_request,
    _stub_record_audit,
]


@pytest.fixture
def reset_responses() -> None:
    _RESPONSES.clear()


async def _run_workflow(client: Client, *, task_id: str) -> Any:
    async with Worker(
        client,
        task_queue="test-q",
        workflows=[OrchestratorWorkflow],
        activities=_ACTIVITIES,
    ):
        return await client.execute_workflow(
            OrchestratorWorkflow.run,
            TaskWorkflowInput(task_id=task_id, objective="test"),
            id=f"wf-{uuid.uuid4().hex}",
            task_queue="test-q",
        )


async def test_workflow_happy_path_returns_terminal_output(reset_responses: None) -> None:
    """No replan needed → workflow returns the first pass's output unchanged."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir()],
        validate_ir=[{"ok": True, "failures": []}],
        execute_graph_pass=[_stub_pass_result(with_replan=False)],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env.client, task_id="t1")
    assert result.replan_count == 0
    assert result.replan_history == []
    assert result.verifier["outcome"] == "pass"
    assert result.output == {"confidence": 0.9, "findings": "ok"}
    assert result.templates == [{"id": "agent.x", "version": "0.1.0"}]


async def test_workflow_replans_once_then_passes(reset_responses: None) -> None:
    """First pass fails → planner replans → second pass passes."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir("agent.x")],
        validate_ir=[
            {"ok": True, "failures": []},  # initial IR
            {"ok": True, "failures": []},  # revised IR
        ],
        execute_graph_pass=[
            _stub_pass_result(with_replan=True),
            _stub_pass_result(with_replan=False),
        ],
        replan_ir=[_stub_ir("agent.y")],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env.client, task_id="t2")
    assert result.replan_count == 1
    assert len(result.replan_history) == 1
    assert result.replan_history[0]["outcome"] == "replanned"


async def test_workflow_exhausts_budget_when_chain_keeps_failing(
    reset_responses: None,
) -> None:
    """Default budget=2; three failing passes → budget_exhausted entry."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir("agent.x")],
        validate_ir=[
            {"ok": True, "failures": []},  # initial
            {"ok": True, "failures": []},  # replan 1
            {"ok": True, "failures": []},  # replan 2
        ],
        execute_graph_pass=[_stub_pass_result(with_replan=True)],
        replan_ir=[_stub_ir("agent.y")],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env.client, task_id="t3")
    assert result.replan_count == 2
    outcomes = [e["outcome"] for e in result.replan_history]
    assert outcomes == ["replanned", "replanned", "budget_exhausted"]


def _stub_ir_with_approval_point(template_id: str = "agent.x") -> dict[str, Any]:
    base = _stub_ir(template_id)
    base["spec"]["approval_points"] = [
        {
            "before_node": "n_agent",
            "description": "Approve destructive run",
            "title": "Approve",
            "action_summary": "Run the agent",
            "risk_classification": "high",
            "affected_resources": [],
            "approver_roles": [],
            "timeout_seconds": None,
            "timeout_auto_action": None,
            "notification_channels": [],
        }
    ]
    return base


async def test_workflow_pauses_at_approval_and_resumes_on_approve(
    reset_responses: None,
) -> None:
    """Workflow waits at the approval gate; a `signal('approve')` releases it
    and execute_graph_pass runs once. The denial branch never fires."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir_with_approval_point()],
        validate_ir=[{"ok": True, "failures": []}],
        execute_graph_pass=[_stub_pass_result(with_replan=False)],
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[OrchestratorWorkflow],
            activities=_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            OrchestratorWorkflow.run,
            TaskWorkflowInput(task_id="ap-1", objective="x"),
            id=f"wf-{uuid.uuid4().hex}",
            task_queue="test-q",
        )
        # Without the signal the workflow would block forever on wait_condition.
        await handle.signal("approve")
        result = await handle.result()
    assert result.verifier["outcome"] == "pass"
    # No approval-related entry leaks into replan_history when the gate clears.
    assert result.replan_history == []


async def test_workflow_denial_short_circuits_and_returns_failure(
    reset_responses: None,
) -> None:
    """`signal('deny')` ends the workflow before execute_graph_pass runs."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir_with_approval_point()],
        validate_ir=[{"ok": True, "failures": []}],
        # execute_graph_pass deliberately omitted — denial must short-circuit.
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[OrchestratorWorkflow],
            activities=_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            OrchestratorWorkflow.run,
            TaskWorkflowInput(task_id="ap-2", objective="x"),
            id=f"wf-{uuid.uuid4().hex}",
            task_queue="test-q",
        )
        await handle.signal("deny")
        result = await handle.result()
    assert result.replan_count == 0
    assert len(result.replan_history) == 1
    assert result.replan_history[0]["outcome"] == "denied"
    assert result.verifier == {"outcome": "fail", "failures": []}


async def test_workflow_records_signal_state_on_status_query(reset_responses: None) -> None:
    """All four signals (cancel/approve/deny/modify_input) wire up cleanly:
    they set state the ``status`` query surfaces. Sprint 14 will use them
    to gate the loop; for now we just pin the wiring."""
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir()],
        validate_ir=[{"ok": True, "failures": []}],
        execute_graph_pass=[_stub_pass_result(with_replan=False)],
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue="test-q", workflows=[OrchestratorWorkflow], activities=_ACTIVITIES),
    ):
        handle = await env.client.start_workflow(
            OrchestratorWorkflow.run,
            TaskWorkflowInput(task_id="sig-1", objective="x"),
            id=f"wf-{uuid.uuid4().hex}",
            task_queue="test-q",
        )
        await handle.signal("approve")
        await handle.signal("modify_input", {"hint": "narrower scope"})
        await handle.result()
        status = await handle.query("status")
        # Decisions accumulate in the queue regardless of workflow consumption;
        # this workflow has no approval gate, so both signals just sit there.
        assert status["cancelled"] is False
        assert len(status["decisions"]) == 2
        assert status["decisions"][0]["action"] == "approve"
        assert status["decisions"][1]["action"] == "modify_input"
        assert status["decisions"][1]["patch"] == {"hint": "narrower scope"}


async def test_workflow_returns_validation_failure_for_initial_ir(
    reset_responses: None,
) -> None:
    _ = reset_responses
    _RESPONSES.update(
        plan_initial_ir=[_stub_ir("agent.x")],
        validate_ir=[{"ok": False, "failures": [{"rule": "x", "message": "bad ir"}]}],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env.client, task_id="t4")
    assert result.ir_validation["ok"] is False
    assert result.replan_history[0]["outcome"] == "ir_validation_failed"
    assert result.replan_count == 0
