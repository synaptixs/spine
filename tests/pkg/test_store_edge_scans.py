"""B102 / SSPN-237: the blast radius must not rescan every edge for every node it reaches.

R2's sympy-16886 and the G2 re-run's sympy-15875 stopped producing output right after
`[research] issue type` and were killed at the 1,800 s wall clock, with two model requests and no model call
after them. The research step is model-free; replayed on the stored graph (44,130 nodes, 348,598 edges) the
hang was in `sdlc.blast_radius` -> `_hotspots` -> `FactStore.impact_of` -> `injection_reach_of`. `impact_of`
asks `exposers_of`, `consumers_of` and `injection_reach_of` about every node it reaches, and each one walked
the whole edge list, so a symbol with thousands of transitive callers cost thousands of full scans of ~350,000
edges. `callers_of` and `children_of` were already indexed for the same reason; these three were not.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from orchestrator.pkg import Edge, EdgeKind, FactBatch, FactStore, Node, NodeKind, Provenance


class _CountingEdges(list[Edge]):
    """An edge list that counts how many times something walks all of it."""

    walks = 0

    def __iter__(self) -> Iterator[Edge]:
        type(self).walks += 1
        return super().__iter__()


def _fn(node_id: str) -> Node:
    return Node(node_id, NodeKind.FUNCTION, node_id.rsplit(".", 1)[-1], "python", Provenance("m.py", 1))


def _chain_store(length: int, filler: int) -> FactStore:
    """f0 <- f1 <- f2 ... (each calls the one before), plus unrelated edges to make a scan expensive."""
    batch = FactBatch()
    for i in range(length):
        batch.add_node(_fn(f"py:m.f{i}"))
    for i in range(1, length):
        batch.add_edge(Edge(f"py:m.f{i}", f"py:m.f{i - 1}", EdgeKind.CALLS, Provenance("m.py", i)))
    for j in range(filler):
        batch.add_edge(Edge(f"py:m.x{j}", f"py:m.y{j}", EdgeKind.IMPORTS, Provenance("m.py", j)))
    store = FactStore(batch)
    store._edges = _CountingEdges(store._edges)
    return store


def test_the_transitive_walk_does_not_rescan_the_edge_list_per_node() -> None:
    _CountingEdges.walks = 0
    store = _chain_store(length=60, filler=2000)

    reached = store.impact_of("py:m.f0", max_depth=60)

    assert len(reached) == 59
    # One pass per lazily built index is fine; one pass per reached node is the defect (it was 3 per node).
    assert _CountingEdges.walks <= 8, _CountingEdges.walks


def test_repeated_lookups_are_answered_from_an_index() -> None:
    store = _chain_store(length=5, filler=500)
    store.exposers_of("py:m.f0")
    store.consumers_of("py:m.f0")
    store.injection_reach_of("py:m.f0")
    _CountingEdges.walks = 0

    for _ in range(50):
        store.exposers_of("py:m.f1")
        store.consumers_of("py:m.f1")
        store.injection_reach_of("py:m.f1")

    assert _CountingEdges.walks == 0


def _graph_with(kind: EdgeKind, src: str, dst: str) -> FactStore:
    batch = FactBatch()
    for node_id in ("py:a.h", "py:a.t", "py:a.u", "py:a.m1", "py:a.m2"):
        batch.add_node(_fn(node_id))
    batch.add_edge(Edge(src, dst, kind, Provenance("a.py", 1)))
    return FactStore(batch)


def test_exposers_are_the_sources_of_exposes_edges_into_the_node() -> None:
    store = _graph_with(EdgeKind.EXPOSES, "py:a.h", "py:a.t")

    assert [n.id for n in store.exposers_of("py:a.t")] == ["py:a.h"]
    assert store.exposers_of("py:a.h") == []


def test_consumers_are_the_sources_of_consumes_edges_into_the_node() -> None:
    store = _graph_with(EdgeKind.CONSUMES, "py:a.h", "py:a.t")

    assert [n.id for n in store.consumers_of("py:a.t")] == ["py:a.h"]
    assert [n.id for n in store.endpoints_called_by("py:a.h")] == ["py:a.t"]


def test_an_edge_to_a_node_the_graph_does_not_hold_is_ignored() -> None:
    store = _graph_with(EdgeKind.EXPOSES, "py:a.ghost", "py:a.t")

    assert store.exposers_of("py:a.t") == []


def test_injection_reach_is_the_provided_type_and_its_members() -> None:
    batch = FactBatch()
    for node_id in ("py:a.impl", "py:a.iface", "py:a.iface.m1", "py:a.iface.m2"):
        batch.add_node(_fn(node_id))
    batch.add_edge(Edge("py:a.impl", "py:a.iface", EdgeKind.PROVIDES, Provenance("a.py", 1)))
    batch.add_edge(Edge("py:a.iface", "py:a.iface.m1", EdgeKind.CONTAINS, Provenance("a.py", 2)))
    batch.add_edge(Edge("py:a.iface", "py:a.iface.m2", EdgeKind.CONTAINS, Provenance("a.py", 3)))
    store = FactStore(batch)

    assert [n.id for n in store.injection_reach_of("py:a.impl")] == [
        "py:a.iface",
        "py:a.iface.m1",
        "py:a.iface.m2",
    ]
    assert store.injection_reach_of("py:a.iface") == []


def test_edges_keep_the_order_they_were_added_in() -> None:
    batch = FactBatch()
    for node_id in ("py:a.t", "py:a.h1", "py:a.h2", "py:a.h3"):
        batch.add_node(_fn(node_id))
    for name in ("h3", "h1", "h2"):
        batch.add_edge(Edge(f"py:a.{name}", "py:a.t", EdgeKind.EXPOSES, Provenance("a.py", 1)))
    store = FactStore(batch)

    assert [n.id for n in store.exposers_of("py:a.t")] == ["py:a.h3", "py:a.h1", "py:a.h2"]


@pytest.mark.parametrize("name", ["exposers_of", "consumers_of", "injection_reach_of"])
def test_a_node_with_no_such_edges_answers_empty(name: str) -> None:
    store = _chain_store(length=3, filler=10)

    assert getattr(store, name)("py:m.f1") == []
