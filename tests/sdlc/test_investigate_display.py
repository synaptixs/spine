"""How many landing sites a brief shows: up to ten distinct files, a few symbols each (SSPN-176 D11).

Ten *symbols* often name fewer than ten *files*, so a brief that capped symbols answered "where does
this land" with six or seven places while claiming ten. The default now fills ten distinct files; an
explicit ``max_symbols`` keeps the old meaning, so a caller that sized its own list (``design``) is
untouched. Display is not ranking: these tests never look at which symbol outranks which.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Node, NodeKind, Provenance
from orchestrator.pkg.retrieval import ScoredSymbol
from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.investigate import _display_slice, build_investigation

SUFFIXES = ("alpha", "bravo", "charlie", "delta", "echo")


def _repo(tmp_path: Path, files: int, per_file: int) -> FactStore:
    """``files`` modules, each defining ``per_file`` functions that all match 'order handler' equally."""
    for i in range(files):
        body = "\n\n".join(f"def order_handler_{SUFFIXES[j]}():\n    return {j}" for j in range(per_file))
        (tmp_path / f"f{i:02d}.py").write_text(body + "\n", encoding="utf-8")
    return FactStore(RepoCodeExtractor().extract(tmp_path))


def _files(inv_landing: list) -> list[str]:  # type: ignore[type-arg]
    seen: list[str] = []
    for land in inv_landing:
        f = land.where.rsplit(":", 1)[0]
        if f not in seen:
            seen.append(f)
    return seen


def test_the_default_fills_ten_distinct_files_with_at_most_three_symbols_each(tmp_path: Path) -> None:
    store = _repo(tmp_path, files=12, per_file=5)  # 60 equally good symbols in 12 files

    inv = build_investigation("order handler", "order handler", store=store)

    files = _files(inv.landing)
    assert len(files) == 10
    assert all(sum(1 for land in inv.landing if land.where.startswith(f + ":")) <= 3 for f in files)
    assert len(inv.landing) == 30


def test_what_was_not_shown_is_counted_not_hidden(tmp_path: Path) -> None:
    """Invariant 7: 'top N of M'. Symbols cut by the per-file cap or the eleventh file are elided."""
    store = _repo(tmp_path, files=12, per_file=5)

    inv = build_investigation("order handler", "order handler", store=store)

    assert inv.elided == 60 - len(inv.landing)


def test_fewer_than_ten_files_lists_everything_and_claims_nothing_cut(tmp_path: Path) -> None:
    store = _repo(tmp_path, files=4, per_file=2)

    inv = build_investigation("order handler", "order handler", store=store)

    assert len(_files(inv.landing)) == 4
    assert len(inv.landing) == 8
    assert inv.elided == 0


def test_an_explicit_max_symbols_keeps_its_old_meaning(tmp_path: Path) -> None:
    """`design` asks for 8 and the three-way harness for 50; neither may change under them."""
    store = _repo(tmp_path, files=12, per_file=5)

    inv = build_investigation("order handler", "order handler", store=store, max_symbols=8)

    assert len(inv.landing) == 8
    assert inv.elided == 1  # the legacy path fetches N + 1, so it says "more", not how many


def test_the_brief_is_identical_run_to_run(tmp_path: Path) -> None:
    store = _repo(tmp_path, files=12, per_file=5)

    first = build_investigation("order handler", "order handler", store=store)
    second = build_investigation("order handler", "order handler", store=store)

    assert [land.node_id for land in first.landing] == [land.node_id for land in second.landing]


# ---- the selection rule on its own, so the ordering cases need no graph ------------------------


def _hit(file: str, name: str) -> ScoredSymbol:
    node = Node(
        id=f"py:{file}.{name}", kind=NodeKind.FUNCTION, name=name, provenance=Provenance(file=file, line=1)
    )
    return ScoredSymbol(node=node, score=1.0, matched=("x",), weak=False)


def test_it_stops_at_the_first_symbol_that_would_start_an_eleventh_file() -> None:
    hits = [_hit(f"f{i}.py", "a") for i in range(10)]
    hits.append(_hit("f10.py", "a"))  # would be the 11th file: the walk ends here
    hits.append(_hit("f0.py", "b"))  # ranks after it, so it is not shown

    shown = _display_slice(hits, max_files=10, per_file=3, max_listed=30)

    assert [h.node.id for h in shown] == [f"py:f{i}.py.a" for i in range(10)]


def test_a_listed_file_keeps_collecting_until_the_eleventh_file_appears() -> None:
    hits = [_hit(f"f{i}.py", "a") for i in range(10)]
    hits.append(_hit("f0.py", "b"))  # a listed file's next symbol, before any eleventh file
    hits.append(_hit("f10.py", "a"))

    shown = _display_slice(hits, max_files=10, per_file=3, max_listed=30)

    assert [h.node.id for h in shown][-1] == "py:f0.py.b"
    assert len(shown) == 11


def test_the_per_file_cap_drops_a_fourth_symbol_but_not_the_walk() -> None:
    hits = [_hit("a.py", n) for n in "wxyz"] + [_hit("b.py", "q")]

    shown = _display_slice(hits, max_files=10, per_file=3, max_listed=30)

    assert [h.node.id for h in shown] == ["py:a.py.w", "py:a.py.x", "py:a.py.y", "py:b.py.q"]


def test_the_total_cap_bounds_the_brief() -> None:
    hits = [_hit(f"f{i}.py", n) for i in range(10) for n in "abc"]  # 30 symbols, 10 files

    assert len(_display_slice(hits, max_files=10, per_file=3, max_listed=30)) == 30
    assert len(_display_slice(hits, max_files=10, per_file=3, max_listed=12)) == 12
