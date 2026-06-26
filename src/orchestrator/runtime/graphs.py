"""LangGraph graph builders for the single_agent and sequential patterns.

Sprint 10 introduces an optional per-edge ``VerifierChain`` slot. Callers
pass a ``chain_factory(template, target_node, verifier_id) -> VerifierChain``
to wire the chain into each (agent → chain) edge. The default behaviour
preserves Sprint 5's contract: when no factory is supplied, the existing
``SchemaVerifierNode`` runs alone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from orchestrator.core.llm import LLMClient
from orchestrator.core.state import OrchestratorState
from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.runtime.agent_node import SingleAgentNode
from orchestrator.runtime.artifacts import ArtifactStore
from orchestrator.runtime.chain_node import AuditLogger, VerifierChainNode
from orchestrator.runtime.failure_dispatch import FailurePolicy
from orchestrator.runtime.post_conditions import MinConfidenceRule, PostCondition
from orchestrator.runtime.verifier import SchemaVerifierNode
from orchestrator.runtime.verifiers import (
    ConfidenceVerifier,
    EvidenceVerifier,
    GlossaryVerifier,
    PolicyVerifier,
    VerifierChain,
)

# Factory signature: ``(template, target_node, verifier_id) -> VerifierChain``.
ChainFactory = Callable[[AgentTemplate, str, str], VerifierChain]


def default_chain_factory(template: AgentTemplate, target_node: str, verifier_id: str) -> VerifierChain:
    """Confidence + evidence + policy. Schema check stays on its own node.

    Per the spec (10.6): "Default: schema + confidence + evidence + policy".
    SchemaVerifier still runs on its own node so the audit row distinguishes
    "the agent's output is structurally invalid" from "the verifier chain
    rejected a semantic claim".
    """
    _ = (template, target_node)  # used only for future per-template tuning
    confidence_threshold = float(template.spec.constraints.get("min_confidence", 0.7))
    evidence_tolerance = float(template.spec.constraints.get("evidence_tolerance", 0.01))
    return VerifierChain(
        [
            ConfidenceVerifier(threshold=confidence_threshold),
            EvidenceVerifier(tolerance=evidence_tolerance),
            PolicyVerifier(),
            GlossaryVerifier(),
        ],
        chain_id=verifier_id,
    )


def build_single_agent_graph(
    *,
    template: AgentTemplate,
    llm: LLMClient,
    checkpointer: Any = None,
    chain_factory: ChainFactory | None = None,
    failure_policy: FailurePolicy | None = None,
    artifact_store: ArtifactStore | None = None,
    audit_logger: AuditLogger | None = None,
) -> Any:
    """Return a compiled LangGraph for the ``single_agent`` workflow pattern.

    Topology:
      START -> agent -> verify (schema) -> [chain] -> END

    The verifier-chain node is inserted only when ``chain_factory`` is set.
    Without it, the graph is byte-identical to Sprint 5's two-node shape.
    """
    builder: StateGraph[OrchestratorState] = StateGraph(OrchestratorState)
    builder.add_node("agent", SingleAgentNode(template, llm))  # type: ignore[type-var]
    builder.add_node("verify", SchemaVerifierNode(template, target_node="agent"))  # type: ignore[type-var]
    builder.add_edge(START, "agent")
    builder.add_edge("agent", "verify")

    if chain_factory is None:
        builder.add_edge("verify", END)
    else:
        chain_node_id = "chain_agent"
        chain = chain_factory(template, "agent", chain_node_id)
        builder.add_node(
            chain_node_id,
            VerifierChainNode(  # type: ignore[type-var]
                template=template,
                chain=chain,
                target_node="agent",
                verifier_id=chain_node_id,
                policy=failure_policy,
                artifact_store=artifact_store,
                audit_logger=audit_logger,
            ),
        )
        builder.add_edge("verify", chain_node_id)
        builder.add_edge(chain_node_id, END)

    return builder.compile(checkpointer=checkpointer)


@dataclass(frozen=True)
class SequentialStep:
    """One stage in a sequential workflow.

    ``node_id`` is the LangGraph node id and the key under which this step's
    output lands in ``state.node_outputs``. ``inputs_from`` maps each declared
    input field of the template to a dotted state path; values default to
    ``task_metadata.<field_name>`` when omitted.

    ``post_conditions`` and ``min_confidence`` decorate the step's terminal
    schema verifier — failures of either land in the same
    ``node_outputs.verify_<step_id>`` record alongside schema failures.
    """

    node_id: str
    template: AgentTemplate
    inputs_from: dict[str, str] = field(default_factory=dict)
    post_conditions: list[PostCondition] = field(default_factory=list)
    min_confidence: MinConfidenceRule | None = None


def build_sequential_graph(
    *,
    steps: list[SequentialStep],
    llm: LLMClient,
    checkpointer: Any = None,
    chain_factory: ChainFactory | None = None,
    failure_policy: FailurePolicy | None = None,
    artifact_store: ArtifactStore | None = None,
    audit_logger: AuditLogger | None = None,
) -> Any:
    """Return a compiled LangGraph for the ``sequential`` workflow pattern.

    Each step is an (agent, schema-verifier, [chain]) triple. The chain
    node only inserts when ``chain_factory`` is supplied; otherwise the
    Sprint 8 shape is preserved exactly.
    """
    if not steps:
        raise ValueError("build_sequential_graph: at least one SequentialStep is required")

    ids = [s.node_id for s in steps]
    if len(ids) != len(set(ids)):
        raise ValueError(f"sequential graph: duplicate step node_ids in {ids}")

    builder: StateGraph[OrchestratorState] = StateGraph(OrchestratorState)
    prior_terminal_id: str | None = None

    for step in steps:
        verifier_id = f"verify_{step.node_id}"
        builder.add_node(
            step.node_id,
            SingleAgentNode(  # type: ignore[type-var]
                step.template,
                llm,
                node_id=step.node_id,
                inputs_from=step.inputs_from,
            ),
        )
        builder.add_node(
            verifier_id,
            SchemaVerifierNode(  # type: ignore[type-var]
                step.template,
                target_node=step.node_id,
                verifier_id=verifier_id,
                post_conditions=step.post_conditions,
                min_confidence=step.min_confidence,
            ),
        )
        builder.add_edge(START if prior_terminal_id is None else prior_terminal_id, step.node_id)
        builder.add_edge(step.node_id, verifier_id)
        terminal_id = verifier_id

        if chain_factory is not None:
            chain_node_id = f"chain_{step.node_id}"
            chain = chain_factory(step.template, step.node_id, chain_node_id)
            builder.add_node(
                chain_node_id,
                VerifierChainNode(  # type: ignore[type-var]
                    template=step.template,
                    chain=chain,
                    target_node=step.node_id,
                    verifier_id=chain_node_id,
                    policy=failure_policy,
                    artifact_store=artifact_store,
                    audit_logger=audit_logger,
                ),
            )
            builder.add_edge(verifier_id, chain_node_id)
            terminal_id = chain_node_id

        prior_terminal_id = terminal_id

    assert prior_terminal_id is not None
    builder.add_edge(prior_terminal_id, END)
    return builder.compile(checkpointer=checkpointer)
