"""Blast-radius regression coverage (C8): what a change should re-test."""

from __future__ import annotations

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.coverage import (
    build_regression_plan,
    is_test_node,
    render_regression_plan_md,
    resolve_target,
)


def _fn(nid: str, name: str, file: str, line: int = 1) -> Node:
    return Node(
        id=nid, kind=NodeKind.FUNCTION, name=name, language="python", provenance=Provenance(file, line)
    )


def _graph() -> FactBatch:
    """core.validate is called by web.handler (untested) and by tests/test_core.test_validate."""
    b = FactBatch()
    validate = _fn("py:core.validate", "validate", "core.py", 10)
    handler = _fn("py:web.handler", "handler", "web.py", 5)
    test_fn = _fn("py:tests.test_core.test_validate", "test_validate", "tests/test_core.py", 3)
    for n in (validate, handler, test_fn):
        b.add_node(n)
    b.add_edge(Edge("py:web.handler", "py:core.validate", EdgeKind.CALLS, Provenance("web.py", 6)))
    b.add_edge(
        Edge(
            "py:tests.test_core.test_validate",
            "py:core.validate",
            EdgeKind.CALLS,
            Provenance("tests/test_core.py", 4),
        )
    )
    return b


def test_is_test_node_by_path_and_name() -> None:
    assert is_test_node(_fn("x", "test_validate", "tests/test_core.py"))
    assert is_test_node(_fn("x", "helper", "src/__tests__/thing.ts"))
    assert not is_test_node(_fn("x", "validate", "core.py"))


def test_plan_splits_covering_tests_from_gaps() -> None:
    store = FactStore(_graph())
    plan = build_regression_plan(store, "py:core.validate")

    assert plan.target_covered is True  # a test transitively exercises validate
    assert any("test_validate" in t for t in plan.covering_tests)
    # web.handler is in the blast radius and NOT reached by any test → a gap
    gaps = [i for i in plan.impacted if not i.covered]
    assert any(i.name == "handler" for i in gaps)


def test_resolve_target_prefers_grounded_function() -> None:
    store = FactStore(_graph())
    assert resolve_target(store, "validate") == "py:core.validate"
    assert resolve_target(store, "nonexistent") is None


def test_render_flags_gaps() -> None:
    md = render_regression_plan_md(build_regression_plan(FactStore(_graph()), "py:core.validate"))
    assert "# Regression coverage" in md
    assert "Regression gaps" in md and "`handler`" in md
    assert "exercised by tests" in md


def test_no_call_graph_is_honest() -> None:
    b = FactBatch()
    b.add_node(_fn("py:core.validate", "validate", "core.py"))  # node but no CALLS edges
    plan = build_regression_plan(FactStore(b), "py:core.validate")
    assert plan.call_graph_available is False
    assert "No call graph" in render_regression_plan_md(plan)
