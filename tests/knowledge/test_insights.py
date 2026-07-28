"""Phase 5: findings that need no new facts (Finding 10).

Public-vs-internal, import cycles, an onboarding path, dead-code candidates and a
symbol index — each a question a *reader* has, answered from the graph as it stands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex
from orchestrator.knowledge.insights import (
    api_split,
    dead_code_candidates,
    import_cycles,
    is_public,
    onboarding_path,
)
from orchestrator.knowledge.understand import render_memory_bank
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A package with a public API, internals, a real import cycle, and dead code."""
    pkg = tmp_path / "shop"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .core import Order\n", encoding="utf-8")
    # core <-> tax is a genuine cycle
    (pkg / "core.py").write_text(
        "from .tax import rate\n\n\n"
        "class Order:\n    def total(self) -> int:\n        return rate()\n\n\n"
        "def _helper() -> int:\n    return 1\n\n\n"
        "def _never_called() -> int:\n    return 2\n\n\n"
        "def main() -> int:\n    return _helper()\n",
        encoding="utf-8",
    )
    (pkg / "tax.py").write_text(
        "from .core import Order\n\n\ndef rate() -> int:\n    return 7\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "shop"\nversion = "1.0"\n\n[project.scripts]\nshop = "shop.core:main"\n',
        encoding="utf-8",
    )
    return tmp_path


def _store(repo: Path) -> FactStore:
    return FactStore(RepoCodeExtractor().extract(repo))


def _pages(repo: Path) -> dict[str, str]:
    return render_memory_bank(repo, refresh=True).files


# ---- public vs internal -----------------------------------------------------


def test_is_public_reads_the_symbol_and_its_module() -> None:
    prov = Provenance("m.py", 1)
    assert is_public(Node("py:shop.core.Order", NodeKind.TYPE, "Order", "python", prov))
    assert not is_public(Node("py:shop.core._helper", NodeKind.FUNCTION, "_helper", "python", prov))
    # a public name inside a private module is still internal
    assert not is_public(Node("py:shop._impl.Thing", NodeKind.TYPE, "Thing", "python", prov))


def test_api_split_counts_both_sides(repo: Path) -> None:
    split = api_split(_store(repo))
    names = {n.name for n in split.public}
    assert "Order" in names and "rate" in names
    assert "_helper" not in names
    assert split.internal_count >= 2
    assert split.total == len(split.public) + split.internal_count


def test_architecture_reframes_the_repo_by_public_surface(repo: Path) -> None:
    page = _pages(repo)["architecture.md"]
    assert "## Public surface" in page
    assert "public" in page and "internal" in page


# ---- import cycles ----------------------------------------------------------


def test_import_cycles_finds_a_mutual_pair(repo: Path) -> None:
    store = _store(repo)
    deps = renderers.ModuleDeps(store, AreaIndex(store))
    cycles = import_cycles(deps.imports)
    assert cycles
    members = {n for cycle in cycles for n in cycle}
    assert "py:shop.core" in members and "py:shop.tax" in members


def test_import_cycles_ignores_an_acyclic_graph() -> None:
    assert import_cycles({"a": {"b"}, "b": {"c"}, "c": set()}) == []


def test_import_cycles_are_deterministic_and_largest_first() -> None:
    graph = {"a": {"b"}, "b": {"a"}, "x": {"y"}, "y": {"z"}, "z": {"x"}}
    first = import_cycles(graph)
    assert first == import_cycles(graph)
    assert len(first[0]) >= len(first[-1])
    assert first[0] == ["x", "y", "z"]


def test_import_cycles_survive_a_deep_chain() -> None:
    """Recursive Tarjan would hit the recursion limit on a real dependency chain."""
    graph = {f"m{i}": {f"m{i + 1}"} for i in range(3000)}
    graph["m3000"] = {"m0"}  # close it into one big cycle
    cycles = import_cycles(graph)
    assert len(cycles[0]) == 3001


def test_architecture_reports_cycles(repo: Path) -> None:
    assert "## Import cycles" in _pages(repo)["architecture.md"]


# ---- onboarding -------------------------------------------------------------


def test_onboarding_starts_at_the_entry_point_then_follows_fan_in(repo: Path) -> None:
    store = _store(repo)
    deps = renderers.ModuleDeps(store, AreaIndex(store))
    steps = onboarding_path(store, deps.importers, ["`main()` @ shop/core.py:14"])
    assert steps
    assert steps[0].module.name == "shop.core"
    assert "starts" in steps[0].why
    assert all(s.why for s in steps)  # every step explains itself


def test_readme_carries_the_onboarding_path(repo: Path) -> None:
    assert "## New here? Read these first" in _pages(repo)["README.md"]


# ---- dead code --------------------------------------------------------------


def test_dead_code_finds_uncalled_internals_only(repo: Path) -> None:
    dead = dead_code_candidates(_store(repo))
    names = {n.name for n in dead.candidates}
    assert "_never_called" in names
    assert "_helper" not in names  # main() calls it
    assert "Order" not in names  # public: no in-repo caller is what an API looks like


def test_dead_code_spares_symbols_a_doc_or_subclass_uses() -> None:
    batch = FactBatch()
    prov = Provenance("m.py", 1)
    for name in ("_Base", "_Documented", "_Orphan"):
        batch.add_node(Node(f"py:m.{name}", NodeKind.TYPE, name, "python", prov))
    batch.add_node(Node("py:m._Sub", NodeKind.TYPE, "_Sub", "python", prov))
    batch.add_edge(Edge("py:m._Sub", "py:m._Base", EdgeKind.IMPLEMENTS, prov))
    batch.add_node(Node("doc:readme", NodeKind.DOC, "README.md", "", prov))
    batch.add_edge(Edge("doc:readme", "py:m._Documented", EdgeKind.MENTIONS, prov))

    names = {n.name for n in dead_code_candidates(FactStore(batch)).candidates}
    assert names == {"_Orphan", "_Sub"}  # _Base is subclassed, _Documented is described


def test_dead_code_is_labelled_as_candidates(repo: Path) -> None:
    """Phase 3's lesson: a precision-first call graph cannot prove absence."""
    page = _pages(repo)["architecture.md"]
    if "## Possibly unused" in page:
        assert "Candidates, not verdicts" in page
        assert "dynamic dispatch" in page


# ---- symbol index -----------------------------------------------------------


def test_symbol_index_lists_symbols_with_their_page(repo: Path) -> None:
    page = _pages(repo)["symbol-index.md"]
    assert "`Order`" in page
    assert "modules/shop.core.md#order" in page  # anchored at the symbol section
    assert "symbol-index.md" in _pages(repo)["README.md"]


def test_symbol_index_excludes_dunders_and_tests(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "class A:\n"
        "    def __call__(self) -> None:\n        pass\n\n"
        "    def real(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    page = renderers.render_symbol_index(_store(tmp_path), page_of={})
    assert "`real`" in page
    assert "__call__" not in page
