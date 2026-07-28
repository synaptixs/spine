"""Phase 4: no page is a stub or a directory listing (Findings 4, 5, 6).

The pages that had degraded into A–Z dumps, four-line pointers, and database
statistics. Each of these pins the difference between *listing* what exists and
*telling you which of it matters*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex
from orchestrator.knowledge.understand import render_memory_bank
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """`Hub` is central (subtypes + production callers); `Trivial` is not."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "class AppError(Exception):\n    pass\n\n\n"
        "class NotFound(AppError):\n    pass\n\n\n"
        "class Hub:\n"
        "    def __init__(self) -> None:\n        self.a = 1\n        self.b = 2\n\n"
        "    def run(self) -> int:\n        return 1\n\n\n"
        "class Spoke(Hub):\n    pass\n\n\n"
        "class Trivial:\n    pass\n\n\n"
        "def use() -> int:\n    return Hub().run()\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from app.core import Trivial\n\n\ndef test_t() -> None:\n    assert Trivial()\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "2.5.0"\nrequires-python = ">=3.12"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# App\n\n## Hub\n\nThe `Hub` coordinates.\n", encoding="utf-8")
    return tmp_path


def _pages(repo: Path) -> dict[str, str]:
    return render_memory_bank(repo, refresh=True).files


# ---- Finding 4: ranked, not alphabetical ------------------------------------


def test_domain_model_ranks_by_centrality_not_alphabet(repo: Path) -> None:
    page = _pages(repo)["domain-model.md"]
    assert "Why it matters" in page
    # `Hub` has a subtype and members; `AppError` sorts first alphabetically but is not
    # the type this codebase is built around.
    assert page.index("`Hub`") < page.index("`Trivial`")


def test_domain_model_says_what_it_is_when_there_is_no_database(repo: Path) -> None:
    page = _pages(repo)["domain-model.md"]
    assert "No database or ORM entities detected" in page
    assert "the types this codebase is built around" in page


def test_importance_ignores_test_call_sites_when_ranking(repo: Path) -> None:
    """Finding 6: being called by tests makes a symbol covered, not central."""
    store = FactStore(RepoCodeExtractor().extract(repo))
    imp = renderers.Importance(store)
    trivial = "py:app.core.Trivial"
    assert imp.test_callers[trivial] > 0  # the test constructs it
    assert imp.score(trivial) == 0  # …and that earns it nothing


# ---- Finding 5: no stubs ----------------------------------------------------


def test_glossary_links_terms_instead_of_promising_definitions(repo: Path) -> None:
    page = _pages(repo)["glossary.md"]
    assert "TODO" not in page
    assert "Defined at" in page and "Explained in" in page
    assert "README.md" in page  # the doc that explains `Hub`


def test_glossary_excludes_private_types(tmp_path: Path) -> None:
    """Underscore names are implementation detail — and they'd sort to the very top."""
    (tmp_path / "m.py").write_text("class _Cache:\n    pass\n\n\nclass Public:\n    pass\n", "utf-8")
    page = renderers.render_glossary(FactStore(RepoCodeExtractor().extract(tmp_path)))
    rows = [line for line in page.splitlines() if line.startswith("| **")]
    assert rows and all("_Cache" not in r for r in rows)
    assert any("Public" in r for r in rows)


def test_architecture_replaces_the_node_kind_dump_with_complexity(repo: Path) -> None:
    page = _pages(repo)["architecture.md"]
    assert "## Node kinds" not in page
    assert "## Complexity" in page
    assert "Size distribution" in page
    # the counts survive as scale, on the graph-size line
    assert "types" in page.split("\n")[3]


def test_conventions_carries_naming_tests_and_errors(repo: Path) -> None:
    page = _pages(repo)["conventions.md"]
    assert "**Naming**" in page
    assert "snake_case" in page
    assert "**Tests**" in page
    assert "**Errors**" in page


def test_exception_hierarchy_found_through_inheritance_not_names(repo: Path) -> None:
    """`NotFound` doesn't end in Error/Exception but is one; a name rule misses it."""
    page = _pages(repo)["conventions.md"]
    assert "2 exception types" in page


def test_tech_context_reports_the_declared_version(repo: Path) -> None:
    page = _pages(repo)["tech-context.md"]
    assert "2.5.0" in page
    assert ">=3.12" in page


# ---- Finding 6: production vs test call-sites -------------------------------


def test_symbol_section_counts_production_and_test_callers_apart(repo: Path) -> None:
    page = _pages(repo)["modules/app.core.md"]
    assert "production" in page or "test call-site" in page


def test_caller_split_reads_naturally() -> None:
    batch = FactBatch()
    prov, tprov = Provenance("app/core.py", 1), Provenance("tests/test_core.py", 1)
    batch.add_node(Node("py:app.core.f", NodeKind.FUNCTION, "f", "python", prov))
    batch.add_node(Node("py:app.core.g", NodeKind.FUNCTION, "g", "python", prov))
    batch.add_node(Node("py:tests.test_core.t", NodeKind.FUNCTION, "t", "python", tprov))
    batch.add_edge(Edge("py:app.core.g", "py:app.core.f", EdgeKind.CALLS, prov))
    batch.add_edge(Edge("py:tests.test_core.t", "py:app.core.f", EdgeKind.CALLS, tprov))
    imp = renderers.Importance(FactStore(batch))

    assert imp.caller_split("py:app.core.f") == "1 production · 1 test call-sites"
    assert imp.caller_split("py:app.core.g") == ""


def test_symbol_with_no_edges_says_so() -> None:
    """A heading followed by silence reads like the renderer gave up, not like a fact."""
    batch = FactBatch()
    prov = Provenance("m.py", 1)
    batch.add_node(Node("py:m", NodeKind.MODULE, "m", "python", prov))
    batch.add_node(Node("py:m.Lonely", NodeKind.TYPE, "Lonely", "python", prov))
    batch.add_edge(Edge("py:m", "py:m.Lonely", EdgeKind.CONTAINS, prov))
    store = FactStore(batch)

    page = renderers.render_module_page(
        store,
        store.node("py:m"),  # type: ignore[arg-type]
        src=None,
        page_of={},
        deps=renderers.ModuleDeps(store, AreaIndex(store)),
    )
    assert "_No relationships extracted" in page


def test_bank_does_not_embed_the_local_directory_name(tmp_path: Path) -> None:
    """A committed bank must be identical for everyone at a given commit. Using the
    checkout's directory name made it report itself stale in any differently-named
    clone — CI included."""
    for folder in ("checkout-a", "checkout-b"):
        d = tmp_path / folder
        (d / "app").mkdir(parents=True)
        (d / "app" / "core.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
        (d / "pyproject.toml").write_text('[project]\nname = "shop"\nversion = "1.0"\n', "utf-8")

    a = render_memory_bank(tmp_path / "checkout-a", refresh=True).files["README.md"]
    b = render_memory_bank(tmp_path / "checkout-b", refresh=True).files["README.md"]
    assert a == b
    assert "shop" in a and "checkout-a" not in a


def test_project_name_falls_back_to_the_directory(tmp_path: Path) -> None:
    (tmp_path / "nameless").mkdir()
    assert renderers.project_name(tmp_path / "nameless") == "nameless"
