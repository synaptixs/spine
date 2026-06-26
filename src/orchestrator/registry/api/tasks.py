"""POST /v1/tasks: synchronous task execution.

Sprint 5 shipped the single-agent executor. Sprint 6 wired the planner +
IR validator. Sprint 8 adds the sequential workflow pattern: when the
planner returns ``workflow_pattern=sequential``, the runtime stitches
together a chain of (agent, schema-verifier) nodes whose ``inputs_from``
mappings carry typed state between stages.

Async submission, streaming, and Temporal-durable execution arrive in
Sprint 13.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.core.llm import LLMClient
from orchestrator.ir.graph import GraphIR, Node, NodeType, WorkflowPattern
from orchestrator.ir.validator import IRValidator
from orchestrator.planner import PlannerError, PlannerV1
from orchestrator.registry._common import LifecycleState, Metadata
from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.api.deps import PrincipalDep, SessionDep, TraceIdDep
from orchestrator.registry.calibration import CalibrationHistoryRepo
from orchestrator.registry.db.models import AgentTemplateRow
from orchestrator.registry.repositories import AuditLogRepo, VersionedRepo
from orchestrator.runtime import AuditLogger, ObjectStoreArtifactStore
from orchestrator.runtime.task_orchestration import (
    TaskOrchestrationError,
    build_graph,
    find_replan_request,
    parse_failure_policy,
    resolve_templates,
    runtime_to_ir_node_id,
    terminal_node_summary,
)

logger = logging.getLogger("orchestrator.registry.tasks")

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


# Sprint 12.5: replan retry budget. Caller may set ir.spec.budget.max_replan_count
# explicitly; anything <= 0 (the Pydantic default) means "use orchestration default".
DEFAULT_MAX_REPLAN_COUNT = 2


class TemplateRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str | None = None


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=4096)
    template: TemplateRef | None = None
    glossary: dict[str, Any] = Field(default_factory=dict)
    workflow_pattern: str | None = Field(
        default=None,
        description="Optional pre-decision: 'single_agent' or 'sequential'. "
        "When set, the planner is constrained to that shape.",
    )
    on_failure: str | None = Field(
        default=None,
        description="Optional failure policy: 'terminate' (default), 'continue_with_warning', "
        "or 'replan'. When 'replan', the orchestration layer asks the planner for "
        "a revised IR after a verifier-chain failure, up to max_replan_count attempts.",
    )
    execution_mode: str | None = Field(
        default=None,
        description="Sprint 13: 'sync' (default) runs the task in the request "
        "handler. 'temporal' enqueues it as an OrchestratorWorkflow on the "
        "configured task queue and awaits the result. Both modes return the "
        "same response shape; choose based on whether you need durable "
        "execution / signals / human approval.",
    )


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    trace_id: str
    workflow_pattern: str
    templates: list[TemplateRef]
    output: dict[str, Any]
    node_outputs: dict[str, Any]
    verifier: dict[str, Any]
    ir_validation: dict[str, Any]
    planner_justification: str | None = None
    replan_count: int = 0
    replan_history: list[dict[str, Any]] = Field(default_factory=list)


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    response_model=TaskResponse,
    responses={
        400: {"description": "IR validation failed."},
        404: {"description": "No published template matches the request."},
        500: {"description": "Runtime failed before producing an output."},
    },
)
async def submit_task(
    payload: Annotated[TaskRequest, Body(...)],
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    trace_id: TraceIdDep,
) -> TaskResponse:
    # ``actor`` is the principal's stable id (was the raw key); ``tenant_id``
    # (Bet 2c-ii) scopes the task's approval + audit rows.
    actor = principal.id
    llm: LLMClient | None = getattr(request.app.state, "llm_client", None)
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="LLM client not configured. Set app.state.llm_client at startup.",
        )

    # Sprint 13.7: side-by-side dispatch. ``execution_mode="temporal"`` (or
    # ORCHESTRATOR_EXECUTION_MODE=temporal in env) routes the request through
    # OrchestratorWorkflow instead of running synchronously. Both code paths
    # return the same TaskResponse shape.
    resolved_mode = _resolve_execution_mode(payload.execution_mode)
    if resolved_mode == "temporal":
        return await _submit_via_temporal(
            payload, actor=actor, trace_id=trace_id, tenant_id=principal.tenant_id
        )

    repo: VersionedRepo[AgentTemplateRow] = VersionedRepo(session, AgentTemplateRow)

    justification: str
    if payload.template is not None:
        pinned_ir = await _ir_from_pinned_template(repo, payload)
        if pinned_ir is None:
            ref = payload.template
            raise HTTPException(
                status_code=404,
                detail=f"No published version for {ref.id}@{ref.version!r}.",
            )
        ir = pinned_ir
        justification = "pinned by caller"
    else:
        try:
            force = _parse_pattern(payload.workflow_pattern)
            ir = await PlannerV1(llm=llm).plan(
                payload.objective,
                session=session,
                glossary=payload.glossary,
                force_pattern=force,
            )
        except PlannerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        justification = _plan_justification(ir) or "LLM-selected plan"

    validator_report = await IRValidator().validate(ir, session=session)
    if not validator_report.ok:
        raise HTTPException(
            status_code=400,
            detail={"detail": "IR validation failed.", "failures": validator_report.failures},
        )

    task_id = uuid.uuid4().hex
    audit_logger = _build_audit_logger(request, actor)
    artifact_store = getattr(request.app.state, "artifact_store", None) or ObjectStoreArtifactStore()

    # Sprint 12.3 + 12.5: replan orchestration loop. We invoke the graph,
    # detect a chain-emitted ``replan_request`` on the returned state, ask
    # the planner for a revised IR, rebuild the graph, and re-invoke with a
    # fresh checkpoint thread until either the chain stops asking to replan
    # or the budget is exhausted.
    max_replan_count = ir.spec.budget.max_replan_count or DEFAULT_MAX_REPLAN_COUNT
    try:
        failure_policy = parse_failure_policy(payload.on_failure)
    except TaskOrchestrationError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=str(exc)) from exc
    replan_count = 0
    replan_history: list[dict[str, Any]] = []

    while True:
        try:
            agent_nodes, templates = await resolve_templates(repo, ir)
            graph = build_graph(
                ir=ir,
                agent_nodes=agent_nodes,
                templates=templates,
                llm=llm,
                audit_logger=audit_logger,
                artifact_store=artifact_store,
                failure_policy=failure_policy,
            )
        except TaskOrchestrationError as exc:
            raise HTTPException(status_code=exc.http_status_code, detail=str(exc)) from exc

        # Fresh thread per attempt: a clean checkpoint surface so the rebuilt
        # graph doesn't inherit the failing pass's __replan__ sentinel state.
        thread_id = task_id if replan_count == 0 else f"{task_id}#replan-{replan_count}"
        config = {"configurable": {"thread_id": thread_id}}
        initial_state: dict[str, Any] = {
            "task_metadata": {
                "task_id": task_id,
                "objective": payload.objective,
                "actor": actor,
                "trace_id": trace_id,
                "replan_count": replan_count,
                **{k: v for k, v in payload.glossary.items() if not isinstance(v, dict)},
            },
            "task_glossary": payload.glossary or {},
        }

        try:
            final_state = await graph.ainvoke(initial_state, config=config)
        except Exception as exc:  # noqa: BLE001 — surface every runtime failure as 500
            logger.exception("runtime.failure", extra={"task_id": task_id, "trace_id": trace_id})
            raise HTTPException(status_code=500, detail=f"Runtime failed: {type(exc).__name__}") from exc

        node_outputs = final_state.get("node_outputs") or {}
        replan_request = find_replan_request(node_outputs)
        if replan_request is None:
            break

        if replan_count >= max_replan_count:
            logger.info(
                "replan.budget_exhausted",
                extra={"task_id": task_id, "replan_count": replan_count, "budget": max_replan_count},
            )
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
        # The chain emits the LangGraph node id ("agent" for single_agent graphs);
        # the planner indexes nodes by IR id ("n_agent"). Translate before calling.
        failing_runtime_id = str(replan_request.get("failing_node") or "")
        failing_ir_id = runtime_to_ir_node_id(ir, failing_runtime_id)
        try:
            previous_pattern = ir.spec.workflow_pattern.value
            ir = await PlannerV1(llm=llm).replan(
                ir,
                session=session,
                failing_node_id=failing_ir_id,
                failure_summary=replan_request,
                replan_count=replan_count,
            )
        except PlannerError as exc:
            logger.warning("replan.planner_error", extra={"task_id": task_id, "error": str(exc)})
            replan_history.append(
                {
                    "attempt": replan_count,
                    "outcome": "planner_error",
                    "failing_node": replan_request.get("failing_node"),
                    "error": str(exc),
                }
            )
            break

        validator_report = await IRValidator().validate(ir, session=session)
        if not validator_report.ok:
            logger.warning(
                "replan.ir_validation_failed",
                extra={"task_id": task_id, "failures": validator_report.failures},
            )
            replan_history.append(
                {
                    "attempt": replan_count,
                    "outcome": "ir_validation_failed",
                    "failing_node": replan_request.get("failing_node"),
                    "ir_validation_failures": validator_report.failures,
                }
            )
            break

        replan_history.append(
            {
                "attempt": replan_count,
                "outcome": "replanned",
                "failing_node": replan_request.get("failing_node"),
                "verifier_id": replan_request.get("verifier_id"),
                "rationale": replan_request.get("rationale"),
                "previous_pattern": previous_pattern,
                "new_pattern": ir.spec.workflow_pattern.value,
                "new_templates": [
                    {"id": n.template_id, "version": n.template_version}
                    for n in ir.spec.nodes
                    if n.type is NodeType.AGENT
                ],
            }
        )
        # Loop back: rebuild the graph with the revised IR and try again.

    terminal_output, terminal_verifier = terminal_node_summary(
        ir.spec.workflow_pattern, agent_nodes, node_outputs
    )

    # Sprint 11.6: record one calibration_history row per agent in the IR.
    # Manager-with-specialists records the manager's terminal claim; the
    # specialists' calibrations land via per-specialist verifier outcomes
    # (each specialist's terminal output is in node_outputs).
    await _record_calibration(
        session=session,
        agent_nodes=agent_nodes,
        templates=templates,
        node_outputs=node_outputs,
        terminal_output=terminal_output,
        terminal_verifier=terminal_verifier,
        task_id=task_id,
        trace_id=trace_id,
    )

    # Sprint 12.6: emit one audit row per replan attempt before the final
    # task_submit row, so /trace can surface what was tried and why.
    for entry in replan_history:
        await AuditLogRepo(session).write(
            actor=actor,
            action="task_replan",
            resource_type="task",
            resource_id=task_id,
            after=entry,
            trace_id=trace_id,
        )

    await AuditLogRepo(session).write(
        actor=actor,
        action="task_submit",
        resource_type="task",
        resource_id=task_id,
        after={
            "workflow_pattern": ir.spec.workflow_pattern.value,
            "templates": [{"id": t.metadata.id, "version": t.metadata.version} for t in templates],
            "verifier_outcome": terminal_verifier.get("outcome"),
            "planner_justification": justification,
            "replan_count": replan_count,
            "replan_budget": max_replan_count,
        },
        trace_id=trace_id,
    )
    await session.commit()

    return TaskResponse(
        task_id=task_id,
        trace_id=trace_id,
        workflow_pattern=ir.spec.workflow_pattern.value,
        templates=[TemplateRef(id=t.metadata.id, version=t.metadata.version) for t in templates],
        output=terminal_output,
        node_outputs=node_outputs,
        verifier=terminal_verifier,
        ir_validation=validator_report.model_dump(),
        planner_justification=justification,
        replan_count=replan_count,
        replan_history=replan_history,
    )


def _resolve_execution_mode(raw: str | None) -> str:
    """Pick the execution mode. Per-request overrides the env default."""
    import os as _os

    if raw is not None:
        if raw not in {"sync", "temporal"}:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown execution_mode {raw!r}; expected 'sync' or 'temporal'.",
            )
        return raw
    return _os.getenv("ORCHESTRATOR_EXECUTION_MODE", "sync")


async def _submit_via_temporal(
    payload: TaskRequest, *, actor: str, trace_id: str | None, tenant_id: str = "default"
) -> TaskResponse:
    """Sprint 13.7: enqueue + await an OrchestratorWorkflow.

    Imports stay inside the function so the synchronous code path doesn't pay
    the Temporal SDK import cost on every request.
    """
    from orchestrator.temporal import connect_client
    from orchestrator.temporal.config import TemporalConfig
    from orchestrator.temporal.workflow import OrchestratorWorkflow, TaskWorkflowInput

    task_id = uuid.uuid4().hex
    cfg = TemporalConfig.from_env()
    try:
        client = await connect_client(cfg)
    except Exception as exc:  # noqa: BLE001 — surface as 503 (Temporal not reachable)
        raise HTTPException(status_code=503, detail=f"Temporal frontend unreachable: {exc}") from exc

    try:
        result = await client.execute_workflow(
            OrchestratorWorkflow.run,
            TaskWorkflowInput(
                task_id=task_id,
                objective=payload.objective,
                actor=actor,
                tenant_id=tenant_id,
                trace_id=trace_id,
                template=payload.template.model_dump() if payload.template else None,
                glossary=payload.glossary,
                workflow_pattern=payload.workflow_pattern,
                on_failure=payload.on_failure,
            ),
            id=f"task-{task_id}",
            task_queue=cfg.task_queue,
        )
    except Exception as exc:  # noqa: BLE001 — workflow failure → 500
        logger.exception("temporal.workflow.failed", extra={"task_id": task_id})
        raise HTTPException(status_code=500, detail=f"Workflow failed: {type(exc).__name__}") from exc

    return TaskResponse(
        task_id=result.task_id,
        trace_id=trace_id or "",
        workflow_pattern=result.workflow_pattern,
        templates=[TemplateRef(**t) for t in result.templates],
        output=result.output,
        node_outputs=result.node_outputs,
        verifier=result.verifier,
        ir_validation=result.ir_validation,
        planner_justification=result.planner_justification,
        replan_count=result.replan_count,
        replan_history=result.replan_history,
    )


def _parse_pattern(raw: str | None) -> WorkflowPattern | None:
    if raw is None:
        return None
    try:
        return WorkflowPattern(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown workflow_pattern {raw!r}.") from exc


async def _ir_from_pinned_template(
    repo: VersionedRepo[AgentTemplateRow], payload: TaskRequest
) -> GraphIR | None:
    """Construct a single-agent IR directly from a caller-supplied template ref."""
    ref = payload.template
    assert ref is not None
    row = (
        await repo.get_latest_published(ref.id)
        if ref.version is None
        else await repo.get_by_id_version(ref.id, ref.version)
    )
    if row is None or row.status != LifecycleState.PUBLISHED.value:
        return None

    return GraphIR(
        metadata=Metadata(
            id="plan.single_agent",
            version="0.1.0",
            description=f"caller-pinned plan for {row.id}@{row.version}",
        ),
        spec={  # type: ignore[arg-type]
            "objective": payload.objective,
            "workflow_pattern": WorkflowPattern.SINGLE_AGENT.value,
            "task_glossary": payload.glossary or {},
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


def _plan_justification(ir: GraphIR) -> str | None:
    """Pull the planner's justification off the first agent node (or constraints block)."""
    if ir.spec.constraints.get("split_justification"):
        return str(ir.spec.constraints["split_justification"])
    for node in ir.spec.nodes:
        if node.type is NodeType.AGENT:
            j = node.config.get("justification")
            return str(j) if j else None
    return None


