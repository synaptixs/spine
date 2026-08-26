"""FactStore.path_between — provenance-grounded shortest extracted paths."""

from __future__ import annotations

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore, PathDirection


def _node(name: str, line: int, kind: NodeKind = NodeKind.FUNCTION) -> Node:
    return Node(
        id=f"py:m.{name}",
        kind=kind,
        name=name,
        language="python",
        provenance=Provenance("m.py", line),
    )


def _store(nodes: list[Node], edges: list[Edge]) -> FactStore:
    batch = FactBatch()
    for node in nodes:
        batch.add_node(node)
    for edge in edges:
        batch.add_edge(edge)
    return FactStore(batch)


def test_path_between_returns_shortest_forward_chain_with_provenance() -> None:
    client, endpoint, handler, service = (
        _node(name, line)
        for name, line in (("client", 1), ("endpoint", 10), ("handler", 20), ("service", 30))
    )
    edges = [
        Edge(client.id, endpoint.id, EdgeKind.CONSUMES, Provenance("m.py", 2)),
        Edge(endpoint.id, handler.id, EdgeKind.EXPOSES, Provenance("m.py", 11)),
        Edge(handler.id, service.id, EdgeKind.CALLS, Provenance("m.py", 21)),
    ]

    result = _store([client, endpoint, handler, service], edges).path_between(client.id, service.id)

    assert result is not None
    assert result.distance == 3
    assert [(hop.edge.kind, str(hop.edge.provenance), hop.reversed) for hop in result.hops] == [
        (EdgeKind.CONSUMES, "m.py:2", False),
        (EdgeKind.EXPOSES, "m.py:11", False),
        (EdgeKind.CALLS, "m.py:21", False),
    ]
    assert [hop.source.id for hop in result.hops] == [client.id, endpoint.id, handler.id]
    assert result.hops[-1].target.id == service.id


def test_path_between_reverse_preserves_the_extracted_edge_orientation() -> None:
    endpoint, handler = _node("endpoint", 1, NodeKind.ENDPOINT), _node("handler", 2)
    edge = Edge(endpoint.id, handler.id, EdgeKind.EXPOSES, Provenance("m.py", 3))

    result = _store([endpoint, handler], [edge]).path_between(
        handler.id,
        endpoint.id,
        direction=PathDirection.REVERSE,
    )

    assert result is not None
    assert result.distance == 1
    hop = result.hops[0]
    assert hop.source == handler
    assert hop.target == endpoint
    assert hop.edge == edge
    assert hop.reversed is True


def test_path_between_both_finds_doc_to_code_in_reverse_direction() -> None:
    doc = _node("architecture", 1, NodeKind.DOC)
    symbol = _node("handler", 9)
    edge = Edge(doc.id, symbol.id, EdgeKind.MENTIONS, Provenance("ARCHITECTURE.md", 4))
    store = _store([doc, symbol], [edge])

    assert store.path_between(symbol.id, doc.id) is None
    result = store.path_between(symbol.id, doc.id, direction=PathDirection.BOTH)

    assert result is not None
    assert result.hops[0].edge.kind is EdgeKind.MENTIONS
    assert result.hops[0].reversed is True


def test_path_between_chooses_a_stable_equal_length_path_after_edge_order_changes() -> None:
    source, alpha, beta, target = (
        _node(name, line) for name, line in (("source", 1), ("alpha", 2), ("beta", 3), ("target", 4))
    )
    edges = [
        Edge(source.id, beta.id, EdgeKind.CALLS, Provenance("m.py", 10)),
        Edge(beta.id, target.id, EdgeKind.CALLS, Provenance("m.py", 11)),
        Edge(source.id, alpha.id, EdgeKind.CALLS, Provenance("m.py", 12)),
        Edge(alpha.id, target.id, EdgeKind.CALLS, Provenance("m.py", 13)),
    ]
    nodes = [source, alpha, beta, target]

    first = _store(nodes, edges).path_between(source.id, target.id)
    second = _store(nodes, list(reversed(edges))).path_between(source.id, target.id)

    assert first is not None and second is not None
    assert [hop.target.id for hop in first.hops] == [alpha.id, target.id]
    assert [hop.target.id for hop in second.hops] == [alpha.id, target.id]


def test_path_between_handles_cycles_and_respects_depth_and_kind_limits() -> None:
    a, b, c = (_node(name, line) for name, line in (("a", 1), ("b", 2), ("c", 3)))
    edges = [
        Edge(a.id, b.id, EdgeKind.CALLS, Provenance("m.py", 4)),
        Edge(b.id, a.id, EdgeKind.CALLS, Provenance("m.py", 5)),
        Edge(b.id, c.id, EdgeKind.REFERENCES, Provenance("m.py", 6)),
    ]
    store = _store([a, b, c], edges)

    assert store.path_between(a.id, c.id, max_depth=1) is None
    assert store.path_between(a.id, c.id, kinds=(EdgeKind.CALLS,)) is None
    result = store.path_between(a.id, c.id, max_depth=2)

    assert result is not None
    assert [hop.target.id for hop in result.hops] == [b.id, c.id]


def test_path_between_handles_same_node_and_missing_or_invalid_queries() -> None:
    a = _node("a", 1)
    store = _store([a], [])

    same = store.path_between(a.id, a.id)
    assert same is not None
    assert same.distance == 0
    assert store.path_between(a.id, "py:m.missing") is None

    try:
        store.path_between(a.id, a.id, max_depth=-1)
    except ValueError as exc:
        assert str(exc) == "max_depth must be non-negative"
    else:
        raise AssertionError("negative max_depth must be rejected")

    try:
        store.path_between(a.id, a.id, kinds=())
    except ValueError as exc:
        assert str(exc) == "at least one edge kind is required"
    else:
        raise AssertionError("empty edge kinds must be rejected")


def test_path_between_skips_dangling_facts_instead_of_fabricating_a_node() -> None:
    source = _node("source", 1)
    dangling = Edge(source.id, "py:m.missing", EdgeKind.CALLS, Provenance("m.py", 2))

    assert _store([source], [dangling]).path_between(source.id, source.id) is not None
    assert _store([source], [dangling]).path_between(source.id, "py:m.missing") is None
