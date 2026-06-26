from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.ir.graph import (
    ApprovalPoint,
    Edge,
    GraphIR,
    GraphSpec,
    Node,
    NodeType,
    WorkflowPattern,
)
from orchestrator.registry._common import Metadata


def _meta() -> Metadata:
    return Metadata(id="graph.test", version="0.1.0", description="x")


def _single_node_spec() -> GraphSpec:
    return GraphSpec(
        objective="Summarize three articles.",
        workflow_pattern=WorkflowPattern.SINGLE_AGENT,
        nodes=[Node(id="n_agent", type=NodeType.AGENT, template_id="research.summarizer")],
    )


def test_valid_single_agent_ir() -> None:
    ir = GraphIR(metadata=_meta(), spec=_single_node_spec())
    assert len(ir.spec.nodes) == 1


def test_valid_sequential_ir() -> None:
    spec = GraphSpec(
        objective="Analyze then write.",
        workflow_pattern=WorkflowPattern.SEQUENTIAL,
        nodes=[
            Node(id="n_analyst", type=NodeType.AGENT),
            Node(id="n_writer", type=NodeType.AGENT),
        ],
        edges=[Edge(source="n_analyst", target="n_writer")],
    )
    GraphIR(metadata=_meta(), spec=spec)


def test_duplicate_node_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate node ids"):
        GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            nodes=[
                Node(id="n_a", type=NodeType.AGENT),
                Node(id="n_a", type=NodeType.AGENT),
            ],
        )


def test_edge_to_unknown_node_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown node"):
        GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            nodes=[Node(id="n_a", type=NodeType.AGENT)],
            edges=[Edge(source="n_a", target="n_b")],
        )


def test_self_loop_edge_rejected() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        Edge(source="n_a", target="n_a")


def test_approval_point_unknown_node_rejected() -> None:
    with pytest.raises(ValidationError, match="Approval point"):
        GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SINGLE_AGENT,
            nodes=[Node(id="n_a", type=NodeType.AGENT)],
            approval_points=[ApprovalPoint(before_node="n_missing")],
        )


def test_node_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        Node(id="bad-id", type=NodeType.AGENT)


def test_empty_node_list_rejected() -> None:
    with pytest.raises(ValidationError):
        GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SINGLE_AGENT,
            nodes=[],
        )