def _build_audit_logger(request: Request, actor: str) -> AuditLogger | None:
    """Return an audit-logger callable that writes one row per verifier execution.

    Each call opens its own short-lived session via the app's session factory,
    so audit writes don't share a transaction with the runtime's other DB work.
    Returns ``None`` when no session factory is available (e.g. unit tests).
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return None

    async def _log(
        verifier_id: str,
        outcome: str,
        elapsed_ms: float,
        payload: dict[str, Any],
        trace_id: str | None,
        task_id: str | None,
    ) -> None:
        async with factory() as audit_session:
            await AuditLogRepo(audit_session).write(
                actor=actor,
                action="verifier_execution",
                resource_type="task",
                resource_id=task_id or "unknown",
                after={
                    "verifier_id": verifier_id,
                    "outcome": outcome,
                    "elapsed_ms": elapsed_ms,
                    "payload": payload,
                },
                trace_id=trace_id,
            )
            await audit_session.commit()

    return _log


async def _record_calibration(
    *,
    session: Any,
    agent_nodes: list[Node],
    templates: list[AgentTemplate],
    node_outputs: dict[str, Any],
    terminal_output: dict[str, Any],
    terminal_verifier: dict[str, Any],
    task_id: str,
    trace_id: str | None,
) -> None:
    """Write one calibration_history row per agent in the IR.

    Specialist agents in a manager_specialists IR record under their own
    output keys; the manager records under the terminal output. The
    terminal verifier outcome attaches to the manager / terminal node;
    specialists fall back to their per-step verifier when present, else
    the terminal outcome.
    """
    repo = CalibrationHistoryRepo(session)
    for node, template in zip(agent_nodes, templates, strict=True):
        node_output = node_outputs.get(node.id) or terminal_output
        confidence = node_output.get("confidence")
        if not isinstance(confidence, (int, float)):
            continue
        per_step_verifier = node_outputs.get(f"verify_{node.id}")
        outcome = (per_step_verifier or terminal_verifier).get("outcome") or "unknown"
        await repo.record(
            template_id=template.metadata.id,
            template_version=template.metadata.version,
            task_id=task_id,
            claimed_confidence=float(confidence),
            verifier_outcome=str(outcome),
            trace_id=trace_id,
        )
