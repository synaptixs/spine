"""Phase 3: the module page becomes a briefing (Findings 3, 8, 9).

A module page told you what the code *is*. These pin the rest of the question a
reader actually arrives with: what depends on it, what breaks if it changes, what
isn't tested, what it inherits from, and which docs describe it — plus the two
repo-level surfaces the graph could always serve and never rendered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex
from orchestrator.knowledge.understand import build_memory_bank, render_memory_bank
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.coverage import CoverageIndex


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A package with a class hierarchy, a test that exercises part of it, and a doc."""
    pkg = tmp_path / "shop"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "base.py").write_text("class Base:\n    pass\n", encoding="utf-8")
    (pkg / "core.py").write_text(
        "from .base import Base\n\n\n"
        "class Order(Base):\n    pass\n\n\n"
        "class Refund(Base):\n    pass\n\n\n"
        "def helper() -> int:\n    return 1\n\n\n"
        "def caller() -> int:\n    return helper()\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from shop.core import caller\n\n\ndef test_caller() -> None:\n    assert caller()\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Shop\n\n## Orders\n\nThe `Order` class models a purchase.\n", encoding="utf-8"
    )
    return tmp_path


def _page(repo: Path, name: str) -> str:
    files = render_memory_bank(repo, refresh=True).files
    return files[name]


# ---- inheritance (Finding 8) ------------------------------------------------


def test_store_answers_inheritance_both_directions() -> None:
    batch = FactBatch()
    prov = Provenance("m.py", 1)
    for nid in ("py:m.Base", "py:m.Child"):
        batch.add_node(Node(nid, NodeKind.TYPE, nid.rsplit(".", 1)[-1], "python", prov))
    batch.add_edge(Edge("py:m.Child", "py:m.Base", EdgeKind.IMPLEMENTS, prov))
    store = FactStore(batch)

    assert [n.id for n in store.implementors_of("py:m.Base")] == ["py:m.Child"]
    assert [n.id for n in store.implements_of("py:m.Child")] == ["py:m.Base"]
    assert store.implementors_of("py:m.Child") == []


def test_module_page_renders_inheritance_both_ways(repo: Path) -> None:
    core = _page(repo, "modules/shop.core.md")
    base = _page(repo, "modules/shop.base.md")
    assert "**Extends**" in core  # Order extends Base
    assert "**Implemented by**" in base  # Base is implemented by Order and Refund
    assert "Order" in base and "Refund" in base


# ---- documentation (Finding 3) ----------------------------------------------


def test_module_page_says_which_docs_describe_it(repo: Path) -> None:
    core = _page(repo, "modules/shop.core.md")
    assert "Documented in" in core
    assert "README.md" in core


def test_architecture_carries_a_documentation_section(repo: Path) -> None:
    arch = _page(repo, "architecture.md")
    assert "## Documentation" in arch
    assert "doc coverage" in arch


def test_documentation_section_absent_without_docs(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    assert "## Documentation" not in render_memory_bank(tmp_path, refresh=True).files["architecture.md"]


# ---- changing this safely (Finding 9) ---------------------------------------


def test_module_page_carries_a_safety_briefing(repo: Path) -> None:
    core = _page(repo, "modules/shop.core.md")
    assert "## Changing this safely" in core
    assert "Tested by" in core  # tests.test_core imports shop.core
    assert "Most depended-upon here" in core
    assert "reaches" in core


def test_coverage_index_tracks_test_reachability(repo: Path) -> None:
    store = FactStore(RepoCodeExtractor().extract(repo))
    cov = CoverageIndex(store)
    assert cov.call_graph_available
    # the test calls `caller`, which calls `helper` — both are reachable from a test
    assert cov.is_covered("py:shop.core.caller")
    assert cov.is_covered("py:shop.core.helper")
    # `helper` is depended on by `caller`
    assert [n.id for n in cov.blast_radius("py:shop.core.helper")] == ["py:shop.core.caller"]


def test_blast_radius_excludes_tests(repo: Path) -> None:
    """A test breaking is the system working — it isn't part of the blast radius."""
    store = FactStore(RepoCodeExtractor().extract(repo))
    cov = CoverageIndex(store)
    assert all("test" not in n.id for n in cov.blast_radius("py:shop.core.caller"))


def test_safety_block_silent_without_a_call_graph() -> None:
    """No call graph must not read as "nothing depends on this"."""
    batch = FactBatch()
    prov = Provenance("m.py", 1)
    batch.add_node(Node("x:m", NodeKind.MODULE, "m", "x", prov))
    batch.add_node(Node("x:m.f", NodeKind.FUNCTION, "f", "x", prov))
    batch.add_edge(Edge("x:m", "x:m.f", EdgeKind.CONTAINS, prov))
    store = FactStore(batch)
    cov = CoverageIndex(store)
    assert not cov.call_graph_available

    page = renderers.render_module_page(
        store,
        store.node("x:m"),  # type: ignore[arg-type]
        src=None,
        page_of={},
        deps=renderers.ModuleDeps(store, AreaIndex(store)),
        cov=cov,
    )
    assert "Changing this safely" not in page


# ---- API surface (Finding 8) ------------------------------------------------


def test_api_surface_page_is_written_only_when_routes_exist(repo: Path) -> None:
    """A library has no routes — an empty API page would imply we looked and found none."""
    build_memory_bank(repo, refresh=True)
    assert not (repo / "episteme" / "api-surface.md").exists()
    assert "api-surface.md" not in (repo / "episteme" / "README.md").read_text(encoding="utf-8")


def test_api_surface_renders_routes_and_handlers() -> None:
    batch = FactBatch()
    prov = Provenance("Api/UserController.cs", 12)
    batch.add_node(Node("csharp:Api.UserController", NodeKind.TYPE, "UserController", "csharp", prov))
    batch.add_node(Node("csharp:Api.UserController.Get", NodeKind.FUNCTION, "Get", "csharp", prov))
    batch.add_node(Node("csharp:endpoint:GET /users", NodeKind.ENDPOINT, "GET /users", "csharp", prov))
    batch.add_edge(
        Edge("csharp:endpoint:GET /users", "csharp:Api.UserController.Get", EdgeKind.EXPOSES, prov)
    )
    store = FactStore(batch)

    assert renderers.has_api_surface(store)
    page = renderers.render_api_surface(store)
    assert "1 endpoint" in page
    assert "GET /users" in page
    assert "Get" in page


def test_has_api_surface_false_without_endpoints(repo: Path) -> None:
    store = FactStore(RepoCodeExtractor().extract(repo))
    assert not renderers.has_api_surface(store)
