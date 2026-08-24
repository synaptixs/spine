"""Parallel-shape rules for a GraphIR fan-out.

**No shipped profile declares a fan-out** — all three SDLC pipelines are linear chains, so these
rules never fire in production today. They exist because the alternative is worse than an unused
rule: `_check_sequential_shape` only inspects the *agent* condensation, so a fan-out over `tool`
nodes passes validation **without anything having looked at it**. That is a concurrency
capability nobody declared and nothing verifies, and the rules close it before the first graph
relies on one.

That is a deliberately narrow claim. A rule that cannot fire on current input is not doing work
today, and this file says so rather than implying coverage it does not have.
"""

from __future__ import annotations

from orchestrator.ir.graph import Edge, GraphIR, GraphSpec, Node, NodeType, WorkflowPattern
from orchestrator.ir.validator import IRValidator
from orchestrator.registry._common import Metadata


def _meta() -> Metadata:
    return Metadata(id="graph.t", version="0.1.0", description="t")


def _ir(nodes: list[Node], edges: list[Edge]) -> GraphIR:
    return GraphIR(
        metadata=_meta(),
        spec=GraphSpec(
            objective="x",
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            nodes=nodes,
            edges=edges,
        ),
    )


def _agent(node_id: str) -> Node:
    return Node(id=node_id, type=NodeType.AGENT, template_id="agent.t", template_version="0.1.0")


def _tool(node_id: str, template_id: str = "sdlc.investigate") -> Node:
    return Node(id=node_id, type=NodeType.TOOL, template_id=template_id)


# --- fan-out that reconverges -------------------------------------------------------------


async def test_tool_fanout_that_reconverges_passes() -> None:
    """a → (b ∥ c) → d, all deterministic. The shape Phase 4 admits."""
    ir = _ir(
        [
            _agent("n_head"),
            _tool("n_left", "sdlc.investigate"),
            _tool("n_right", "sdlc.rca"),
            _tool("n_join", "sdlc.blast_radius"),
            _agent("n_tail"),
        ],
        [
            Edge(source="n_head", target="n_left"),
            Edge(source="n_head", target="n_right"),
            Edge(source="n_left", target="n_join"),
            Edge(source="n_right", target="n_join"),
            Edge(source="n_join", target="n_tail"),
        ],
    )
    report = await IRValidator().validate(ir)
    assert report.ok, report.failures


async def test_branch_that_never_reconverges_is_refused() -> None:
    """A branch nothing downstream reads is work the run pays for and discards."""
    ir = _ir(
        [
            _agent("n_head"),
            _tool("n_left", "sdlc.investigate"),
            _tool("n_orphan", "sdlc.rca"),
            _agent("n_tail"),
        ],
        [
            Edge(source="n_head", target="n_left"),
            Edge(source="n_head", target="n_orphan"),
            Edge(source="n_left", target="n_tail"),
        ],
    )
    report = await IRValidator().validate(ir)
    assert any(f["rule"] == "parallel_reconvergence" for f in report.failures), report.failures


async def test_fanout_over_a_model_node_is_refused() -> None:
    """Two model calls in flight can each pass the run budget check and jointly overrun it."""
    ir = _ir(
        [
            _agent("n_head"),
            _tool("n_left", "sdlc.investigate"),
            _agent("n_modelled"),
            _tool("n_join", "sdlc.blast_radius"),
        ],
        [
            Edge(source="n_head", target="n_left"),
            Edge(source="n_head", target="n_modelled"),
            Edge(source="n_left", target="n_join"),
            Edge(source="n_modelled", target="n_join"),
        ],
    )
    report = await IRValidator().validate(ir)
    assert any(f["rule"] == "parallel_determinism" for f in report.failures), report.failures
    assert any("n_modelled" in (f["message"] or "") for f in report.failures)


async def test_linear_chain_declares_no_fanout() -> None:
    """The shipped profile shape. The new rules must be silent on it."""
    ir = _ir(
        [_agent("n_a"), _tool("n_b"), _agent("n_c")],
        [Edge(source="n_a", target="n_b"), Edge(source="n_b", target="n_c")],
    )
    report = await IRValidator().validate(ir)
    assert report.ok, report.failures


async def test_shipped_profiles_still_validate() -> None:
    """Phase 4 adds rules; it does not get to break the graphs the SDLC actually runs."""
    from orchestrator.sdlc.profiles import load_profile, profile_names

    validator = IRValidator()
    names = profile_names(None)
    assert names, "no profiles shipped"
    for name in names:
        report = await validator.validate(load_profile(name, None))
        assert report.ok, (name, report.failures)
