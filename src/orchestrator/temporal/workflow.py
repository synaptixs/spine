"""Sprint 13.2: OrchestratorWorkflow — the Temporal-side replan loop.

Mirrors the synchronous ``/v1/tasks`` orchestration loop:

    1. plan_initial_ir (or pinned-template IR)
    2. validate_ir
    3. loop:
         a. execute_graph_pass(ir, replan_count) → final_state + replan_request
         b. break if no replan_request
         c. break if replan_count >= budget (record budget_exhausted)
         d. replan_ir → revised IR
         e. validate_ir; break on failure
         f. record_audit(task_replan); continue
    4. record_audit(task_submit)

Workflow code must be deterministic, so all side effects funnel through
activities (defined in ``orchestrator.temporal.activities``). The workflow
never imports the planner / runtime / DB modules directly — those imports
sit behind the activity surface. The ``with workflow.unsafe.imports_passed_through()``
context makes the unavoidable runtime imports (RetryPolicy, etc.) safe.

A successful workflow returns the same shape ``/v1/tasks`` returns today
(``TaskWorkflowResult``), so the API layer can swap synchronous → workflow
execution without changing its response model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

DEFAULT_MAX_REPLAN_COUNT = 2

# Each activity gets its own retry / timeout policy. LLM-bound activities
# can be slow and benefit from a generous start-to-close; audit writes are
# fast and tolerate aggressive timeouts.
_PLAN_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))
_GRAPH_RETRY = RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=2))
_AUDIT_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(milliseconds=200))

_PLAN_TIMEOUT = timedelta(minutes=2)
_GRAPH_TIMEOUT = timedelta(minutes=10)
_VALIDATE_TIMEOUT = timedelta(seconds=30)
_AUDIT_TIMEOUT = timedelta(seconds=10)
_APPROVAL_RAISE_TIMEOUT = timedelta(seconds=30)
# Default wait for an approval decision when the IR doesn't specify a
# per-point timeout. 24 hours is long enough for human approval but
# short enough that abandoned workflows clean themselves up.
_DEFAULT_APPROVAL_WAIT = timedelta(hours=24)


@dataclass
class TaskWorkflowInput:
    """Caller-facing input. Mirrors ``TaskRequest`` shape minus auth bits."""

    task_id: str
    objective: str
    actor: str = "system"
    # Bet 2c-ii: owning tenant; carried onto approval + audit rows this task raises.
    tenant_id: str = "default"
    trace_id: str | None = None
    template: dict[str, Any] | None = None  # {"id": ..., "version": ...} | None
    glossary: dict[str, Any] = field(default_factory=dict)
    workflow_pattern: str | None = None
    on_failure: str | None = None


@dataclass
class TaskWorkflowResult:
    """What the workflow returns to the caller.

    Carries the same fields the synchronous ``/v1/tasks`` response uses, so
    the API layer can map either source onto the same JSON shape.
    """

    task_id: str
    workflow_pattern: str
    templates: list[dict[str, str]]
    output: dict[str, Any]
    node_outputs: dict[str, Any]
    verifier: dict[str, Any]
    ir_validation: dict[str, Any]
    replan_count: int
    replan_history: list[dict[str, Any]]
    planner_justification: str | None = None


@workflow.defn(name="OrchestratorWorkflow")
class OrchestratorWorkflow:
    """Driver of the planner → execute → replan loop on Temporal."""

    def __init__(self) -> None:
        # Cancel is a one-shot flag — any loop iteration honours it.
        self._cancelled: bool = False
        # Approval decisions accumulate in a queue so signals delivered
        # before the workflow reaches its wait point still survive the
        # next ``self._reset`` cycle. Each entry is one decision the
        # workflow consumes in order.
        self._decisions: list[dict[str, Any]] = []

    @workflow.signal(name="cancel")
    def on_cancel(self) -> None:
        """Cooperative cancel — every wait_condition wakes up and bails."""
        self._cancelled = True

    @workflow.signal(name="approve")
    def on_approve(self) -> None:
        """Approve the current pending approval gate. Queued so the workflow
        sees it even when the signal arrives before the wait point."""
        self._decisions.append({"action": "approve", "patch": None})

    @workflow.signal(name="deny")
    def on_deny(self) -> None:
        """Reject the current pending approval gate. Terminates the run."""
        self._decisions.append({"action": "deny", "patch": None})

    @workflow.signal(name="modify_input")
    def on_modify_input(self, patch: dict[str, Any]) -> None:
        """Approve + ship a patch that merges into the next pass's glossary."""
        self._decisions.append({"action": "modify_input", "patch": dict(patch)})

    @workflow.query(name="is_cancelled")
    def is_cancelled(self) -> bool:
        return self._cancelled

    @workflow.query(name="status")
    def status(self) -> dict[str, Any]:
        """Snapshot of decision-queue state — debugging surface for the UI."""
        return {
            "cancelled": self._cancelled,
            "decisions": list(self._decisions),
        }

    @workflow.run
    async def run(self, payload: TaskWorkflowInput) -> TaskWorkflowResult:
        # ---- Plan ----------------------------------------------------------
        ir_dump = await workflow.execute_activity(
            "plan_initial_ir",
            {
                "objective": payload.objective,
                "glossary": payload.glossary,
                "force_pattern": payload.workflow_pattern,
                "template": payload.template,
            },
            schedule_to_close_timeout=_PLAN_TIMEOUT,
            retry_policy=_PLAN_RETRY,
        )

        validation = await workflow.execute_activity(
            "validate_ir",
            ir_dump,
            schedule_to_close_timeout=_VALIDATE_TIMEOUT,
            retry_policy=_PLAN_RETRY,
        )
        if not validation.get("ok", False):
            # Initial-IR validation failure terminates with the failure payload
            # baked into the workflow result; the API layer surfaces this as a
            # 400. No replan path is open here because we have no IR to revise.
            return TaskWorkflowResult(
                task_id=payload.task_id,
                workflow_pattern=ir_dump["spec"]["workflow_pattern"],
                templates=[],
                output={},
                node_outputs={},
                verifier={"outcome": "fail", "failures": []},
                ir_validation=validation,
                replan_count=0,
                replan_history=[
                    {
                        "attempt": 0,
                        "outcome": "ir_validation_failed",
                        "ir_validation_failures": validation.get("failures") or [],
                    }
                ],
            )

        # ---- Approval gates ------------------------------------------------
        # Sprint 14.4 + 14.5: every approval_point in the IR raises a request
        # and waits for the next queued decision. Signals deliver into an
        # append-only ``_decisions`` queue so a REST decision that races the
        # workflow start (i.e. arrives before this wait point is reached)
        # still survives — we consume by index, not by per-iteration flags.
        modified_input_carry: dict[str, Any] | None = None
        denial: dict[str, Any] | None = None
        decisions_consumed = 0

        for idx, ap in enumerate(ir_dump["spec"].get("approval_points") or []):
            approval_id = f"approval-{payload.task_id}-{idx}"
            await workflow.execute_activity(
                "raise_approval_request",
                {
                    "approval_id": approval_id,
                    "task_id": payload.task_id,
                    "tenant_id": payload.tenant_id,
                    "before_node_id": ap.get("before_node"),
                    "title": ap.get("title"),
                    "description": ap.get("description"),
                    "action_summary": ap.get("action_summary"),
                    "risk_classification": ap.get("risk_classification") or "medium",
                    "affected_resources": ap.get("affected_resources") or [],
                    "approver_roles": ap.get("approver_roles") or [],
                    "timeout_seconds": ap.get("timeout_seconds"),
                    "timeout_auto_action": ap.get("timeout_auto_action"),
                    "notification_channels": ap.get("notification_channels") or [],
                    "trace_id": payload.trace_id,
                },
                schedule_to_close_timeout=_APPROVAL_RAISE_TIMEOUT,
                retry_policy=_AUDIT_RETRY,
            )

            # Sprint 14.8 adds timeout-driven auto_action; today an undecided
            # gate blocks until cancel or a queued decision arrives.
            waiting_for_index = decisions_consumed

            def _next_decision_ready(waiting: int = waiting_for_index) -> bool:
                return len(self._decisions) > waiting or self._cancelled

            await workflow.wait_condition(_next_decision_ready)

            if self._cancelled:
                denial = {
                    "attempt": 0,
                    "outcome": "cancelled",
                    "approval_id": approval_id,
                    "before_node": ap.get("before_node"),
                }
                break

            decision = self._decisions[decisions_consumed]
            decisions_consumed += 1  # noqa: SIM113 — tracks queue index, not loop count
            action = decision.get("action")
            if action == "deny":
                denial = {
                    "attempt": 0,
                    "outcome": "denied",
                    "approval_id": approval_id,
                    "before_node": ap.get("before_node"),
                }
                break
            # ``approve`` or ``modify_input``. The latter ships a patch that
            # merges into the next pass's glossary (one-shot).
            patch = decision.get("patch")
            if isinstance(patch, dict):
                modified_input_carry = dict(patch)

        if denial is not None:
            return TaskWorkflowResult(
                task_id=payload.task_id,
                workflow_pattern=ir_dump["spec"]["workflow_pattern"],
                templates=[],
                output={},
                node_outputs={},
                verifier={"outcome": "fail", "failures": []},
                ir_validation=validation,
                replan_count=0,
                replan_history=[denial],
            )

        # ---- Replan loop ---------------------------------------------------
        budget = (ir_dump["spec"].get("budget") or {}).get("max_replan_count") or 0
        max_replan_count = budget or DEFAULT_MAX_REPLAN_COUNT
        replan_count = 0
        replan_history: list[dict[str, Any]] = []
        last_pass: dict[str, Any] = {}

        while True:
            if self._cancelled:
                replan_history.append({"attempt": replan_count, "outcome": "cancelled"})
                break

            # Merge a one-shot approver patch into glossary for this pass.
            # ``modified_input`` from the approver decision shadows the
            # caller's payload for matching keys, then resets so subsequent
            # replan attempts don't keep applying it.
            pass_glossary = dict(payload.glossary or {})
            if modified_input_carry is not None:
                pass_glossary.update(modified_input_carry)
                modified_input_carry = None
            last_pass = await workflow.execute_activity(
                "execute_graph_pass",
                {
                    "ir": ir_dump,
                    "task_id": payload.task_id,
                    "objective": payload.objective,
                    "actor": payload.actor,
                    "trace_id": payload.trace_id,
                    "glossary": pass_glossary,
                    "replan_count": replan_count,
                    "on_failure": payload.on_failure,
                },
                schedule_to_close_timeout=_GRAPH_TIMEOUT,
                retry_policy=_GRAPH_RETRY,
            )

            replan_request = last_pass.get("replan_request")
            if not replan_request:
                break

            if replan_count >= max_replan_count:
                replan_history.append(
                    {
                        "attempt": replan_count + 1,
                        "outcome": "budget_exhausted",
                        "failing_node": replan_request.get("failing_node"),
                        "verifier_id": replan_request.get("verifier_id"),
                        "rationale": replan_request.get("rationale"),
                    }
                )
                break

            replan_count += 1
            previous_pattern = ir_dump["spec"]["workflow_pattern"]
            try:
                ir_dump = await workflow.execute_activity(
                    "replan_ir",
                    {
                        "original_ir": ir_dump,
                        "failing_node": replan_request.get("failing_node"),
                        "failure_summary": replan_request,
                        "replan_count": replan_count,
                    },
                    schedule_to_close_timeout=_PLAN_TIMEOUT,
                    retry_policy=_PLAN_RETRY,
                )
            except Exception as exc:  # noqa: BLE001 — exposed as planner_error history entry
                replan_history.append(
                    {
                        "attempt": replan_count,
                        "outcome": "planner_error",
                        "failing_node": replan_request.get("failing_node"),
                        "error": str(exc),
                    }
                )
                break

            validation = await workflow.execute_activity(
                "validate_ir",
                ir_dump,
                schedule_to_close_timeout=_VALIDATE_TIMEOUT,
                retry_policy=_PLAN_RETRY,
            )
            if not validation.get("ok", False):
                replan_history.append(
                    {
                        "attempt": replan_count,
                        "outcome": "ir_validation_failed",
                        "failing_node": replan_request.get("failing_node"),
                        "ir_validation_failures": validation.get("failures") or [],
                    }
                )
                break

            new_pattern = ir_dump["spec"]["workflow_pattern"]
            new_templates = [
                {"id": n.get("template_id"), "version": n.get("template_version")}
                for n in ir_dump["spec"]["nodes"]
                if n.get("type") == "agent"
            ]
            entry = {
                "attempt": replan_count,
                "outcome": "replanned",
                "failing_node": replan_request.get("failing_node"),
                "verifier_id": replan_request.get("verifier_id"),
                "rationale": replan_request.get("rationale"),
                "previous_pattern": previous_pattern,
                "new_pattern": new_pattern,
                "new_templates": new_templates,
            }
            replan_history.append(entry)
            await workflow.execute_activity(
                "record_audit",
                {
                    "action": "task_replan",
                    "resource_id": payload.task_id,
                    "after": entry,
                    "actor": payload.actor,
                    "trace_id": payload.trace_id,
                    "tenant_id": payload.tenant_id,
                },
                schedule_to_close_timeout=_AUDIT_TIMEOUT,
                retry_policy=_AUDIT_RETRY,
            )

        # ---- Final audit + assemble result ---------------------------------
        node_outputs = last_pass.get("node_outputs") or {}
        verifier = _terminal_verifier(ir_dump["spec"]["workflow_pattern"], node_outputs)
        output = _terminal_output(ir_dump["spec"], node_outputs)
        templates = last_pass.get("templates") or []

        await workflow.execute_activity(
            "record_audit",
            {
                "action": "task_submit",
                "resource_id": payload.task_id,
                "after": {
                    "workflow_pattern": ir_dump["spec"]["workflow_pattern"],
                    "templates": templates,
                    "verifier_outcome": verifier.get("outcome"),
                    "replan_count": replan_count,
                    "replan_budget": max_replan_count,
                },
                "actor": payload.actor,
                "trace_id": payload.trace_id,
                "tenant_id": payload.tenant_id,
            },
            schedule_to_close_timeout=_AUDIT_TIMEOUT,
            retry_policy=_AUDIT_RETRY,
        )

        return TaskWorkflowResult(
            task_id=payload.task_id,
            workflow_pattern=ir_dump["spec"]["workflow_pattern"],
            templates=templates,
            output=output,
            node_outputs=node_outputs,
            verifier=verifier,
            ir_validation=validation,
            replan_count=replan_count,
            replan_history=replan_history,
        )


