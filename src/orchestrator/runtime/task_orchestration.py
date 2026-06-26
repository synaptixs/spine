"""Sprint 13: shared task-orchestration helpers used by both the synchronous
``POST /v1/tasks`` endpoint and the Temporal workflow path.

Previously these lived as private ``_helpers`` inside the FastAPI route module
and raised ``HTTPException`` directly. That coupling stopped Temporal
activities from reusing them — activities can't import FastAPI exceptions
without making the worker process drag the web stack.

The new shape: helpers raise domain-level exceptions; callers map them to
their own surface (HTTPException at the API edge, Temporal-friendly
ApplicationError-equivalents in activities). Each exception declares the
HTTP status code it *would* convert to, so the API layer is a one-line
adapter rather than a switch statement.
"""

from __future__ import annotations

from typing import Any

from orchestrator.core.llm import LLMClient
from orchestrator.ir.graph import GraphIR, Node, NodeType, WorkflowPattern
from orchestrator.registry._common import LifecycleState
from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.db.models import AgentTemplateRow
from orchestrator.registry.repositories import VersionedRepo
from orchestrator.runtime import (
    AuditLogger,
    FailurePolicy,
    ManagerSpec,
    SequentialStep,
    SpecialistSpec,
    build_manager_specialists_graph,
    build_sequential_graph,
    build_single_agent_graph,
    default_chain_factory,
)
from orchestrator.runtime.artifacts import ArtifactStore
from orchestrator.runtime.post_conditions import FailureAction


class TaskOrchestrationError(Exception):
    """Base for orchestration failures with a hint for HTTP mapping."""

    http_status_code: int = 500


class TemplateNotFoundError(TaskOrchestrationError):
    http_status_code = 404


class UnknownPatternError(TaskOrchestrationError):
    http_status_code = 400


class UnknownFailureActionError(TaskOrchestrationError):
    http_status_code = 400


class ManagerSpecialistsConfigError(TaskOrchestrationError):
    http_status_code = 400


def row_to_template(row: AgentTemplateRow) -> AgentTemplate:
    return AgentTemplate.model_validate(
        {
            "metadata": {
                "id": row.id,
                "version": row.version,
                "description": row.description,
                "tags": list(row.tags),
            },
            "spec": row.spec_json,
            "status": {"state": row.status},
        }
    )


async def resolve_templates(
    repo: VersionedRepo[AgentTemplateRow], ir: GraphIR
) -> tuple[list[Node], list[AgentTemplate]]:
    """Resolve every agent node's template from the registry.

    Raises ``TemplateNotFoundError`` if any node points at a missing /
    unpublished row.
    """
    agent_nodes = [n for n in ir.spec.nodes if n.type is NodeType.AGENT]
    templates: list[AgentTemplate] = []
    for node in agent_nodes:
        row = await repo.get_by_id_version(node.template_id or "", node.template_version or "")
        if row is None or row.status != LifecycleState.PUBLISHED.value:
            raise TemplateNotFoundError(
                f"Planner chose {node.template_id}@{node.template_version}, but no published row exists."
            )
        templates.append(row_to_template(row))
    return agent_nodes, templates


def parse_failure_policy(raw: str | None) -> FailurePolicy | None:
    """Map an ``on_failure`` string to a runtime ``FailurePolicy``."""
    if raw is None:
        return None
    try:
        action = FailureAction(raw)
    except ValueError as exc:
        raise UnknownFailureActionError(f"Unknown on_failure {raw!r}.") from exc
    return FailurePolicy(on_fail=action)


def partition_manager_specialists(
    agent_nodes: list[Node], templates: list[AgentTemplate]
) -> tuple[tuple[Node, AgentTemplate], list[tuple[Node, AgentTemplate]]]:
    """Split (node, template) pairs into (manager, [specialists])."""
    manager_pair: tuple[Node, AgentTemplate] | None = None
    specialists: list[tuple[Node, AgentTemplate]] = []
    for node, tmpl in zip(agent_nodes, templates, strict=True):
        role = node.config.get("role")
        if role == "manager":
            manager_pair = (node, tmpl)
        elif role == "specialist":
            specialists.append((node, tmpl))
    if manager_pair is None:
        raise ManagerSpecialistsConfigError(
            "manager_specialists IR must include exactly one node with config.role='manager'."
        )
    return manager_pair, specialists


