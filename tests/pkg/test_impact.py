"""FactStore.impact_of — transitive blast radius (Bet 3)."""

from __future__ import annotations

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore


def _fn(name: str, line: int) -> Node:
    return Node(
        id=f"py:m.{name}",
        kind=NodeKind.FUNCTION,
        name=name,
        language="python",
        provenance=Provenance("m.py", line),
    )


def _store(edges: list[Edge], nodes: list[Node]) -> FactStore:
    batch = FactBatch()
    for n in nodes:
        batch.add_node(n)
    for e in edges:
        batch.add_edge(e)
    return FactStore(batch)


def test_impact_is_transitive_callers_with_depth() -> None:
    # main → helper → leaf  (CALLS: src=caller, dst=callee)
    main, helper, leaf = _fn("main", 1), _fn("helper", 10), _fn("leaf", 20)
    edges = [
        Edge(main.id, helper.id, EdgeKind.CALLS, Provenance("m.py", 2)),
        Edge(helper.id, leaf.id, EdgeKind.CALLS, Provenance("m.py", 11)),
    ]
    store = _store(edges, [main, helper, leaf])
    impacted = store.impact_of(leaf.id)
    by_name = {n.name: d for n, d in impacted}
    assert by_name == {"helper": 1, "main": 2}  # changing leaf affects helper (1 hop) + main (2)


def test_impact_respects_max_depth() -> None:
    main, helper, leaf = _fn("main", 1), _fn("helper", 10), _fn("leaf", 20)
    edges = [
        Edge(main.id, helper.id, EdgeKind.CALLS, Provenance("m.py", 2)),
        Edge(helper.id, leaf.id, EdgeKind.CALLS, Provenance("m.py", 11)),
    ]
    store = _store(edges, [main, helper, leaf])
    impacted = store.impact_of(leaf.id, max_depth=1)
    assert [n.name for n, _ in impacted] == ["helper"]  # main is 2 hops away → excluded


def test_impact_handles_cycle_without_looping() -> None:
    a, b = _fn("a", 1), _fn("b", 2)
    edges = [
        Edge(a.id, b.id, EdgeKind.CALLS, Provenance("m.py", 1)),
        Edge(b.id, a.id, EdgeKind.CALLS, Provenance("m.py", 2)),
    ]
    store = _store(edges, [a, b])
    impacted = store.impact_of(a.id)
    assert {n.name for n, _ in impacted} == {"b"}  # terminates, no infinite loop


def test_impact_empty_for_uncalled_symbol() -> None:
    leaf = _fn("leaf", 1)
    assert _store([], [leaf]).impact_of(leaf.id) == []


def test_impact_across_unions_kinds_and_filters() -> None:
    # n0 has three incoming edges of different kinds; c2 chains two CALLS hops.
    n0, c1, c2, m1, e1 = (_fn(x, i) for i, x in enumerate(("n0", "c1", "c2", "m1", "e1")))
    edges = [
        Edge("py:m.c1", "py:m.n0", EdgeKind.CALLS),
        Edge("py:m.c2", "py:m.c1", EdgeKind.CALLS),  # transitive caller
        Edge("py:m.m1", "py:m.n0", EdgeKind.IMPORTS),
        Edge("py:m.e1", "py:m.n0", EdgeKind.REFERENCES),
    ]
    store = _store(edges, [n0, c1, c2, m1, e1])

    across = {n.id for n, _ in store.impact_across("py:m.n0")}
    assert across == {"py:m.c1", "py:m.c2", "py:m.m1", "py:m.e1"}  # all three layers

    calls_only = {n.id for n, _ in store.impact_across("py:m.n0", kinds=(EdgeKind.CALLS,))}
    assert calls_only == {"py:m.c1", "py:m.c2"}  # data + import layers filtered out
    # the CALLS-filtered composed query matches the original CALLS-only impact_of
    assert calls_only == {n.id for n, _ in store.impact_of("py:m.n0")}
