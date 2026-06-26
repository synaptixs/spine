"""Memory-bank renderers + build_memory_bank (deterministic, no LLM)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.knowledge.renderers import (
    _is_test_module,
    _under_tests,
    render_architecture,
    render_domain_model,
    render_glossary,
    render_tech_context,
)
from orchestrator.knowledge.understand import build_memory_bank, memory_bank_dir
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.stats import summarise_store
from orchestrator.pkg.store import FactStore


def _store() -> FactStore:
    batch = FactBatch()
    src = Provenance(file="src/app/core.py", line=1)
    tst = Provenance(file="tests/test_core.py", line=1)
    batch.add_node(Node(id="py:app.core", kind=NodeKind.MODULE, name="app.core", provenance=src))
    batch.add_node(Node(id="py:app.core.Widget", kind=NodeKind.TYPE, name="Widget", provenance=src))
    batch.add_node(Node(id="py:app.core.run", kind=NodeKind.FUNCTION, name="run", provenance=src))
    batch.add_node(
        Node(id="py:tests.test_core", kind=NodeKind.MODULE, name="tests.test_core", provenance=tst)
    )
    batch.add_node(Node(id="py:tests.test_core.TFix", kind=NodeKind.TYPE, name="TFix", provenance=tst))
    batch.add_edge(Edge(src="py:app.core", dst="py:app.core.Widget", kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:app.core", dst="py:app.core.run", kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:tests.test_core", dst="py:tests.test_core.TFix", kind=EdgeKind.CONTAINS))
    return FactStore(batch)


def test_is_test_module() -> None:
    assert _is_test_module("tests.test_core") and _is_test_module("test_foo")
    assert not _is_test_module("orchestrator.sdlc.codegen")


def test_under_tests() -> None:
    n = Node(id="x", kind=NodeKind.TYPE, name="X", provenance=Provenance(file="tests/test_x.py", line=1))
    s = Node(id="y", kind=NodeKind.TYPE, name="Y", provenance=Provenance(file="src/app/y.py", line=1))
    assert _under_tests(n) and not _under_tests(s)


def test_architecture_excludes_test_modules() -> None:
    md = render_architecture(_store(), summarise_store(_store()), greenfield=False)
    assert "app.core` — 1 types, 1 functions" in md
    assert "tests.test_core" not in md  # source-only module map


def test_architecture_greenfield_note() -> None:
    md = render_architecture(FactStore(FactBatch()), summarise_store(FactStore(FactBatch())), greenfield=True)
    assert "Greenfield" in md


def test_domain_model_falls_back_to_source_types() -> None:
    md = render_domain_model(_store())
    assert "`Widget`" in md and "TFix" not in md  # test fixtures excluded


def test_glossary_excludes_tests() -> None:
    md = render_glossary(_store())
    assert "Widget" in md and "TFix" not in md


def test_tech_context_table() -> None:
    md = render_tech_context(ProjectProfile.from_repo("."), greenfield=False)
    assert "| Languages |" in md and "| Test runner |" in md


def test_memory_bank_dir_override(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert memory_bank_dir(tmp_path) == tmp_path / "memory-bank"
    monkeypatch.setenv("ORCHESTRATOR_MEMORY_BANK_DIR", str(tmp_path / "mb"))
    assert memory_bank_dir(tmp_path) == tmp_path / "mb"


def test_build_memory_bank_writes_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text(
        "class Widget:\n    pass\n\n\ndef run() -> int:\n    return 1\n"
    )
    result = build_memory_bank(tmp_path, refresh=True)
    mb = tmp_path / "memory-bank"
    assert mb.is_dir()
    for f in ("README.md", "architecture.md", "domain-model.md", "tech-context.md", "conventions.md"):
        assert (mb / f).is_file()
    assert not result["greenfield"]
    assert "Widget" in (mb / "domain-model.md").read_text()


def test_build_memory_bank_greenfield(tmp_path: Path) -> None:
    result = build_memory_bank(tmp_path, refresh=True)
    assert result["greenfield"] is True
    assert "Greenfield" in (tmp_path / "memory-bank" / "architecture.md").read_text()


def _repo_with_memory_bank(tmp_path: Path) -> Path:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text("class Widget:\n    pass\n")
    build_memory_bank(tmp_path, refresh=True)
    return tmp_path


def test_memory_bank_grounding_includes_domain_knowledge(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import memory_bank_grounding

    _repo_with_memory_bank(tmp_path)
    block = memory_bank_grounding(tmp_path)
    assert block.startswith("PROJECT KNOWLEDGE")
    assert "Widget" in block  # domain type surfaced


def test_memory_bank_grounding_absent(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import memory_bank_grounding

    assert memory_bank_grounding(tmp_path) == ""


def test_read_memory_bank_sections_and_section(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import read_memory_bank

    _repo_with_memory_bank(tmp_path)
    listing = read_memory_bank(tmp_path)
    assert listing["exists"] and "architecture.md" in listing["sections"]
    one = read_memory_bank(tmp_path, "domain-model")
    assert one["section"] == "domain-model.md" and "Widget" in one["content"]


def test_read_memory_bank_missing(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import read_memory_bank

    assert read_memory_bank(tmp_path)["exists"] is False


def test_grounder_includes_memory_bank(tmp_path: Path) -> None:
    from orchestrator.sdlc.grounding import PKGCodegenGrounder

    _repo_with_memory_bank(tmp_path)
    grounder = PKGCodegenGrounder.from_repo(tmp_path, use_cache=False)
    ctx = grounder.context_for_spec({"title": "add a Widget feature", "summary": "work with Widget"})
    assert "PROJECT KNOWLEDGE" in ctx  # committed memory bank fed into codegen grounding
