"""The hybrid retrieval core (hybrid KG search, D6/D7/D23): four legs fused by reciprocal rank.

Name/path (today's ranker), content (BM25 over symbol text), meaning (an embedding, PR2) and graph
(personalized
PageRank seeded by the first three) are fused by RRF, k = 60. What these tests pin: the fusion and the walk
are the
published formulas; the walk goes through test code but never returns it; documentation is never a landing
site
(B158); a hit says which legs found it and, for the graph, from where; and the same graph gives the same
bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactBatch, FactStore, Node, NodeKind, Provenance
from orchestrator.pkg.extractor import RepoCodeExtractor, mark_tests
from orchestrator.pkg.facts import Edge, EdgeKind
from orchestrator.pkg.hybrid import (
    TRAVERSAL_EDGES,
    HybridConfig,
    HybridRetriever,
    personalized_pagerank,
    reciprocal_rank_fusion,
)


def test_rrf_is_the_sum_of_weighted_reciprocal_ranks_and_ties_go_to_the_id() -> None:
    fused = reciprocal_rank_fusion({"x": ["a", "b"], "y": ["b", "c"]}, k=60)
    assert [i for i, _ in fused] == ["b", "a", "c"]
    assert dict(fused)["b"] == round(1 / 62 + 1 / 61, 6)
    weighted = reciprocal_rank_fusion({"x": ["a"], "y": ["b"]}, k=60, weights={"x": 2.0})
    assert [i for i, _ in weighted] == ["a", "b"]
    assert [i for i, _ in reciprocal_rank_fusion({"x": ["z"], "y": ["a"]}, k=60)] == ["a", "z"]


def _node(nid: str, kind: NodeKind = NodeKind.FUNCTION, file: str = "m.py") -> Node:
    return Node(nid, kind, nid.rsplit(".", 1)[-1], "python", Provenance(file, 1, 2))


def _graph(nodes: list[Node], edges: list[tuple[str, str, EdgeKind]]) -> FactStore:
    batch = FactBatch()
    for n in nodes:
        batch.add_node(n)
    for src, dst, kind in edges:
        batch.add_edge(Edge(src, dst, kind))
    return FactStore(mark_tests(batch))


def test_pagerank_spreads_from_the_seed_along_the_traversal_edges_only() -> None:
    nodes = [_node(f"py:m.{n}") for n in ("seed", "near", "far", "served", "alone")]
    store = _graph(
        nodes,
        [
            ("py:m.seed", "py:m.near", EdgeKind.CALLS),
            ("py:m.near", "py:m.far", EdgeKind.CALLS),
            ("py:m.seed", "py:m.served", EdgeKind.SERVES),  # not a traversal edge
        ],
    )
    assert EdgeKind.SERVES not in TRAVERSAL_EDGES and EdgeKind.CALLS in TRAVERSAL_EDGES
    scores = personalized_pagerank(store, {"py:m.seed": 1.0}, restart=0.15, iterations=30)
    # On a path the seed's only neighbour collects everything the seed passes on, so it may outscore the seed;
    # what must hold is that mass falls off with distance and never crosses a non-traversal edge.
    assert scores["py:m.near"] > scores["py:m.far"] > 0
    assert scores["py:m.seed"] > scores["py:m.far"]
    assert scores.get("py:m.served", 0.0) == 0.0
    assert scores.get("py:m.alone", 0.0) == 0.0


def test_pagerank_walks_both_directions() -> None:
    store = _graph(
        [_node("py:m.caller"), _node("py:m.callee")], [("py:m.caller", "py:m.callee", EdgeKind.CALLS)]
    )
    assert personalized_pagerank(store, {"py:m.callee": 1.0})["py:m.caller"] > 0


_BILLING = '''\
def persist(record, path):
    """Append one row to the accounting ledger."""
    with open(path, "a") as fh:
        fh.write(record)
    audit_trail(record)


def audit_trail(record):
    return len(record)


def render(record):
    return str(record)
'''

_TEST = """\
from billing import persist


def test_ledger_rows():
    persist("r", "p")