def _terminal_verifier(pattern: str, node_outputs: dict[str, Any]) -> dict[str, Any]:
    """Pick the verifier slot the API surfaces as the run's terminal outcome."""
    if pattern == "single_agent":
        slot = node_outputs.get("verify") or {}
        return slot if isinstance(slot, dict) else {}
    # sequential / manager_specialists: pick the last verify_<id> slot.
    verify_slots = [v for k, v in node_outputs.items() if isinstance(k, str) and k.startswith("verify_")]
    if not verify_slots:
        return {"outcome": "fail", "failures": []}
    last = verify_slots[-1]
    return last if isinstance(last, dict) else {}


def _terminal_output(ir_spec: dict[str, Any], node_outputs: dict[str, Any]) -> dict[str, Any]:
    """Locate the terminal agent's output for the response payload."""
    pattern = ir_spec.get("workflow_pattern")
    nodes = [n for n in ir_spec.get("nodes") or [] if n.get("type") == "agent"]
    if not nodes:
        return {}
    if pattern == "single_agent":
        result = node_outputs.get("agent")
        return result if isinstance(result, dict) else {}
    if pattern == "manager_specialists":
        manager = next((n for n in nodes if (n.get("config") or {}).get("role") == "manager"), nodes[0])
        result = node_outputs.get(manager["id"]) or {}
        return result if isinstance(result, dict) else {}
    # sequential: terminal = last node by IR order
    result = node_outputs.get(nodes[-1]["id"]) or {}
    return result if isinstance(result, dict) else {}


