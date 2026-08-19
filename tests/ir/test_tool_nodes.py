"""Deterministic `tool` nodes: resolution against the in-process registry, and the chain
shape that having them between agents produces (Phase 1 of the GraphIR SDLC workflow)."""

from __future__ import annotations

import pytest

from orchestrator.ir.graph import Edge, GraphIR, GraphSpec, Node, NodeType, WorkflowPattern
from orchestrator.ir.validator import IRValidator
from orchestrator.registry._common import Metadata
from orchestrator.runtime.tool_registry import ToolError, ToolRegistry, digest_of


def _ir(nodes: list[Node], edges: list[tuple[str, str]]) -> GraphIR:
    return GraphIR(
        metadata=Metadata(id="graph.t", version="0.1.0", description="t"),
        spec=GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            nodes=nodes,
            edges=[Edge(source=s, target=t) for s, t in edges],
        ),
    )


def _failures(report: object, rule: str) -> list[dict[str, str | None]]:
    return [f for f in report.failures if f["rule"] == rule]  # type: ignore[attr-defined]


async def test_a_tool_node_naming_a_registered_tool_resolves() -> None:
    ir = _ir(
        [
            Node(id="n_a", type=NodeType.AGENT),
            Node(id="n_t", type=NodeType.TOOL, template_id="sdlc.investigate"),
            Node(id="n_b", type=NodeType.AGENT),
        ],
        [("n_a", "n_t"), ("n_t", "n_b")],
    )
    assert (await IRValidator().validate(ir)).ok


async def test_a_tool_node_naming_nothing_is_a_failure() -> None:
    """``template_id`` is optional on the model because agent nodes may omit it. A tool node
    without one names no callable at all, which is unrunnable rather than merely unresolved."""
    ir = _ir(
        [
            Node(id="n_a", type=NodeType.AGENT),
            Node(id="n_t", type=NodeType.TOOL),
            Node(id="n_b", type=NodeType.AGENT),
        ],
        [("n_a", "n_t"), ("n_t", "n_b")],
    )
    report = await IRValidator().validate(ir)
    assert not report.ok
    assert _failures(report, "tool_unresolved")


async def test_an_unregistered_tool_is_caught_without_a_database() -> None:
    """The point of the in-process registry: `autorun` runs with no registry service, so this
    check must hold with ``session=None`` — which is how every SDLC run validates its graph."""
    ir = _ir(
        [
            Node(id="n_a", type=NodeType.AGENT),
            Node(id="n_t", type=NodeType.TOOL, template_id="sdlc.does_not_exist"),
            Node(id="n_b", type=NodeType.AGENT),
        ],
        [("n_a", "n_t"), ("n_t", "n_b")],
    )
    report = await IRValidator().validate(ir, session=None)
    assert not report.ok
    assert _failures(report, "tool_unresolved")


async def test_agents_separated_by_tools_are_still_a_linear_chain() -> None:
    """The sequential shape check used to look only at direct agent→agent edges. With a tool
    node between them every agent had in-degree and out-degree zero, so all four were heads and
    all four were tails, and the SDLC profile could not validate at all."""
    ir = _ir(
        [
            Node(id="n_a", type=NodeType.AGENT),
            Node(id="n_t1", type=NodeType.TOOL, template_id="sdlc.investigate"),
            Node(id="n_t2", type=NodeType.TOOL, template_id="sdlc.rca"),
            Node(id="n_b", type=NodeType.AGENT),
        ],
        [("n_a", "n_t1"), ("n_t1", "n_t2"), ("n_t2", "n_b")],
    )
    assert (await IRValidator().validate(ir)).ok


async def test_branching_through_tools_is_still_branching() -> None:
    """Condensation must not launder a fork into a chain: one agent reaching two agents through
    tool nodes is exactly the shape the sequential rule exists to reject."""
    ir = _ir(
        [
            Node(id="n_a", type=NodeType.AGENT),
            Node(id="n_t1", type=NodeType.TOOL, template_id="sdlc.investigate"),
            Node(id="n_t2", type=NodeType.TOOL, template_id="sdlc.rca"),
            Node(id="n_b", type=NodeType.AGENT),
            Node(id="n_c", type=NodeType.AGENT),
        ],
        [("n_a", "n_t1"), ("n_a", "n_t2"), ("n_t1", "n_b"), ("n_t2", "n_c")],
    )
    report = await IRValidator().validate(ir)
    assert not report.ok
    assert _failures(report, "sequential_shape")


# --------------------------------------------------------------------------------------
# The registry itself
# --------------------------------------------------------------------------------------


async def test_the_digest_ignores_key_order() -> None:
    """Python dicts preserve insertion order, so two runs that computed identical facts in a
    different order would digest differently and read as a divergence in the pipeline."""
    assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})


async def test_a_registry_runs_sync_and_async_tools_alike() -> None:
    registry = ToolRegistry()
    registry.register("t.sync", lambda **_: {"v": 1})

    async def _async(**_: object) -> dict[str, int]:
        return {"v": 1}

    registry.register("t.async", _async)
    assert (await registry.run("t.sync")).digest == (await registry.run("t.async")).digest


async def test_registering_a_different_callable_under_a_used_name_raises() -> None:
    """Idempotent for the same function object — a lazy import racing a direct one is normal —
    but a genuine redefinition would silently change what a graph node means."""
    registry = ToolRegistry()
    fn = lambda **_: 1  # noqa: E731
    registry.register("t.x", fn)
    registry.register("t.x", fn)
    with pytest.raises(ToolError):
        registry.register("t.x", lambda **_: 2)


async def test_an_unknown_tool_names_the_ones_that_exist() -> None:
    registry = ToolRegistry()
    registry.register("t.known", lambda **_: 1)
    with pytest.raises(ToolError, match="t.known"):
        await registry.run("t.missing")
