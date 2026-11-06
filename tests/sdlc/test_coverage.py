"""Blast-radius regression coverage (C8): what a change should re-test."""

from __future__ import annotations

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import mark_tests
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.coverage import (
    build_regression_plan,
    render_regression_plan_md,
    resolve_target,
)


def _fn(nid: str, name: str, file: str, line: int = 1) -> Node:
    """A function node as extraction hands it: ``test`` set by `mark_tests` from its path."""
    node = Node(
        id=nid, kind=NodeKind.FUNCTION, name=name, language="python", provenance=Provenance(file, line)
    )
    return _marked(node)


def _marked(node: Node, *, front_end_marked: bool = False) -> Node:
    batch = FactBatch()
    batch.add_node(node)
    return mark_tests(batch, {node.id} if front_end_marked else ()).nodes[0]


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


def test_a_test_is_the_graph_fact_set_from_the_path() -> None:
    assert _fn("x", "test_validate", "tests/test_core.py").test
    assert _fn("x", "helper", "src/__tests__/thing.ts").test
    assert not _fn("x", "validate", "core.py").test


def test_perl_tap_file_is_a_covering_test() -> None:
    batch = FactBatch()
    target = _fn("perl:Shop.Cart.subtotal", "subtotal", "lib/Shop/Cart.pm")
    caller = _fn("perl:cart_check.main", "main", "t/10-cart.t")
    batch.add_node(target)
    batch.add_node(caller)
    batch.add_edge(Edge(caller.id, target.id, EdgeKind.CALLS, Provenance("t/10-cart.t", 3)))
    plan = build_regression_plan(FactStore(batch), target.id)
    assert plan.target_covered
    assert plan.covering_tests == ["main @ t/10-cart.t:1"]


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


# ---- the name fallback is gone (B85, then B123) ------------------------------------------------
#
# After the path check, coverage fell back to a name match. B85 narrowed it to a `test_` function
# and a CamelCase `...Test(s)` class; B123 found that even those made flask's public
# `Flask.test_client` a test, so what it called read "exercised by tests". Coverage now reads
# `Node.test`, which the name never sets: a test outside a test path is one the language marks in
# the file (Rust's `#[test]`), and the front-end puts that mark on the node.


@pytest.mark.parametrize(
    "name",
    [
        "latest",
        "contest",
        "attest",
        "testify",
        "testimonials_view",
        "inspec",
        "AgentSpec",
        "ToolSpec",
        "_build_spec",
        "TestRunner",
        "TestEnvironment",
        "_run_tests",
        "_weakens_test",
        "author_tests",
        "get_user",
        "test_client",  # B123: flask's public API
        "test_foo",
        "OrderTest",
        "OrderTests",
    ],
)
def test_a_production_name_that_only_looks_like_a_test_is_not_one(name: str) -> None:
    assert not _fn(f"py:app.{name}", name, "src/app/prod.py").test


@pytest.mark.parametrize("name", ["test_foo", "parses_empty_input", "OrderTests"])
def test_a_test_the_front_end_marked_in_the_file_is_one_whatever_its_name(name: str) -> None:
    node = Node(f"rust:crate.{name}", NodeKind.FUNCTION, name, "rust", Provenance("src/lib.rs", 1))
    assert _marked(node, front_end_marked=True).test
    assert not _marked(node).test


def test_a_csharp_test_class_in_a_dot_tests_project_is_a_test() -> None:
    assert _fn("cs:Foo.Tests.OrderTests", "OrderTests", "Foo.Tests/OrderTests.cs").test


def test_a_path_alone_still_makes_any_name_a_test() -> None:
    assert _fn("py:tests.helper", "helper", "tests/helpers.py").test
    assert _fn("py:tests.TestRunner", "TestRunner", "tests/test_runner.py").test