def build_graph(
    *,
    ir: GraphIR,
    agent_nodes: list[Node],
    templates: list[AgentTemplate],
    llm: LLMClient,
    audit_logger: AuditLogger | None,
    artifact_store: ArtifactStore,
    failure_policy: FailurePolicy | None = None,
) -> Any:
    """Build the LangGraph runtime for the given IR. Pattern dispatch."""
    if ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT:
        return build_single_agent_graph(
            template=templates[0],
            llm=llm,
            chain_factory=default_chain_factory,
            failure_policy=failure_policy,
            artifact_store=artifact_store,
            audit_logger=audit_logger,
        )
    if ir.spec.workflow_pattern is WorkflowPattern.SEQUENTIAL:
        steps = [
            SequentialStep(
                node_id=node.id,
                template=tmpl,
                inputs_from=dict(node.config.get("inputs_from") or {}),
            )
            for node, tmpl in zip(agent_nodes, templates, strict=True)
        ]
        return build_sequential_graph(
            steps=steps,
            llm=llm,
            chain_factory=default_chain_factory,
            failure_policy=failure_policy,
            artifact_store=artifact_store,
            audit_logger=audit_logger,
        )
    if ir.spec.workflow_pattern is WorkflowPattern.MANAGER_SPECIALISTS:
        manager_pair, specialist_pairs = partition_manager_specialists(agent_nodes, templates)
        manager_node, manager_tmpl = manager_pair
        parallelism = int(manager_node.config.get("parallelism_max", 4))
        manager_spec = ManagerSpec(
            node_id=manager_node.id,
            template=manager_tmpl,
            parallelism_max=parallelism,
        )
        specialist_specs = [SpecialistSpec(node_id=node.id, template=tmpl) for node, tmpl in specialist_pairs]
        return build_manager_specialists_graph(
            manager=manager_spec,
            specialists=specialist_specs,
            llm=llm,
            artifact_store=artifact_store,
        )
    raise UnknownPatternError(f"Unsupported workflow_pattern {ir.spec.workflow_pattern.value!r}.")


def runtime_to_ir_node_id(ir: GraphIR, runtime_id: str) -> str:
    """Map a LangGraph runtime node id back to its IR node id.

    The single_agent graph builder hardcodes the LangGraph node name to
    ``"agent"`` regardless of the IR's ``n_<...>`` id, so a direct lookup
    fails. Sequential and manager_specialists graphs use the IR id as the
    LangGraph id, so the lookup succeeds directly.
    """
    for node in ir.spec.nodes:
        if node.id == runtime_id:
            return node.id
    if ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT:
        for node in ir.spec.nodes:
            if node.type is NodeType.AGENT:
                return node.id
    return runtime_id


def find_replan_request(node_outputs: dict[str, Any]) -> dict[str, Any] | None:
    """Scan chain slots for a ``replan_request`` (chain_node emits this when
    the on-failure dispatcher decides REPLAN). Returns the first one found
    or None.
    """
    for slot in node_outputs.values():
        if isinstance(slot, dict) and "replan_request" in slot:
            value = slot["replan_request"]
            if isinstance(value, dict):
                return value
    return None


def terminal_node_summary(
    pattern: WorkflowPattern,
    agent_nodes: list[Node],
    node_outputs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if pattern is WorkflowPattern.SINGLE_AGENT:
        return (
            node_outputs.get("agent") or {},
            node_outputs.get("verify") or {"outcome": "fail", "failures": []},
        )
    if pattern is WorkflowPattern.MANAGER_SPECIALISTS:
        manager = next((n for n in agent_nodes if n.config.get("role") == "manager"), agent_nodes[0])
        return (
            node_outputs.get(manager.id) or {},
            node_outputs.get(f"verify_{manager.id}") or {"outcome": "fail", "failures": []},
        )
    last = agent_nodes[-1]
    return (
        node_outputs.get(last.id) or {},
        node_outputs.get(f"verify_{last.id}") or {"outcome": "fail", "failures": []},
    )
