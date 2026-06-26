"""IR validator coverage for the sequential pattern."""

from __future__ import annotations

from orchestrator.ir.graph import Edge, GraphIR, GraphSpec, Node, NodeType, WorkflowPattern
from orchestrator.ir.validator import IRValidator
from orchestrator.registry._common import Metadata


def _meta() -> Metadata:
    return Metadata(id="graph.t", version="0.1.0", description="t")


def _seq_ir(*node_ids: str, edges: list[tuple[str, str]] | None = None) -> GraphIR:
    nodes = [Node(id=nid, type=NodeType.AGENT) for nid in node_ids]
    edge_list = [Edge(source=s, target=t) for s, t in (edges or [])]
    return GraphIR(
        metadata=_meta(),
        spec=GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            nodes=nodes,
            edges=edge_list,
        ),
    )


async def test_two_node_chain_passes() -> None:
    report = await IRValidator().validate(_seq_ir("n_a", "n_b", edges=[("n_a", "n_b")]))
    assert report.ok


async def test_three_node_chain_passes() -> None:
    report = await IRValidator().validate(
        _seq_ir("n_a", "n_b", "n_c", edges=[("n_a", "n_b"), ("n_b", "n_c")])
    )
    assert report.ok


async def test_single_agent_in_sequential_pattern_flagged() -> None:
    report = await IRValidator().validate(_seq_ir("n_only"))
    assert any(f["rule"] == "sequential_shape" for f in report.failures)


async def test_branching_node_flagged() -> None:
    report = await IRValidator().validate(
        _seq_ir(
            "n_a",
            "n_b",
            "n_c",
            edges=[("n_a", "n_b"), ("n_a", "n_c")],  # fan-out from n_a
        )
    )
    assert any(
        f["rule"] == "sequential_shape" and "branching" in (f["message"] or "") for f in report.failures
    )


async def test_disconnected_components_flagged() -> None:
    report = await IRValidator().validate(
        _seq_ir("n_a", "n_b", "n_c", "n_d", edges=[("n_a", "n_b"), ("n_c", "n_d")])
    )
    assert any(f["rule"] == "sequential_shape" and "head" in (f["message"] or "") for f in report.failures)
