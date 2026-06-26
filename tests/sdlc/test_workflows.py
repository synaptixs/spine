"""Unit tests for SDLCWorkflow + FeatureImplementationWorkflow.

Uses ``WorkflowEnvironment.start_time_skipping()`` (in-process, no docker).
Stub activities replace the real side-effecting ones against the same names
the workflows call, so the orchestration logic runs unchanged while the
intake / git / merge work is faked. Both human gates are driven by signals.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.sdlc.types import FeatureWorkflowInput, SDLCWorkflowInput
from orchestrator.sdlc.workflows import FeatureImplementationWorkflow, SDLCWorkflow

# Audit actions recorded by the stub, in order, for assertions.
_AUDIT_ACTIONS: list[str] = []
# Approval requests the gate raised (captured payloads), for assertions.
_APPROVALS_RAISED: list[dict[str, Any]] = []
# Specs that reached create_jira_issues (post gate-1 patching), for assertions.
_CREATE_JIRA_SPECS: list[dict[str, Any]] = []
# Per-test toggles for the child-failure compensation test.
_CLEANUP_CALLS: list[str] = []
_IMPLEMENT_SHOULD_FAIL = {"value": False}
# Verdict-gating toggles (reset by the fixture):
#   _RUN_TESTS_FAIL_UNTIL: per-path number of initial failing runs before pass
#   _REVIEW_BLOCK:         make the review stub raise a BLOCKER
#   _INTEGRATION_PASS:     parent integration-check verdict
_RUN_TESTS_FAIL_UNTIL = {"value": 0}
_RUN_TESTS_CALLS: dict[str, int] = {}
_REVIEW_BLOCK = {"value": False}  # always block (every review call)
_REVIEW_BLOCK_UNTIL = {"value": 0}  # block the first N review calls, then clear
_REVIEW_CALLS = {"value": 0}
_INTEGRATION_PASS = {"value": True}


@activity.defn(name="sdlc_record_audit")
async def _stub_record_audit(request: dict[str, Any]) -> None:
    _AUDIT_ACTIONS.append(str(request["action"]))


@activity.defn(name="sdlc_raise_approval_request")
async def _stub_raise_approval_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Capture the gate's approval payload and echo a pending-shaped row; real
    persistence is the approvals integration suite's job, not this unit test."""
    _APPROVALS_RAISED.append(dict(payload))
    return {
        "id": payload["approval_id"],
        "task_id": payload["task_id"],
        "before_node_id": payload["before_node_id"],
        "state": "pending",
    }


@activity.defn(name="sdlc_intake_analyze")
async def _stub_intake_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {
        "intent_count": 2,
        "gap_count": 0,
        "blocked": False,
        "truncated": False,
        "specs": [{"title": "Spec A"}, {"title": "Spec B"}],
    }


# Capability plan the profiling stub returns; tests override _PLAN["value"].
_PLAN: dict[str, Any] = {"value": {"skills": [], "mcp_servers": [], "workflow_params": {}, "items": []}}


