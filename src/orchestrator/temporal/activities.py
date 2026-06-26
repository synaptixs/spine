"""Sprint 13.3: Temporal activities — the side-effecting steps a workflow needs.

The workflow itself is purely deterministic and can't read DBs, call LLMs,
or open sockets. Every interaction with the outside world goes through an
activity, which Temporal can retry, observe, and time out independently.

Spec scope says "Activities wrap: LLM calls, MCP tool invocations, verifier
execution, planner replan calls." Rather than wrap each LLM call as its own
activity — which would require rebuilding the LangGraph runtime — we draw
activity boundaries at workflow-decision points:

  - ``plan_initial_ir``: ask the planner for an IR.
  - ``validate_ir``: run the IR validator against the registry.
  - ``execute_graph_pass``: build + invoke the LangGraph for one pass. The
    runtime's internal LLM / tool / verifier calls all happen inside this
    activity. Retry policy treats the whole pass as the unit of retry.
  - ``replan_ir``: ask the planner to revise an IR after a chain failure.
  - ``record_audit``: write task_submit + task_replan rows.

Boundaries chosen so the workflow can drive the replan loop (the same loop
``/v1/tasks`` runs today) without re-implementing LangGraph. Sprint 14's
approval gate slots in between ``validate_ir`` and ``execute_graph_pass``
without changing this surface.

Every activity is implemented as a method on ``Activities``, which holds
the worker-side ``ActivityDeps``. Bound methods get registered with the
Temporal worker; activities don't reach for module globals.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from temporalio import activity

from orchestrator.approval import (
    ApprovalRequest,
    ApprovalRequestRepo,
    ApprovalState,
    ApprovalTimeout,
    Approver,
    RiskClassification,
)
from orchestrator.ir.graph import GraphIR
from orchestrator.ir.validator import IRValidator
from orchestrator.obs import tracing
from orchestrator.planner import PlannerError, PlannerV1
from orchestrator.registry.db.models import AgentTemplateRow
from orchestrator.registry.repositories import AuditLogRepo, VersionedRepo
from orchestrator.runtime.task_orchestration import (
    TaskOrchestrationError,
    build_graph,
    find_replan_request,
    parse_failure_policy,
    resolve_templates,
    runtime_to_ir_node_id,
)
from orchestrator.temporal.deps import ActivityDeps

logger = logging.getLogger("orchestrator.temporal.activities")


class Activities:
    """Container for activity implementations bound to worker-side deps."""

    def __init__(self, deps: ActivityDeps) -> None:
        self._deps = deps

    @activity.defn(name="plan_initial_ir")
    async def plan_initial_ir(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the planner. ``payload`` carries objective, glossary, optional
        ``force_pattern``, and an optional pinned ``template`` ref.

        Returns the IR as a JSON-mode pydantic dump so Temporal can serialise
        it across the workflow / activity boundary cleanly.
        """
        from orchestrator.ir.graph import Node, NodeType, WorkflowPattern
        from orchestrator.registry._common import LifecycleState, Metadata

        async with self._deps.session_factory() as session:
            repo: VersionedRepo[AgentTemplateRow] = VersionedRepo(session, AgentTemplateRow)
            pinned_ref = payload.get("template")
            if pinned_ref:
                # Build a single-agent IR straight from the caller-pinned ref.
                row = (
                    await repo.get_latest_published(pinned_ref["id"])
                    if pinned_ref.get("version") is None
                    else await repo.get_by_id_version(pinned_ref["id"], pinned_ref["version"])
                )
                if row is None or row.status != LifecycleState.PUBLISHED.value:
                    raise TaskOrchestrationError(
                        f"No published version for {pinned_ref['id']}@{pinned_ref.get('version')!r}."
                    )
                ir = GraphIR(
                    metadata=Metadata(
                        id="plan.single_agent",
                        version="0.1.0",
                        description=f"caller-pinned plan for {row.id}@{row.version}",
                    ),
                    spec={  # type: ignore[arg-type]
                        "objective": payload["objective"],
                        "workflow_pattern": WorkflowPattern.SINGLE_AGENT.value,
                        "task_glossary": payload.get("glossary") or {},
                        "nodes": [
                            Node(
                                id="n_agent",
                                type=NodeType.AGENT,
                                template_id=row.id,
                                template_version=row.version,
                                config={"justification": "pinned by caller"},
                            )
                        ],
                    },
                )
                return ir.model_dump(mode="json")

            force_raw = payload.get("force_pattern")
            force = WorkflowPattern(force_raw) if force_raw else None
            try:
                ir = await PlannerV1(llm=self._deps.llm).plan(
                    payload["objective"],
                    session=session,
                    glossary=payload.get("glossary") or {},
                    force_pattern=force,
                )
            except PlannerError as exc:
                raise TaskOrchestrationError(f"Planner failed: {exc}") from exc
            return ir.model_dump(mode="json")

    @activity.defn(name="validate_ir")
    async def validate_ir(self, ir_dump: dict[str, Any]) -> dict[str, Any]:
        """Run IRValidator and return its report as a dict."""
        ir = GraphIR.model_validate(ir_dump)
        async with self._deps.session_factory() as session:
            report = await IRValidator().validate(ir, session=session)
        return report.model_dump()

    @activity.defn(name="execute_graph_pass")
    async def execute_graph_pass(self, request: dict[str, Any]) -> dict[str, Any]:
        """Build the LangGraph for the given IR and invoke it once.

        ``request`` carries the IR dump plus per-pass metadata: task_id,
        objective, actor, trace_id, glossary, replan_count, on_failure.

        Returns ``{"final_state": ..., "replan_request": ... | None}``. The
        workflow uses ``replan_request`` to decide whether to loop.
        """
        ir = GraphIR.model_validate(request["ir"])
        replan_count = int(request.get("replan_count") or 0)
        # Bind the app trace_id so spans nested under this activity (agent.step,
        # llm.complete, tool.<name>) carry the join key back to the audit log,
        # and tag the activity span with the pass number (Phase 3).
        with (
            tracing.bind_trace_id(request.get("trace_id")),
            tracing.span("execute_graph_pass", **{"replan_count": replan_count}),
        ):
            async with self._deps.session_factory() as session:
                repo: VersionedRepo[AgentTemplateRow] = VersionedRepo(session, AgentTemplateRow)
                agent_nodes, templates = await resolve_templates(repo, ir)
                failure_policy = parse_failure_policy(request.get("on_failure"))
                graph = build_graph(
                    ir=ir,
                    agent_nodes=agent_nodes,
                    templates=templates,
                    llm=self._deps.llm,
                    audit_logger=None,  # Sprint 13 keeps per-verifier audit deferred.
                    artifact_store=self._deps.artifact_store,
                    failure_policy=failure_policy,
                )

                task_id = request["task_id"]
                thread_id = task_id if replan_count == 0 else f"{task_id}#replan-{replan_count}"
                initial_state: dict[str, Any] = {
                    "task_metadata": {
                        "task_id": task_id,
                        "objective": request["objective"],
                        "actor": request.get("actor") or self._deps.actor,
                        "trace_id": request.get("trace_id"),
                        "replan_count": replan_count,
                        **{
                            k: v
                            for k, v in (request.get("glossary") or {}).items()
                            if not isinstance(v, dict)
                        },
                    },
                    "task_glossary": request.get("glossary") or {},
                }
                config = {"configurable": {"thread_id": thread_id}}
                final_state = await graph.ainvoke(initial_state, config=config)

        node_outputs = final_state.get("node_outputs") or {}
        replan_req = find_replan_request(node_outputs)
        return {
            "final_state": dict(final_state),
            "node_outputs": node_outputs,
            "replan_request": replan_req,
            "templates": [{"id": t.metadata.id, "version": t.metadata.version} for t in templates],
        }

    @activity.defn(name="replan_ir")
    async def replan_ir(self, request: dict[str, Any]) -> dict[str, Any]:
        """Ask the planner for a revised IR after a chain failure."""
        ir = GraphIR.model_validate(request["original_ir"])
        failing_runtime_id = str(request.get("failing_node") or "")
        failing_ir_id = runtime_to_ir_node_id(ir, failing_runtime_id)
        replan_count = int(request["replan_count"])
        async with self._deps.session_factory() as session:
            try:
                revised = await PlannerV1(llm=self._deps.llm).replan(
                    ir,
                    session=session,
                    failing_node_id=failing_ir_id,
                    failure_summary=request["failure_summary"],
                    replan_count=replan_count,
                )
            except PlannerError as exc:
                raise TaskOrchestrationError(f"Replan failed: {exc}") from exc
        return revised.model_dump(mode="json")

    @activity.defn(name="raise_approval_request")
    async def raise_approval_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sprint 14.4: persist an approval-required event from the workflow.

        Returns the approval id so the workflow can correlate the eventual
        signal back to the row. The workflow then waits on ``approve`` /
        ``deny`` / ``modify_input`` signals before continuing.

        Idempotency: the activity uses the approval id provided by the
        workflow (which derives it deterministically from task_id +
        before_node_id), so Temporal retries don't double-insert.
        """
        approval_id = str(payload["approval_id"])
        task_id = str(payload["task_id"])
        before_node = str(payload["before_node_id"])
        async with self._deps.session_factory() as session:
            repo = ApprovalRequestRepo(session)
            existing = await repo.get(approval_id)
            if existing is not None:
                return existing.model_dump(mode="json")

            timeout_seconds = payload.get("timeout_seconds")
            timeout_action = payload.get("timeout_auto_action")
            timeout: ApprovalTimeout | None = None
            if isinstance(timeout_seconds, int) and isinstance(timeout_action, str):
                timeout = ApprovalTimeout(after_seconds=timeout_seconds, auto_action=timeout_action)

            risk_raw = str(payload.get("risk_classification") or "medium")
            try:
                risk = RiskClassification(risk_raw)
            except ValueError:
                risk = RiskClassification.MEDIUM

            approver_roles = payload.get("approver_roles") or []
            approvers = (
                [Approver(role=str(r), min_required=1) for r in approver_roles]
                if approver_roles
                else [Approver(role="any", min_required=1)]
            )

            tenant_id = str(payload.get("tenant_id") or "default")
            request = ApprovalRequest(
                id=approval_id,
                task_id=task_id,
                tenant_id=tenant_id,
                before_node_id=before_node,
                title=str(payload.get("title") or f"Approval required before {before_node}"),
                description=str(payload.get("description") or ""),
                action_summary=str(payload.get("action_summary") or f"Run node {before_node}"),
                risk_classification=risk,
                affected_resources=list(payload.get("affected_resources") or []),
                approvers=approvers,
                timeout=timeout,
                notification_channels=list(payload.get("notification_channels") or []),
                trace_id=payload.get("trace_id"),
            )
            saved = await repo.create(request)

            await AuditLogRepo(session).write(
                actor=self._deps.actor,
                action="approval_raised",
                resource_type="approval",
                resource_id=saved.id,
                tenant_id=tenant_id,
                after={
                    "task_id": saved.task_id,
                    "before_node_id": saved.before_node_id,
                    "risk": saved.risk_classification.value,
                    "approver_roles": [a.role for a in saved.approvers],
                    "notification_channels": saved.notification_channels,
                    "before_hash": saved.before_hash,
                },
                trace_id=saved.trace_id,
            )
            await session.commit()
            return saved.model_dump(mode="json")

    @activity.defn(name="find_timed_out_approvals")
    async def find_timed_out_approvals(self) -> list[dict[str, Any]]:
        """Sprint 14.8: scan pending approvals for elapsed timeouts.

        Returns a compact dict per timed-out approval so the sweep
        workflow can dispatch ``expire_approval`` against each without
        re-fetching. Empty list when nothing's expired.
        """
        async with self._deps.session_factory() as session:
            rows = await ApprovalRequestRepo(session).list_timed_out()
        return [
            {
                "approval_id": r.id,
                "task_id": r.task_id,
                "before_node_id": r.before_node_id,
                "risk_classification": r.risk_classification.value,
                "auto_action": (r.timeout.auto_action if r.timeout else "escalate"),
            }
            for r in rows
        ]

    @activity.defn(name="expire_approval")
    async def expire_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sprint 14.8: mark a pending approval as timed_out and apply its
        ``auto_action``.

        Safety rail: ``grant`` is only honoured for low-risk requests; any
        higher classification falls through to ``escalate`` and we record
        the override on the audit row.

        Returns ``{"applied_action": "...", "signal": "approve"|"deny"|None}``
        so the sweep workflow knows what (if any) signal to send the
        waiting task workflow.
        """
        approval_id = str(payload["approval_id"])
        auto_action = str(payload.get("auto_action") or "escalate")
        risk = str(payload.get("risk_classification") or "medium")

        applied = auto_action
        if auto_action == "grant" and risk != RiskClassification.LOW.value:
            applied = "escalate"  # safety override

        signal_name: str | None = None
        if applied == "grant":
            signal_name = "approve"
        elif applied == "reject":
            signal_name = "deny"
        # "escalate" doesn't auto-signal; a human still has to decide.

        async with self._deps.session_factory() as session:
            repo = ApprovalRequestRepo(session)
            existing = await repo.get(approval_id)
            if existing is None:
                return {"applied_action": "missing", "signal": None}
            if existing.state is not ApprovalState.PENDING:
                # Race: someone decided between sweep and expiry — no-op.
                return {"applied_action": "already_decided", "signal": None}

            await repo.decide(
                approval_id,
                state=ApprovalState.TIMED_OUT,
                decided_by="system.timeout_sweep",
            )
            await AuditLogRepo(session).write(
                actor="system.timeout_sweep",
                action="approval_timed_out",
                resource_type="approval",
                resource_id=approval_id,
                before={"state": existing.state.value, "before_hash": existing.before_hash},
                after={
                    "state": ApprovalState.TIMED_OUT.value,
                    "auto_action_requested": auto_action,
                    "auto_action_applied": applied,
                    "risk_classification": risk,
                    "task_id": existing.task_id,
                },
                trace_id=existing.trace_id,
            )
            await session.commit()
        return {
            "applied_action": applied,
            "signal": signal_name,
            "task_id": payload.get("task_id"),
        }

    @activity.defn(name="signal_task_workflow")
    async def signal_task_workflow(self, payload: dict[str, Any]) -> None:
        """Sprint 14.8 helper: send a signal to a task workflow from inside
        the timeout-sweep workflow (workflows can't open client connections
        directly, but activities can).

        Failures log + swallow: the approval row is already terminal, so a
        missing workflow is fine (the task may have already ended).
        """
        from orchestrator.temporal import connect_client
        from orchestrator.temporal.config import TemporalConfig

        task_id = str(payload["task_id"])
        signal_name = str(payload["signal"])
        try:
            cfg = TemporalConfig.from_env()
            client = await connect_client(cfg)
            handle = client.get_workflow_handle(workflow_id=f"task-{task_id}")
            await handle.signal(signal_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "approval.timeout_signal_failed",
                extra={"task_id": task_id, "signal": signal_name, "error": str(exc)},
            )

    @activity.defn(name="record_audit")
    async def record_audit(self, request: dict[str, Any]) -> None:
        """Write an audit row. The workflow calls this once per replan attempt
        (action=task_replan) plus once at the end (action=task_submit).
        """
        async with self._deps.session_factory() as session:
            await AuditLogRepo(session).write(
                actor=request.get("actor") or self._deps.actor,
                action=request["action"],
                resource_type=request.get("resource_type") or "task",
                resource_id=request["resource_id"],
                after=request.get("after"),
                trace_id=request.get("trace_id"),
                tenant_id=str(request.get("tenant_id") or "default"),
            )
            await session.commit()


def new_task_id() -> str:
    """Activity-style task-id generator. Kept here so workflows can call it
    via an activity wrapper (workflow code must avoid uuid.uuid4 directly
    because Temporal needs deterministic replay).
    """
    return uuid.uuid4().hex


__all__ = ["Activities", "new_task_id"]
