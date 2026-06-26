"""PKG Layer 1: fact de-dup + the grounded-query surface."""

from __future__ import annotations

from orchestrator.pkg import (
    CallSite,
    Edge,
    EdgeKind,
    FactBatch,
    FactStore,
    Node,
    NodeKind,
    Provenance,
)


def _grounded(node_id: str, name: str) -> Node:
    return Node(node_id, NodeKind.FUNCTION, name, "python", Provenance("a.py", 3))


def test_grounded_node_upgrades_external_placeholder() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:a.f", NodeKind.FUNCTION, "f", external=True))
    batch.add_node(_grounded("py:a.f", "f"))  # later, grounded
    assert len(batch.nodes) == 1
    assert batch.nodes[0].grounded


def test_external_does_not_clobber_grounded() -> None:
    batch = FactBatch()
    batch.add_node(_grounded("py:a.f", "f"))
    batch.add_node(Node("py:a.f", NodeKind.FUNCTION, "f", external=True))  # later, weaker
    assert batch.nodes[0].grounded


def test_edges_dedup_on_key() -> None:
    batch = FactBatch()
    e = Edge("a", "b", EdgeKind.CALLS, Provenance("a.py", 1))
    batch.add_edge(e)
    batch.add_edge(e)
    assert len(batch.edges) == 1


def _store() -> FactStore:
    batch = FactBatch()
    batch.add_node(_grounded("py:m.caller", "caller"))
    batch.add_node(_grounded("py:m.target", "target"))
    batch.add_node(_grounded("py:m.other", "other"))
    batch.add_edge(Edge("py:m.caller", "py:m.target", EdgeKind.CALLS, Provenance("m.py", 9)))
    batch.add_edge(Edge("py:m.other", "py:m.target", EdgeKind.CALLS, Provenance("m.py", 14)))
    return FactStore(batch)


def test_callers_of_returns_callsites_with_lines() -> None:
    callers = _store().callers_of("py:m.target")
    assert {c.caller.id for c in callers} == {"py:m.caller", "py:m.other"}
    assert all(isinstance(c, CallSite) for c in callers)
    assert {c.at for c in callers} == {"m.py:9", "m.py:14"}


def test_touches_is_bidirectional_blast_radius() -> None:
    store = _store()
    # target is called by both → touches both callers
    assert {n.id for n in store.touches("py:m.target")} == {"py:m.caller", "py:m.other"}
    # caller only points at target
    assert {n.id for n in store.touches("py:m.caller")} == {"py:m.target"}


def test_find_prefers_grounded() -> None:
    store = _store()
    assert store.find("TARGET")[0].id == "py:m.target"  # case-insensitive


def test_summary_counts_grounded_vs_external() -> None:
    batch = FactBatch()
    batch.add_node(_grounded("py:a.f", "f"))
    batch.add_node(Node("py:ext", NodeKind.MODULE, "ext", external=True))
    s = FactStore(batch).summary()
    assert s["grounded_nodes"] == 1 and s["external_nodes"] == 1 and s["nodes"] == 2
