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


# ---- B140 + B185 (G6 D3, D4): the target is the symbol the caller named, or a refusal ----------


def _two_runs() -> FactStore:
    """Two functions called ``run`` (in classes A and B) and one unique ``validate``."""
    b = FactBatch()
    for n in (
        _fn("py:jobs.A.run", "run", "jobs.py", 3),
        _fn("py:jobs.B.run", "run", "jobs.py", 9),
        _fn("py:core.validate", "validate", "core.py", 10),
    ):
        b.add_node(n)
    return FactStore(b)


def test_a_full_node_id_resolves_to_itself() -> None:
    """B185: ``regression_gaps`` answered ``found: false`` for a full id, which ``blast_radius`` takes."""
    from orchestrator.sdlc.coverage import resolve_change_target

    store = _two_runs()
    assert resolve_change_target(store, "py:jobs.B.run").id == "py:jobs.B.run"
    assert resolve_target(store, "py:jobs.B.run") == "py:jobs.B.run"


def test_a_qualified_name_picks_one_of_many() -> None:
    from orchestrator.sdlc.coverage import resolve_change_target

    assert resolve_change_target(_two_runs(), "A.run").id == "py:jobs.A.run"


def test_an_ambiguous_bare_name_is_refused_with_its_candidates() -> None:
    """B140: a bare name shared by several symbols silently answered for the first one."""
    from orchestrator.sdlc.coverage import resolve_change_target

    res = resolve_change_target(_two_runs(), "run")
    assert res.id is None and res.ambiguous
    assert res.match_count == 2
    assert [n.id for n in res.candidates] == ["py:jobs.A.run", "py:jobs.B.run"]
    assert not res.truncated
    assert resolve_target(_two_runs(), "run") is None


def test_a_unique_bare_name_resolves_as_before() -> None:
    from orchestrator.sdlc.coverage import resolve_change_target

    res = resolve_change_target(_two_runs(), "validate")
    assert (res.id, res.ambiguous, res.match_count) == ("py:core.validate", False, 1)
    missing = resolve_change_target(_two_runs(), "nonexistent")
    assert (missing.id, missing.ambiguous, missing.match_count) == (None, False, 0)


def test_candidates_are_capped_and_the_cap_is_said() -> None:
    from orchestrator.sdlc.coverage import MAX_TARGET_CANDIDATES, resolve_change_target

    b = FactBatch()
    for i in range(MAX_TARGET_CANDIDATES + 3):
        b.add_node(_fn(f"py:m{i:02d}.go", "go", f"m{i:02d}.py"))
    res = resolve_change_target(FactStore(b), "go")
    assert res.match_count == MAX_TARGET_CANDIDATES + 3
    assert len(res.candidates) == MAX_TARGET_CANDIDATES and res.truncated


def test_the_ambiguity_markdown_says_how_to_pick_one() -> None:
    from orchestrator.sdlc.coverage import render_ambiguous_target_md, resolve_change_target

    md = render_ambiguous_target_md("run", resolve_change_target(_two_runs(), "run"))
    assert "`py:jobs.A.run`" in md and "`py:jobs.B.run`" in md
    assert "full node id" in md and "`Class.name`" in md


# ---- D4b / D4c: refuse only what is genuinely shared -------------------------------------------


def _node(nid: str, kind: NodeKind, name: str, file: str, lang: str = "csharp") -> Node:
    return _marked(Node(id=nid, kind=kind, name=name, language=lang, provenance=Provenance(file, 1)))


def test_a_class_and_its_own_constructor_are_one_symbol() -> None:
    """C#/Java/C++ give the constructor the class's name; `Cart` named one symbol on develop, which
    answered for the constructor, and still does (D4b)."""
    from orchestrator.sdlc.coverage import resolve_change_target

    b = FactBatch()
    for n in (
        _node("csharp:Shop.Cart", NodeKind.TYPE, "Cart", "Cart.cs"),
        _node("csharp:Shop.Cart.Cart", NodeKind.FUNCTION, "Cart", "Cart.cs"),
        _node("cpp:Basket", NodeKind.TYPE, "Basket", "basket.h", "cpp"),
        _node("cpp:Basket::Basket", NodeKind.FUNCTION, "Basket", "basket.cpp", "cpp"),
    ):
        b.add_node(n)
    store = FactStore(b)
    assert resolve_change_target(store, "Cart").id == "csharp:Shop.Cart.Cart"
    assert resolve_change_target(store, "Basket").id == "cpp:Basket::Basket"


def test_one_candidate_in_the_typed_case_answers() -> None:
    """`Handler` (one class) against 58 `handler` functions was refused on case alone (D4b)."""
    from orchestrator.sdlc.coverage import resolve_change_target

    b = FactBatch()
    b.add_node(_node("py:web.Handler", NodeKind.TYPE, "Handler", "web.py", "python"))
    for i in range(3):
        b.add_node(_fn(f"py:m{i}.handler", "handler", f"m{i}.py"))
    store = FactStore(b)
    assert resolve_change_target(store, "Handler").id == "py:web.Handler"
    lower = resolve_change_target(store, "handler")
    assert lower.ambiguous and lower.match_count == 3
    assert [n.id for n in lower.candidates] == ["py:m0.handler", "py:m1.handler", "py:m2.handler"]


def test_test_doubles_do_not_make_a_product_symbol_ambiguous() -> None:
    """One product `TestRunner` plus a same-named double in a test file names the product one (D4c)."""
    from orchestrator.sdlc.coverage import resolve_change_target

    b = FactBatch()
    b.add_node(
        _node("py:sdlc.contracts.TestRunner", NodeKind.TYPE, "TestRunner", "sdlc/contracts.py", "python")
    )
    b.add_node(_node("py:tests.fakes.TestRunner", NodeKind.TYPE, "TestRunner", "tests/fakes.py", "python"))
    res = resolve_change_target(FactStore(b), "TestRunner")
    assert res.id == "py:sdlc.contracts.TestRunner"
    assert res.match_count == 2  # the double is still counted, for honesty


def test_two_product_symbols_and_a_double_are_refused_with_all_three() -> None:
    """D4c narrows only to a single product candidate; otherwise every candidate is listed."""
    from orchestrator.sdlc.coverage import resolve_change_target

    b = FactBatch()
    for n in (
        _fn("py:a.run", "run", "a.py"),
        _fn("py:b.run", "run", "b.py"),
        _fn("py:tests.test_x.run", "run", "tests/test_x.py"),
    ):
        b.add_node(n)
    res = resolve_change_target(FactStore(b), "run")
    assert res.ambiguous and res.match_count == 3
    assert [n.id for n in res.candidates] == ["py:a.run", "py:b.run", "py:tests.test_x.run"]