@workflow.defn(name="ApprovalTimeoutSweepWorkflow")
class ApprovalTimeoutSweepWorkflow:
    """Sprint 14.8: scheduled job that fires expired approvals.

    Intended to be invoked on a Temporal Schedule (e.g. every minute).
    Each run scans for pending approvals whose timeout has elapsed,
    transitions them to ``timed_out``, applies the auto_action, and —
    if the action implies a workflow decision (``grant`` / ``reject``) —
    sends the corresponding signal to the task workflow.

    Idempotent at the approval level: each ``expire_approval`` activity
    re-checks the row's current state and no-ops on rows already decided
    by a human between sweeps.
    """

    @workflow.run
    async def run(self) -> dict[str, Any]:
        timed_out = await workflow.execute_activity(
            "find_timed_out_approvals",
            schedule_to_close_timeout=timedelta(seconds=30),
            retry_policy=_AUDIT_RETRY,
        )
        results: list[dict[str, Any]] = []
        for entry in timed_out:
            outcome = await workflow.execute_activity(
                "expire_approval",
                entry,
                schedule_to_close_timeout=timedelta(seconds=30),
                retry_policy=_AUDIT_RETRY,
            )
            results.append({"approval_id": entry["approval_id"], **outcome})
            signal_name = outcome.get("signal")
            task_id = outcome.get("task_id")
            if signal_name and task_id:
                await workflow.execute_activity(
                    "signal_task_workflow",
                    {"task_id": task_id, "signal": signal_name},
                    schedule_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_AUDIT_RETRY,
                )
        return {"swept": len(results), "results": results}


__all__ = [
    "ApprovalTimeoutSweepWorkflow",
    "OrchestratorWorkflow",
    "TaskWorkflowInput",
    "TaskWorkflowResult",
]
