"""First-line ranking: ``scored_symbols(text, title=...)`` ranks on the title, the body only breaks ties.

SSPN-192. With ``title`` unset the ranking is exactly what it was, so the codegen agent's
``pkg_relevant_symbols`` tool and ``sdlc/grounding`` are untouched; only ``investigate`` passes a title.
"""

from __future__ import annotations

from orchestrator.pkg import FactBatch, FactStore, GroundedRetriever, Node, NodeKind, Provenance
from orchestrator.pkg.retrieval import ScoredSymbol


def _fn(file: str, name: str) -> Node:
    return Node(f"py:{file[:-3]}.{name}", NodeKind.FUNCTION, name, "python", Provenance(file, 1, 3))


def _store(*nodes: Node) -> FactStore:
    batch = FactBatch()
    for n in nodes:
        batch.add_node(n)
    return FactStore(batch)


def _widgets() -> FactStore:
    return _store(
        _fn("a.py", "frobnicate_widgets"),  # names both title words
        _fn("b.py", "frobnicate"),  # one title word
        _fn("c.py", "gizmo_gears"),  # body words only
        _fn("d.py", "frobnicate_gizmo"),  # one title word, one body word
    )


def _ids(hits: list[ScoredSymbol]) -> list[str]:
    return [h.node.id for h in hits]


TITLE = "frobnicate widgets"
BODY = "also touches gizmo gears"


def test_flat_order_is_pinned_when_no_title() -> None:
    """The order today: the flat query of title and body, ties by node id."""
    hits = GroundedRetriever(_widgets()).scored_symbols(f"{TITLE}\n{BODY}")
    assert _ids(hits) == [
        "py:a.frobnicate_widgets",
        "py:c.gizmo_gears",
        "py:d.frobnicate_gizmo",
        "py:b.frobnicate",
    ]


def test_title_none_is_identical_to_omitting_it() -> None:
    r = GroundedRetriever(_widgets())
    text = f"{TITLE}\n{BODY}"
    assert r.scored_symbols(text, title=None) == r.scored_symbols(text)
    assert r.relevant_symbols(text) == [h.node for h in r.scored_symbols(text)]


def test_empty_or_wordless_title_is_flat() -> None:
    r = GroundedRetriever(_widgets())
    text = f"{TITLE}\n{BODY}"
    assert r.scored_symbols(text, title="") == r.scored_symbols(text)
    assert r.scored_symbols(text, title="the of to") == r.scored_symbols(text)  # stop words only


def test_title_matches_rank_first_and_body_only_hits_follow() -> None:
    hits = GroundedRetriever(_widgets()).scored_symbols(f"{TITLE}\n{BODY}", title=TITLE)
    assert _ids(hits) == [
        "py:a.frobnicate_widgets",  # title score first
        "py:b.frobnicate",
        "py:d.frobnicate_gizmo",
        "py:c.gizmo_gears",  # matches the body only: never ahead of a title match
    ]


def test_title_score_ties_are_broken_by_the_full_text_score() -> None:
    store = _store(_fn("x.py", "alpha_amber"), _fn("y.py", "alpha_birch"))
    r = GroundedRetriever(store)
    # By the title alone the two tie, and node id puts x first.
    assert _ids(r.scored_symbols("alpha")) == ["py:x.alpha_amber", "py:y.alpha_birch"]
    # The body names birch, so the full-text score breaks the tie the other way.
    assert _ids(r.scored_symbols("alpha\nbirch", title="alpha")) == ["py:y.alpha_birch", "py:x.alpha_amber"]


def test_limit_applies_after_ordering() -> None:
    hits = GroundedRetriever(_widgets()).scored_symbols(f"{TITLE}\n{BODY}", title=TITLE, limit=2)
    assert _ids(hits) == ["py:a.frobnicate_widgets", "py:b.frobnicate"]


def test_test_files_stay_excluded_with_a_title() -> None:
    store = _store(_fn("tests/test_widgets.py", "frobnicate_widgets"), _fn("a.py", "frobnicate"))
    hits = GroundedRetriever(store).scored_symbols(f"{TITLE}\n{BODY}", title=TITLE)
    assert _ids(hits) == ["py:a.frobnicate"]
