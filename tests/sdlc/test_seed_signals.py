"""`sdlc/seed_signals.py`: two deterministic signals a ticket gives search before search runs (SSPN-390).

Measured on SSPN-376: the quoted sentence sat 44 lines into `builddoc._blast_prose`, past the 20 lines
of body the hybrid indexes, and `store.importers_of` was one call from a function in a file the ticket
named but drowned among 440 seeds. These tests pin the two signals that recover them, and the limits
that keep them from becoming noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc import rca
from orchestrator.sdlc.localize import localize_trace
from orchestrator.sdlc.seed_signals import (
    HopHit,
    QuoteHit,
    one_hop,
    quoted_text_hits,
    ticket_quotes,
)

QUOTE = "Nothing in the product depends on what changes."
GENERIC = "a very generic phrase indeed"


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int, end: int, *, test: bool = False) -> Node:
    return Node(
        id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line, end), test=test
    )


def _tree(root: Path) -> FactStore:
    """render.py prints the quote 26 lines into its function; impact.blast_radius calls store.importers_of."""
    body = "\n".join(f"    a{i} = {i}" for i in range(24))
    files = {
        "src/app/render.py": f'def render(x):\n{body}\n    return "{QUOTE}"\n',
        "src/app/store.py": "def importers_of(n):\n    return []\n",
        "src/app/impact.py": "def blast_radius(store):\n    return store.importers_of(1)\n",
        "tests/test_render.py": f'def test_render():\n    assert "{QUOTE}"\n',
    }
    for i in range(4):
        files[f"src/app/many{i}.py"] = f'X = "{GENERIC}"\n'
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    b = FactBatch()
    for rel in files:
        mod = rel.removesuffix(".py").replace("/", ".")
        b.add_node(_node(f"py:{mod}", NodeKind.MODULE, mod, rel, 1, 40, test=rel.startswith("tests/")))
    b.add_node(_node("py:render", NodeKind.FUNCTION, "render", "src/app/render.py", 1, 26))
    b.add_node(_node("py:importers_of", NodeKind.FUNCTION, "importers_of", "src/app/store.py", 1, 2))
    b.add_node(_node("py:blast_radius", NodeKind.FUNCTION, "blast_radius", "src/app/impact.py", 1, 2))
    b.add_node(
        _node("py:test_render", NodeKind.FUNCTION, "test_render", "tests/test_render.py", 1, 2, test=True)
    )
    b.add_edge(Edge("py:src.app.render", "py:render", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:blast_radius", "py:importers_of", EdgeKind.CALLS, Provenance("src/app/impact.py", 2)))
    b.add_edge(
        Edge("py:test_render", "py:blast_radius", EdgeKind.CALLS, Provenance("tests/test_render.py", 2))
    )
    return FactStore(b)


def test_quotes_are_double_quoted_runs_of_at_least_four_words_split_at_an_ellipsis() -> None:
    text = (
        f'The section reads "{QUOTE}" and “the only importers are tests … nothing else here”. '
        'A "short one" and a "two words" stay out.'
    )
    assert ticket_quotes(text) == [
        "Nothing in the product depends on what changes",
        "the only importers are tests",
    ]


def test_a_quote_lands_on_the_innermost_symbol_holding_it_even_past_the_indexed_body(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    hits = quoted_text_hits(f'It says "{QUOTE}"', store, tmp_path)
    assert hits == [
        QuoteHit(
            quote="Nothing in the product depends on what changes",
            file="src/app/render.py",
            line=26,
            node_id="py:render",
        )
    ]


def test_test_code_is_never_a_quote_hit(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    assert all(not h.file.startswith("tests/") for h in quoted_text_hits(f'"{QUOTE}"', store, tmp_path))


def test_a_quote_found_in_more_than_three_files_is_too_generic_to_be_evidence(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    assert quoted_text_hits(f'it shows "{GENERIC}"', store, tmp_path) == []


def test_a_file_outside_the_root_is_never_read(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.py").write_text(f'X = "{QUOTE}"\n')
    b = FactBatch()
    b.add_node(_node("py:secret", NodeKind.MODULE, "secret", "../secret.py", 1, 1))
    assert quoted_text_hits(f'"{QUOTE}"', FactStore(b), root) == []


def test_one_hop_follows_calls_from_the_top_hits_in_named_files_and_skips_tests(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    ranked = ["py:src.app.render", "py:blast_radius", "py:render"]
    hops = one_hop(store, ranked, {"src/app/impact.py"}, k=3)
    assert hops == [
        HopHit(
            origin="py:blast_radius", node_id="py:importers_of", file="src/app/store.py", direction="calls"
        )
    ]
    assert one_hop(store, ranked, {"src/app/impact.py"}, k=0) == []


def test_a_module_node_never_uses_up_an_origin_slot(tmp_path: Path) -> None:
    """A module calls nothing; were it an origin, k=1 would spend its one slot and find no hop."""
    store = _tree(tmp_path)
    hops = one_hop(store, ["py:src.app.impact", "py:blast_radius"], {"src/app/impact.py"}, k=1)
    assert [h.node_id for h in hops] == ["py:importers_of"]


def test_one_hop_never_returns_a_file_the_ticket_already_named(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    hops = one_hop(store, ["py:blast_radius"], {"src/app/impact.py", "src/app/store.py"}, k=3)
    assert hops == []


@dataclass
class _Hit:
    node: Node
    found_by: str = "content (#1)"


@dataclass
class _FakeHybrid:
    store: FactStore
    model: str | None = "fake"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search(
        self, text: str, *, limit: int = 50, extra_seeds: tuple[str, ...] | list[str] = ()
    ) -> list[_Hit]:
        self.calls.append({"limit": limit, "extra_seeds": list(extra_seeds)})
        order = ["py:blast_radius", "py:src.app.impact", "py:importers_of", "py:render"]
        by_id = {n.id: n for n in self.store.nodes}
        return [_Hit(by_id[i]) for i in order]


def test_rca_searches_once_two_hundred_deep_and_reports_both_signals_without_reseeding(
    tmp_path: Path,
) -> None:
    """P2 re-scope: a second pass seeded with the signals moved no fix file in the dev set's top 10
    (15/22 under all six settings), so the signals are reported for the hypotheses, not fed back."""
    store = _tree(tmp_path)
    problem = f'§5 of `src/app/impact.py` says "{QUOTE}"'
    loc = localize_trace(problem, store=store)
    fake = _FakeHybrid(store)
    ranked = rca._ranked_with_signals(problem, loc, store, tmp_path, fake)  # type: ignore[arg-type]
    assert len(fake.calls) == 1
    assert fake.calls[0]["limit"] == rca.RCA_HIT_LIMIT == 200
    assert fake.calls[0]["extra_seeds"] == ["py:blast_radius", "py:src.app.impact"]  # the named file only
    assert [q.node_id for q in ranked.quotes] == ["py:render"]
    assert [h.node_id for h in ranked.hops] == ["py:importers_of"]
    assert ranked.files[:3] == ["src/app/impact.py", "src/app/store.py", "src/app/render.py"]


def test_the_three_value_ranking_callers_rely_on_is_unchanged(tmp_path: Path) -> None:
    store = _tree(tmp_path)
    problem = f'`src/app/impact.py` says "{QUOTE}"'
    loc = localize_trace(problem, store=store)
    files, evidence, model = rca._ranked_candidates(problem, loc, store, tmp_path, _FakeHybrid(store))  # type: ignore[arg-type]
    assert files[0] == "src/app/impact.py" and len(files) == len(evidence) and model == "fake"
