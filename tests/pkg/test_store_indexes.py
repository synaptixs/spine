"""``FactStore`` is read-only, so the indexes a walk needs are built once per store (B110): a
file's lenses ask ``impact_across`` hundreds of times, and each call used to rescan every edge."""

from __future__ import annotations

from orchestrator.pkg import FactBatch, FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, Node, NodeKind, Provenance


def _store() -> FactStore:
    batch = FactBatch()
    for name in ("a", "b", "c", "d"):
        batch.add_node(Node(f"py:{name}", NodeKind.FUNCTION, name, "python", Provenance(f"{name}.py", 1)))
    batch.add_edge(Edge("py:b", "py:a", EdgeKind.CALLS))
    batch.add_edge(Edge("py:c", "py:b", EdgeKind.CALLS))
    batch.add_edge(Edge("py:d", "py:a", EdgeKind.IMPORTS))
    return FactStore(batch)


def _ids(walk: list[tuple[Node, int]]) -> list[tuple[str, int]]:
    return [(n.id, d) for n, d in walk]


def test_impact_across_answers_the_same_every_time_and_builds_each_index_once() -> None:
    store = _store()
    calls = (EdgeKind.CALLS,)
    assert _ids(store.impact_across("py:a", kinds=calls)) == [("py:b", 1), ("py:c", 2)]
    assert _ids(store.impact_across("py:a", kinds=calls, max_depth=1)) == [("py:b", 1)]
    assert _ids(store.impact_across("py:a")) == [("py:b", 1), ("py:d", 1), ("py:c", 2)]
    assert _ids(store.impact_across("py:a", kinds=calls)) == [("py:b", 1), ("py:c", 2)]
    assert len(store._predecessors) == 2  # one per kind set asked for, not one per call


def test_summary_is_computed_once_and_a_caller_cannot_change_it() -> None:
    store = _store()
    first = store.summary()
    first["nodes"] = -1
    assert store.summary()["nodes"] == 4
    assert store.summary() is not store.summary()