@activity.defn(name="sdlc_profile_and_plan")
async def _stub_profile_and_plan(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {"profile": {"languages": [], "task_type": "feature"}, "plan": _PLAN["value"]}


@activity.defn(name="sdlc_create_jira_issues")
async def _stub_create_jira_issues(payload: dict[str, Any]) -> dict[str, Any]:
    specs = list(payload.get("specs") or [])
    _CREATE_JIRA_SPECS.clear()
    _CREATE_JIRA_SPECS.extend(specs)
    return {"issue_plans": [{"issue_key": f"SDLC-{i + 1}", "spec": spec} for i, spec in enumerate(specs)]}


@activity.defn(name="sdlc_integration_test")
async def _stub_integration_test(payload: dict[str, Any]) -> dict[str, Any]:
    passed = _INTEGRATION_PASS["value"]
    return {
        "verdict": "pass" if passed else "fail",
        "summary": "stub",
        "issue_keys": list(payload.get("issue_keys") or []),
    }


@activity.defn(name="sdlc_preflight")
async def _stub_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {"passed": True, "output": "stub preflight"}


@activity.defn(name="sdlc_escalation_check")
async def _stub_escalation_check(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {"escalate": False, "reasons": [], "blast_radius": 0, "radius_symbol": ""}


@activity.defn(name="sdlc_merge_prs")
async def _stub_merge_prs(payload: dict[str, Any]) -> dict[str, Any]:
    urls = [str(u) for u in (payload.get("pr_urls") or [])]
    return {"verdict": "pass", "merges": [{"url": u, "merged": True} for u in urls]}


@activity.defn(name="sdlc_consolidate_memory")
async def _stub_consolidate_memory(payload: dict[str, Any]) -> dict[str, Any]:
    # Echo back the episode count so tests can assert blocks reached the hook.
    return {"skipped": True, "received_blocks": len(payload.get("policy_blocks") or [])}


@activity.defn(name="sdlc_register_units")
async def _stub_register_units(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {"skipped": True}


# ---- child stage stubs ---------------------------------------------------


@activity.defn(name="sdlc_create_workspace")
async def _stub_create_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    return {"path": f"/tmp/ws/{payload['issue_key']}"}


@activity.defn(name="sdlc_code_plan")
async def _stub_code_plan(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    return {"steps": ["implement", "author tests"]}


# Capture the skills/mcp_servers each implement call received (plan threading).
_IMPLEMENT_PAYLOADS: list[dict[str, Any]] = []
# In-loop approval (Bet 2c-i): number of require_approval pauses to emit before
# the implement completes; the decisions the resume stub received, in order.
_IMPLEMENT_PAUSES = {"value": 0}
_RESUME_DECISIONS: list[dict[str, Any]] = []


def _pause(index: int) -> dict[str, Any]:
    return {
        "status": "needs_approval",
        "checkpoint": {"marker": f"cp{index}"},
        "pending": {"tool": "deploy", "reason": "policy requires approval"},
        "prior_written": [],
        "prior_summary": "",
    }


@activity.defn(name="sdlc_implement")
async def _stub_implement(payload: dict[str, Any]) -> dict[str, Any]:
    _IMPLEMENT_PAYLOADS.append(dict(payload))
    if _IMPLEMENT_SHOULD_FAIL["value"]:
        raise ApplicationError("implement blew up", non_retryable=True)
    if _IMPLEMENT_PAUSES["value"] > 0:
        return _pause(0)
    return {"status": "complete", "files": [f"{payload['path']}/generated.py"], "summary": "stub"}


@activity.defn(name="sdlc_implement_resume")
async def _stub_implement_resume(payload: dict[str, Any]) -> dict[str, Any]:
    _RESUME_DECISIONS.append(dict(payload.get("decision") or {}))
    _IMPLEMENT_PAUSES["value"] -= 1
    if _IMPLEMENT_PAUSES["value"] > 0:
        return _pause(_IMPLEMENT_PAUSES["value"])
    return {"status": "complete", "files": [f"{payload['path']}/generated.py"], "summary": "resumed"}


@activity.defn(name="sdlc_test_author")
async def _stub_test_author(payload: dict[str, Any]) -> dict[str, Any]:
    return {"files": [f"{payload['path']}/test_generated.py"], "summary": "stub"}


@activity.defn(name="sdlc_refine")
async def _stub_refine(payload: dict[str, Any]) -> dict[str, Any]:
    return {"files": [f"{payload['path']}/generated.py"], "summary": "refined"}


@activity.defn(name="sdlc_run_tests")
async def _stub_run_tests(payload: dict[str, Any]) -> dict[str, Any]:
    """Pass unless this path is scripted to fail its first N runs."""
    path = str(payload["path"])
    n = _RUN_TESTS_CALLS.get(path, 0)
    _RUN_TESTS_CALLS[path] = n + 1
    passed = n >= _RUN_TESTS_FAIL_UNTIL["value"]
    return {
        "passed": passed,
        "returncode": 0 if passed else 1,
        "output": "stub pass" if passed else "stub fail",
    }


@activity.defn(name="sdlc_review")
async def _stub_review(payload: dict[str, Any]) -> dict[str, Any]:
    _ = payload
    _REVIEW_CALLS["value"] += 1
    block = _REVIEW_BLOCK["value"] or _REVIEW_CALLS["value"] <= _REVIEW_BLOCK_UNTIL["value"]
    return {
        "verdict": "request_changes" if block else "comment",
        "blockers": ["stub blocker"] if block else [],
        "summary": "stub",
        "has_blocker": block,
    }


@activity.defn(name="sdlc_open_pr")
async def _stub_open_pr(payload: dict[str, Any]) -> dict[str, Any]:
    return {"pr_url": f"https://stub.github/pr/{payload['issue_key']}", "number": 1}


@activity.defn(name="sdlc_cleanup_workspace")
async def _stub_cleanup_workspace(payload: dict[str, Any]) -> None:
    _CLEANUP_CALLS.append(str(payload["path"]))


_ACTIVITIES = [
    _stub_record_audit,
    _stub_raise_approval_request,
    _stub_intake_analyze,
    _stub_profile_and_plan,
    _stub_create_jira_issues,
    _stub_integration_test,
    _stub_merge_prs,
    _stub_consolidate_memory,
    _stub_register_units,
    _stub_create_workspace,
    _stub_code_plan,
    _stub_implement,
    _stub_implement_resume,
    _stub_test_author,
    _stub_refine,
    _stub_run_tests,
    _stub_preflight,
    _stub_review,
    _stub_escalation_check,
    _stub_open_pr,
    _stub_cleanup_workspace,
]


@pytest.fixture
def reset_audit() -> None:
    _AUDIT_ACTIONS.clear()
    _APPROVALS_RAISED.clear()
    _CREATE_JIRA_SPECS.clear()
    _CLEANUP_CALLS.clear()
    _IMPLEMENT_SHOULD_FAIL["value"] = False
    _RUN_TESTS_FAIL_UNTIL["value"] = 0
    _RUN_TESTS_CALLS.clear()
    _REVIEW_BLOCK["value"] = False
    _REVIEW_BLOCK_UNTIL["value"] = 0
    _REVIEW_CALLS["value"] = 0
    _INTEGRATION_PASS["value"] = True
    _PLAN["value"] = {"skills": [], "mcp_servers": [], "workflow_params": {}, "items": []}
    _IMPLEMENT_PAYLOADS.clear()
    _IMPLEMENT_PAUSES["value"] = 0
    _RESUME_DECISIONS.clear()


def _worker(client: Client) -> Worker:
    return Worker(
        client,
        task_queue="sdlc-test-q",
        workflows=[SDLCWorkflow, FeatureImplementationWorkflow],
        activities=_ACTIVITIES,
    )


async def test_happy_path_through_both_gates(reset_audit: None) -> None:
    """approve at gate 1, modify_input at gate 2 → reaches the merge,
    fans out 2 children, records the expected audit trail."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s1", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        # Both decisions queue; gate 1 consumes index 0, gate 2 consumes index 1.
        await handle.signal("approve")
        await handle.signal("modify_input", {"release_notes": "v1 ships"})
        result = await handle.result()

    assert result.terminated is False
    assert result.issue_keys == ["SDLC-1", "SDLC-2"]
    assert len(result.feature_results) == 2
    assert len(result.gate_decisions) == 2
    assert result.gate_decisions[1]["action"] == "modify_input"
    # Both gates raised a real (decidable) approval request, with the right
    # before_node + risk so the REST API can list and decide them.
    assert [a["before_node_id"] for a in _APPROVALS_RAISED] == ["intents", "merge"]
    assert [a["risk_classification"] for a in _APPROVALS_RAISED] == ["medium", "high"]
    assert [a["approval_id"] for a in _APPROVALS_RAISED] == ["sdlc-s1-0", "sdlc-s1-1"]
    # The full stage trail landed, ending at the merge.
    assert "sdlc_prs_merged" in _AUDIT_ACTIONS
    assert result.stage_outcomes["merge"]["verdict"] == "pass"
    # Each child emitted its two audit rows (workspace + PR) × 2 issues.
    assert _AUDIT_ACTIONS.count("feature_workspace_created") == 2
    assert _AUDIT_ACTIONS.count("feature_pr_opened") == 2


async def test_capability_plan_surfaced_at_gate_and_applied(reset_audit: None) -> None:
    """The plan is computed before gate 1, shown in the gate description, audited,
    and its workflow_params override the run (a migration-style fan-out)."""
    _ = reset_audit
    _PLAN["value"] = {
        "skills": ["python-conventions"],
        "mcp_servers": ["db"],
        "workflow_params": {"max_parallel_features": 1, "max_review_iterations": 3},
        "items": [
            {"capability_id": "python-conventions", "kind": "skill", "rationale": "python, feature task"},
            {"capability_id": "db-schema-mcp", "kind": "mcp_server", "rationale": "database present"},
        ],
    }
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="sP", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        await handle.signal("approve")  # gate 1
        await handle.signal("approve")  # gate 2
        result = await handle.result()

    assert result.terminated is False
    # Plan computed + audited, and the selected MCP server recorded for governance.
    assert "sdlc_capability_plan" in _AUDIT_ACTIONS
    assert "sdlc_mcp_selected" in _AUDIT_ACTIONS
    assert result.stage_outcomes["capability_plan"]["skills"] == ["python-conventions"]
    # The intent gate's description carried the toolkit so the human signs off on it.
    intents_gate = next(a for a in _APPROVALS_RAISED if a["before_node_id"] == "intents")
    assert "Capability plan" in intents_gate["description"]
    assert "python-conventions" in intents_gate["description"]


async def test_capability_plan_threads_skills_into_implement(reset_audit: None) -> None:
    """The run's selected skills + MCP servers reach every feature's implement call."""
    _ = reset_audit
    _PLAN["value"] = {
        "skills": ["python-conventions", "repo-pkg-grounding"],
        "mcp_servers": ["db"],
        "workflow_params": {},
        "items": [],
    }
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="sT", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        await handle.signal("approve")  # gate 1
        await handle.signal("approve")  # gate 2
        await handle.result()

    assert _IMPLEMENT_PAYLOADS  # at least one feature ran
    for payload in _IMPLEMENT_PAYLOADS:
        assert payload["skills"] == ["python-conventions", "repo-pkg-grounding"]
        assert payload["mcp_servers"] == ["db"]


async def test_capability_plan_editable_via_modify_input(reset_audit: None) -> None:
    """A modify_input patch at gate 1 can override the assembled plan."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="sE", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        edited = {
            "skills": [],
            "mcp_servers": [],
            "workflow_params": {"max_parallel_features": 1},
            "items": [],
        }
        await handle.signal("modify_input", {"capability_plan": edited})  # gate 1
        await handle.signal("approve")  # gate 2
        result = await handle.result()

    assert result.terminated is False
    assert "sdlc_capability_plan_edited" in _AUDIT_ACTIONS


async def test_deny_at_gate1_terminates_early(reset_audit: None) -> None:
    """deny at gate 1 ends the run before any issue is created or merged."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s2", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        await handle.signal("deny")
        result = await handle.result()

    assert result.terminated is True
    assert result.termination_reason == "intents_denied"
    assert result.issue_keys == []
    assert "sdlc_intents_denied" in _AUDIT_ACTIONS
    # No merge / fan-out audit rows when the gate denies.
    assert "sdlc_prs_merged" not in _AUDIT_ACTIONS
    assert "feature_workspace_created" not in _AUDIT_ACTIONS


async def test_cancel_at_gate1_terminates(reset_audit: None) -> None:
    """A `cancel` signal wakes the waiting gate and terminates the run."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s4", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        await handle.signal("cancel")
        result = await handle.result()

    assert result.terminated is True
    assert result.termination_reason == "cancelled"
    assert result.issue_keys == []
    assert "sdlc_cancelled" in _AUDIT_ACTIONS
    assert "feature_workspace_created" not in _AUDIT_ACTIONS


async def test_auto_approve_intent_skips_gate1_signal(reset_audit: None) -> None:
    """The test hook clears gate 1 without a signal; gate 2 still needs one."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s3", source_uri="confluence://123", auto_approve_intent=True),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        # Only gate 2 needs a signal now (gate 1 auto-approves).
        await handle.signal("approve")
        result = await handle.result()

    assert result.terminated is False
    assert result.stage_outcomes["merge"]["verdict"] == "pass"
    assert result.gate_decisions[0]["action"] == "approve"


async def test_child_cleans_up_worktree_when_a_stage_fails(reset_audit: None) -> None:
    """When `implement` exhausts its retries, the child degrades to a FAILED
    verdict (the parent must fan in and stop at features_failed, not crash)
    and still tears down its worktree (finally-block compensation)."""
    _ = reset_audit
    _IMPLEMENT_SHOULD_FAIL["value"] = True
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s5", issue_key="SDLC-1", spec={}),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "failed"
    assert "stage error" in result.detail
    assert "feature_stage_failed" in _AUDIT_ACTIONS
    # The worktree the child created was cleaned up despite the failure.
    assert _CLEANUP_CALLS == ["/tmp/ws/SDLC-1"]


async def test_child_refines_then_passes(reset_audit: None) -> None:
    """First test run fails, the loop refines once, the second run passes →
    verdict ``passed``, a PR opens, and ``iterations`` counts both runs."""
    _ = reset_audit
    _RUN_TESTS_FAIL_UNTIL["value"] = 1  # first run red, then green
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s6", issue_key="SDLC-1", spec={}),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "passed"
    assert result.iterations == 2
    assert result.pr_url == "https://stub.github/pr/SDLC-1"
    # One refinement cycle ran between the two test runs.
    assert _AUDIT_ACTIONS.count("feature_refined") == 1
    assert "feature_pr_opened" in _AUDIT_ACTIONS
    assert _CLEANUP_CALLS == ["/tmp/ws/SDLC-1"]


async def test_child_pauses_in_loop_for_approval_then_resumes(reset_audit: None) -> None:
    """The agentic implement hits two ``require_approval`` gates mid-run (Bet
    2c-i). The child raises a real approval per pause, waits for the human's
    signal, and resumes the loop with the decision — finishing ``passed``."""
    _ = reset_audit
    _IMPLEMENT_PAUSES["value"] = 2
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s9", issue_key="SDLC-1", spec={}),
            # ``task-`` prefix so the in-loop gate's REST signal would route here.
            id="task-feat-s9-SDLC-1",
            task_queue="sdlc-test-q",
        )
        # Two buffered decisions, consumed by the two in-loop gates in order.
        await handle.signal("approve")
        await handle.signal("modify_input", {"env": "staging"})
        result = await handle.result()

    assert result.verdict == "passed"
    assert result.pr_url == "https://stub.github/pr/SDLC-1"
    # Two resumes, carrying the two decisions in order.
    assert len(_RESUME_DECISIONS) == 2
    assert _RESUME_DECISIONS[0]["action"] == "approve"
    assert _RESUME_DECISIONS[1] == {"action": "modify_input", "modified_input": {"env": "staging"}}
    # Two real approval rows raised for the in-loop gates, deterministically id'd.
    impl_approvals = [a for a in _APPROVALS_RAISED if str(a["before_node_id"]).startswith("implement:")]
    assert [a["approval_id"] for a in impl_approvals] == ["feat-s9-SDLC-1-impl-0", "feat-s9-SDLC-1-impl-1"]
    assert all(a["task_id"] == "feat-s9-SDLC-1" for a in impl_approvals)
    assert _AUDIT_ACTIONS.count("feature_impl_approval_decided") == 2


async def test_child_in_loop_approval_times_out_rejects_and_continues(reset_audit: None) -> None:
    """No decision signal arrives: the gate times out and resolves as a reject
    (decision policy), and the feature still completes rather than stranding."""
    _ = reset_audit
    _IMPLEMENT_PAUSES["value"] = 1
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s10", issue_key="SDLC-1", spec={}),
            id="task-feat-s10-SDLC-1",
            task_queue="sdlc-test-q",
        )
        # No signal — the time-skipping env fast-forwards past the 24h wait.
        result = await handle.result()

    assert result.verdict == "passed"
    assert len(_RESUME_DECISIONS) == 1
    assert _RESUME_DECISIONS[0]["action"] == "reject"  # timeout → reject-and-continue
    assert "feature_impl_approval_timeout" in _AUDIT_ACTIONS


async def test_child_tests_stay_red_escalates_without_pr(reset_audit: None) -> None:
    """Tests never go green within the cap → verdict ``failed``, no PR, but the
    worktree is still cleaned up."""
    _ = reset_audit
    _RUN_TESTS_FAIL_UNTIL["value"] = 99  # always red
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s7", issue_key="SDLC-1", spec={}, max_refine_iterations=3),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "failed"
    assert result.pr_url is None
    assert result.iterations == 3  # ran the full cap
    assert "feature_tests_failed" in _AUDIT_ACTIONS
    assert "feature_pr_opened" not in _AUDIT_ACTIONS
    assert _CLEANUP_CALLS == ["/tmp/ws/SDLC-1"]


async def test_child_review_blocker_persists_escalates_without_pr(reset_audit: None) -> None:
    """A review BLOCKER that never clears (even after the feedback rounds) →
    verdict ``changes_requested``, no PR."""
    _ = reset_audit
    _REVIEW_BLOCK["value"] = True  # blocks on every review call
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s8", issue_key="SDLC-1", spec={}, max_review_iterations=2),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "changes_requested"
    assert result.pr_url is None
    # It tried: two review-feedback rounds before giving up.
    assert _AUDIT_ACTIONS.count("feature_review_refined") == 2
    assert "feature_review_blocked" in _AUDIT_ACTIONS
    assert "feature_pr_opened" not in _AUDIT_ACTIONS
    assert _CLEANUP_CALLS == ["/tmp/ws/SDLC-1"]


async def test_child_responds_to_review_feedback_then_passes(reset_audit: None) -> None:
    """The review blocks once; the child addresses the feedback, the re-review
    clears, and the feature PASSES with a PR — the engineer-like loop."""
    _ = reset_audit
    _REVIEW_BLOCK_UNTIL["value"] = 1  # first review blocks, second clears
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s11", issue_key="SDLC-1", spec={}, max_review_iterations=2),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "passed"
    assert result.pr_url == "https://stub.github/pr/SDLC-1"
    # Exactly one review-feedback round ran, then the re-review cleared.
    assert _AUDIT_ACTIONS.count("feature_review_refined") == 1
    assert "feature_review_blocked" not in _AUDIT_ACTIONS
    assert "feature_pr_opened" in _AUDIT_ACTIONS
    assert _CLEANUP_CALLS == ["/tmp/ws/SDLC-1"]


async def test_max_review_iterations_zero_is_old_behavior(reset_audit: None) -> None:
    """max_review_iterations=0 restores the old behavior: a blocker ends the
    feature immediately with no feedback round."""
    _ = reset_audit
    _REVIEW_BLOCK_UNTIL["value"] = 1  # would clear on a second review
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            FeatureImplementationWorkflow.run,
            FeatureWorkflowInput(sdlc_id="s12", issue_key="SDLC-1", spec={}, max_review_iterations=0),
            id=f"feat-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.verdict == "changes_requested"
    assert _AUDIT_ACTIONS.count("feature_review_refined") == 0  # no feedback round
    assert "feature_review_blocked" in _AUDIT_ACTIONS


async def test_parent_terminates_when_a_feature_fails(reset_audit: None) -> None:
    """If any child returns a non-``passed`` verdict, the parent stops before
    integration/merge with reason ``features_failed``."""
    _ = reset_audit
    _REVIEW_BLOCK["value"] = True  # every child escalates with changes_requested
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s9", source_uri="confluence://123", auto_approve_intent=True),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.terminated is True
    assert result.termination_reason == "features_failed"
    assert all(fr["verdict"] != "passed" for fr in result.feature_results)
    assert "sdlc_features_failed" in _AUDIT_ACTIONS
    # We never reached integration / merge.
    assert "sdlc_integration_tested" not in _AUDIT_ACTIONS


async def test_parent_terminates_on_integration_failure(reset_audit: None) -> None:
    """A failing integration check stops the run before the merge gate."""
    _ = reset_audit
    _INTEGRATION_PASS["value"] = False
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s10", source_uri="confluence://123", auto_approve_intent=True),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        result = await handle.result()

    assert result.terminated is True
    assert result.termination_reason == "integration_failed"
    assert "sdlc_integration_failed" in _AUDIT_ACTIONS
    assert "sdlc_prs_merged" not in _AUDIT_ACTIONS


async def test_parent_caps_features_when_max_features_set(reset_audit: None) -> None:
    """max_features=1 keeps only the first issue plan (audited) — intake
    variance can't fan out past what the operator approved."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(
                sdlc_id="s-cap",
                source_uri="confluence://1",
                auto_approve_intent=True,
                max_features=1,
            ),
            id=f"task-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        # Only gate 2 needs a signal (gate 1 auto-approves).
        await handle.signal("approve")
        result = await handle.result()

    assert result.issue_keys == ["SDLC-1"]
    assert len(result.feature_results) == 1
    assert "sdlc_features_capped" in _AUDIT_ACTIONS


async def test_gate1_clarifications_fold_into_specs(reset_audit: None) -> None:
    """A modify_input `clarifications` patch at gate 1 is folded into every
    spec's technical_notes before fan-out, so codegen builds against the
    approver's answers — and the decision is audited."""
    _ = reset_audit
    async with await WorkflowEnvironment.start_time_skipping() as env, _worker(env.client):
        handle = await env.client.start_workflow(
            SDLCWorkflow.run,
            SDLCWorkflowInput(sdlc_id="s13", source_uri="confluence://123"),
            id=f"sdlc-{uuid.uuid4().hex}",
            task_queue="sdlc-test-q",
        )
        # Gate 1: answer the open questions; gate 2: approve the merge.
        await handle.signal("modify_input", {"clarifications": "Use OAuth2 via Auth0."})
        await handle.signal("approve")
        result = await handle.result()

    assert result.terminated is False
    assert "sdlc_clarifications_applied" in _AUDIT_ACTIONS
    # Every spec that reached create_jira carries the clarification.
    assert _CREATE_JIRA_SPECS, "create_jira received no specs"
    for spec in _CREATE_JIRA_SPECS:
        assert "Use OAuth2 via Auth0." in spec.get("technical_notes", "")


def test_intents_gate_description_lists_open_questions() -> None:
    from orchestrator.sdlc.workflows import _intents_gate_description

    desc = _intents_gate_description(3, ["Which DB?", "Sync or async?"])
    assert "3 intent(s)" in desc
    assert "Which DB?" in desc and "Sync or async?" in desc
    assert "clarifications" in desc
    # No questions → plain approval prompt, no clarifications ask.
    assert _intents_gate_description(2, []) == "Gate 0: approve 2 intent(s)"


def test_coerce_clarifications_normalizes_shapes() -> None:
    from orchestrator.sdlc.workflows import _coerce_clarifications

    assert _coerce_clarifications("one answer") == ["one answer"]
    assert _coerce_clarifications(["a", " b ", ""]) == ["a", "b"]
    assert _coerce_clarifications(None) == []
    assert _coerce_clarifications(42) == []


def test_apply_clarifications_appends_to_technical_notes() -> None:
    from orchestrator.sdlc.workflows import _apply_clarifications

    spec = {"title": "X", "technical_notes": "Existing note."}
    out = _apply_clarifications(spec, ["Use OAuth2", "Page size 50"])
    assert out["technical_notes"].startswith("Existing note.")
    assert "Use OAuth2" in out["technical_notes"]
    assert "Page size 50" in out["technical_notes"]
    assert spec["technical_notes"] == "Existing note."  # original not mutated

    fresh = _apply_clarifications({"title": "Y"}, ["Only answer"])
    assert "Only answer" in fresh["technical_notes"]
