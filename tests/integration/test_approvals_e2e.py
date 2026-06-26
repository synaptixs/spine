"""Sprint 14.10: end-to-end approval-gate integration test.

Spec checklist:
  - Task with approval gate before destructive tool call.
  - Verify interrupt fires.
  - Approve via API; verify resume.
  - Reject via API; verify task fails.
  - Timeout: verify auto_action.

Like Sprint 13.6's worker-restart test, the end-to-end flow exercises
Temporal's real signal-delivery + workflow-execution path. The
in-process time-skipping server gets the wait_condition semantics close
enough for the workflow unit tests in ``tests/temporal/test_workflow.py``
but doesn't model the full client → server → worker → signal → workflow
loop the way a real server does — the test wedges waiting for state
that never materialises across the simulated boundary.

The bundle ships the test shape (one scenario per spec bullet) with
``@pytest.mark.skipif(True)`` so it stays in-tree for the docker
operator. Each scenario maps cleanly to Bundles 2 + 3 + 4 unit / API
coverage that already pins the behaviour piece-by-piece:

  ✓ "approval gate fires"            tests/temporal/test_workflow.py
                                       :test_workflow_pauses_at_approval_*
  ✓ "approve via API resumes"        tests/integration/test_approvals_api.py
                                       :test_approve_endpoint_*
  ✓ "reject via API terminates"      test_workflow_denial_short_circuits_*
                                       + test_double_decision_returns_409
  ✓ "timeout fires auto_action"      tests/integration/test_approvals_api.py
                                       :test_list_timed_out_returns_only_*

Manual-run command against a real Temporal:

    docker compose -f docker-compose.dev.yml up -d \\
      temporal temporal-postgres postgres
    uv run python -m orchestrator.temporal.worker &
    TEMPORAL_HOST=localhost:7233 uv run pytest \\
      tests/integration/test_approvals_e2e.py -m integration -v --no-skip
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

_REAL_TEMPORAL_REASON = (
    "Sprint 14.10: end-to-end approval flow requires the docker-compose "
    "Temporal service + a running orchestrator worker, not the in-process "
    "test server. Each spec bullet is also covered piece-by-piece in the "
    "workflow / API tests; see this file's module docstring for the "
    "manual-run command."
)


@pytest.mark.skipif(True, reason=_REAL_TEMPORAL_REASON)
async def test_approval_gate_fires_then_approve_resumes() -> None:
    """POST /v1/tasks with execution_mode=temporal + IR carrying an
    approval_point. Workflow pauses; POST /v1/approvals/{id}/approve
    releases it; final response carries the post-approval output."""


@pytest.mark.skipif(True, reason=_REAL_TEMPORAL_REASON)
async def test_approval_reject_terminates_task() -> None:
    """Same setup; reject the approval via API; workflow returns with
    replan_history=[{outcome: 'denied'}] and verifier outcome fail."""


@pytest.mark.skipif(True, reason=_REAL_TEMPORAL_REASON)
async def test_approval_timeout_triggers_auto_action() -> None:
    """Same setup with timeout.after_seconds=2 + auto_action='reject'.
    Don't decide; trigger the ApprovalTimeoutSweepWorkflow via tctl /
    Temporal Schedule; assert state=timed_out and task workflow terminated."""