"""


@pytest.fixture
def repo(tmp_path: Path) -> tuple[FactStore, Path]:
    (tmp_path / "billing.py").write_text(_BILLING)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_billing.py").write_text(_TEST)
    return FactStore(RepoCodeExtractor().extract(tmp_path)), tmp_path


def test_the_content_leg_finds_what_no_name_says(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    hits = HybridRetriever(store, root).search("Rows go missing from the accounting ledger")
    top = hits[0]
    assert top.node.id.endswith("billing.persist")
    assert "content" in top.legs and "name" not in top.legs


def test_the_graph_leg_reaches_a_callee_and_says_from_where(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    hits = {h.node.id.rsplit(".", 1)[-1]: h for h in HybridRetriever(store, root).search("accounting ledger")}
    audit = hits["audit_trail"]
    assert "graph" in audit.legs
    assert audit.via and audit.via[0].endswith("persist") and audit.via[1] == "CALLS"
    assert "graph via persist → CALLS" in audit.found_by


def test_test_code_is_walked_through_but_never_returned(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    hits = HybridRetriever(store, root).search("ledger rows test")
    assert hits
    assert not any(h.node.test for h in hits)


def test_documentation_and_intents_are_never_landing_sites(tmp_path: Path) -> None:
    store = _graph(
        [
            Node("py:billing.refund", NodeKind.FUNCTION, "refund", "python", Provenance("billing.py", 1, 2)),
            Node(
                "doc:README.md#refund-policy",
                NodeKind.DOC,
                "Refund policy",
                "",
                Provenance("README.md", 1, 3),
            ),
            Node("intent:SSPN-9", NodeKind.INTENT, "Refund policy", ""),
        ],
        [("doc:README.md#refund-policy", "py:billing.refund", EdgeKind.MENTIONS)],
    )
    hits = HybridRetriever(store, tmp_path).search("refund policy")
    assert [h.node.id for h in hits] == ["py:billing.refund"]


def test_a_weak_name_hit_stays_weak_unless_another_leg_supports_it(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def client_one():\n    return 1\n")
    (tmp_path / "b.py").write_text("def client_two():\n    return 2\n")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    # No content leg (no root): the walk only re-finds its own seeds, which is no independent support (D25).
    name_only = [h for h in HybridRetriever(store, None).search("client") if "name" in h.legs]
    assert len(name_only) == 2 and all(h.weak for h in name_only)
    # The content leg ranks both in its top 20: that is support of its own, so neither is weak.
    with_content = HybridRetriever(store, tmp_path).search("client")
    assert with_content and not any(h.weak for h in with_content)


def test_without_a_model_the_core_runs_three_legs_and_says_so(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    retriever = HybridRetriever(store, root)
    assert retriever.legs_used == ("name", "content", "graph")
    assert all("meaning" not in h.legs for h in retriever.search("accounting ledger"))


def test_the_name_leg_can_count_double(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    equal = HybridRetriever(store, root, config=HybridConfig(name_weight=1.0)).search("render the ledger")
    double = HybridRetriever(store, root, config=HybridConfig(name_weight=2.0)).search("render the ledger")
    assert equal[0].node.name != double[0].node.name or equal[0].score != double[0].score


def test_extra_seeds_bring_in_what_the_words_alone_never_reach(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(_BILLING)
    (tmp_path / "parser.py").write_text("def tokenize(stream):\n    return list(stream)\n")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    plain = {h.node.name for h in HybridRetriever(store, tmp_path).search("accounting ledger")}
    seeded = {
        h.node.name
        for h in HybridRetriever(store, tmp_path).search(
            "accounting ledger", extra_seeds=("py:parser.tokenize",)
        )
    }
    assert "tokenize" not in plain  # no query word names it, and no edge ties parser.py to billing.py
    assert "tokenize" in seeded


def test_the_same_graph_gives_the_same_bytes(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    first = [
        (h.node.id, h.score, h.legs, h.via) for h in HybridRetriever(store, root).search("accounting ledger")
    ]
    again = [
        (h.node.id, h.score, h.legs, h.via) for h in HybridRetriever(store, root).search("accounting ledger")
    ]
    assert first == again
