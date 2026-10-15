"""Blast-radius regression coverage (C8): what a change should re-test."""

from __future__ import annotations

import pytest

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


# ---- the name fallback (B85, SSPN-170) ---------------------------------------
#
# After the path check, `is_test_node` fell back to a name match with no word boundary, so
# production symbols named `latest`, `contest`, `AgentSpec` or `TestRunner` counted as test code
# (53 of Spine's own 13,564 function/type symbols). The fallback now recognises only the two
# shapes that name a test outside a test directory: a `test_` function, and a CamelCase
# `...Test` / `...Tests` class (C# keeps its test classes in a `Foo.Tests/` project whose path
# the path rule does not match).


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
    ],
)
def test_a_production_name_that_only_looks_like_a_test_is_not_one(name: str) -> None:
    assert not is_test_node(_fn(f"py:app.{name}", name, "src/app/prod.py"))


@pytest.mark.parametrize("name", ["test_foo", "test_", "OrderTest", "OrderTests", "Test2Tests"])
def test_a_test_by_name_outside_a_test_directory_still_is_one(name: str) -> None:
    assert is_test_node(_fn(f"x.{name}", name, "src/lib.rs"))


def test_a_csharp_test_class_in_a_dot_tests_project_is_a_test() -> None:
    assert is_test_node(_fn("cs:Foo.Tests.OrderTests", "OrderTests", "Foo.Tests/OrderTests.cs"))


def test_a_path_alone_still_makes_any_name_a_test() -> None:
    assert is_test_node(_fn("py:tests.helper", "helper", "tests/helpers.py"))
    assert is_test_node(_fn("py:tests.TestRunner", "TestRunner", "tests/test_runner.py"))
