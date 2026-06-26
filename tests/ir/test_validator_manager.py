"""IR validator coverage for the manager_specialists pattern."""

from __future__ import annotations

from orchestrator.ir.graph import GraphIR, GraphSpec, Node, NodeType, WorkflowPattern
from orchestrator.ir.validator import IRValidator
from orchestrator.registry._common import Metadata


def _meta() -> Metadata:
    return Metadata(id="graph.t", version="0.1.0", description="t")


def _manager_ir(*, manager_count: int, specialist_count: int) -> GraphIR:
    nodes: list[Node] = []
    for i in range(manager_count):
        nodes.append(
            Node(
                id=f"n_mgr{i}",
                type=NodeType.AGENT,
                template_id="agent.manager",
                template_version="0.1.0",
                config={"role": "manager"},
            )
        )
    for i in range(specialist_count):
        nodes.append(
            Node(
                id=f"n_spec{i}",
                type=NodeType.AGENT,
                template_id="agent.specialist",
                template_version="0.1.0",
                config={"role": "specialist"},
            )
        )
    return GraphIR(
        metadata=_meta(),
        spec=GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.MANAGER_SPECIALISTS,
            nodes=nodes,
        ),
    )


async def test_one_manager_two_specialists_passes() -> None:
    report = await IRValidator().validate(_manager_ir(manager_count=1, specialist_count=2))
    assert report.ok


async def test_multiple_managers_flagged() -> None:
    report = await IRValidator().validate(_manager_ir(manager_count=2, specialist_count=2))
    assert any(f["rule"] == "manager_specialists_shape" for f in report.failures)


async def test_no_manager_flagged() -> None:
    report = await IRValidator().validate(_manager_ir(manager_count=0, specialist_count=2))
    assert any(f["rule"] == "manager_specialists_shape" for f in report.failures)


async def test_single_specialist_flagged() -> None:
    report = await IRValidator().validate(_manager_ir(manager_count=1, specialist_count=1))
    assert any(
        f["rule"] == "manager_specialists_shape" and "two specialists" in (f["message"] or "")
        for f in report.failures
    )
