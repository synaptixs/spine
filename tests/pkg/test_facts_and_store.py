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


# ---- EXPOSES in blast radius (a handler's callers are outside the language) ----


def _route_graph() -> FactBatch:
    """``GET /v1/runs`` routes to ``list_runs``, which calls ``_summarize``.

    ``list_runs`` has no in-language caller, which is the whole point: the framework
    invokes it through the decorator.
    """
    b = FactBatch()
    b.add_node(Node("py:api", NodeKind.MODULE, "api", "python", Provenance("api.py", 1)))
    b.add_node(Node("py:api.list_runs", NodeKind.FUNCTION, "list_runs", "python", Provenance("api.py", 10)))
    b.add_node(Node("py:api._summarize", NodeKind.FUNCTION, "_summarize", "python", Provenance("api.py", 30)))
    b.add_node(
        Node("py:endpoint:GET /v1/runs", NodeKind.ENDPOINT, "GET /v1/runs", "python", Provenance("api.py", 9))
    )
    b.add_edge(Edge("py:api", "py:api.list_runs", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:api", "py:api._summarize", EdgeKind.CONTAINS))
    b.add_edge(
        Edge("py:endpoint:GET /v1/runs", "py:api.list_runs", EdgeKind.EXPOSES, Provenance("api.py", 9))
    )
    b.add_edge(Edge("py:api.list_runs", "py:api._summarize", EdgeKind.CALLS, Provenance("api.py", 12)))
    return b


def test_exposers_of_returns_the_routing_endpoints() -> None:
    store = FactStore(_route_graph())
    assert [n.name for n in store.exposers_of("py:api.list_runs")] == ["GET /v1/runs"]
    assert store.exposers_of("py:api._summarize") == []


def test_impact_of_a_handler_names_its_endpoint() -> None:
    """The regression: this returned [] and read as 'nothing depends on this handler',
    which for a public route is the most dangerous answer the graph can give."""
    store = FactStore(_route_graph())
    assert [(n.name, d) for n, d in store.impact_of("py:api.list_runs")] == [("GET /v1/runs", 1)]


def test_the_endpoint_is_transitively_reachable() -> None:
    """Changing what the handler calls also changes what the route serves — at 2 hops."""
    store = FactStore(_route_graph())
    radius = {n.name: d for n, d in store.impact_of("py:api._summarize")}
    assert radius == {"list_runs": 1, "GET /v1/runs": 2}


def test_callers_of_still_means_call_sites() -> None:
    """``EXPOSES`` joins blast radius, not the call graph: a handler has no call sites,
    and anything counting call sites must keep saying zero."""
    store = FactStore(_route_graph())
    assert store.callers_of("py:api.list_runs") == []


def test_impact_across_includes_exposes_by_default() -> None:
    store = FactStore(_route_graph())
    default = {n.name for n, _ in store.impact_across("py:api.list_runs")}
    explicit = {n.name for n, _ in store.impact_across("py:api.list_runs", kinds=(EdgeKind.CALLS,))}
    assert "GET /v1/runs" in default
    assert explicit == set()  # a caller that asks for CALLS only still gets the old reading
