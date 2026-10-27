"""Reads that follow a repository's own files never follow a symlink out of it.

A listed repository on a shared server is content other people push. A link committed as
``episteme/glossary.md -> /proc/self/environ`` would hand the server's environment to whoever
calls ``pkg_grounding`` — past the repository list, which only resolves the path a caller
names, not the links inside it. The code and doc walkers already refuse links that leave the
root (``extractor.symlink_admitted``); these are the readers that did not. Found by the P5
security review (SSPN-258).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.intake.file_source import FileSourceAdapter
from orchestrator.knowledge.access import memory_bank_grounding, read_memory_bank
from orchestrator.sdlc.design import design_bank


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("TOKEN=hunter2\n")
    (outside / "glossary.md").write_text("TOKEN=hunter2\n")
    root = tmp_path / "repo"
    (root / "episteme").mkdir(parents=True)
    (root / "docs" / "guide").mkdir(parents=True)
    (root / "README.md").write_text("# Repo\n")
    (root / "docs" / "guide" / "intro.md").write_text("# Intro\nreal page\n")
    return root


def test_grounding_skips_a_bank_page_that_links_out(repo: Path) -> None:
    os.symlink(repo.parent / "outside" / "secret.md", repo / "episteme" / "glossary.md")
    (repo / "episteme" / "domain-model.md").write_text("# Domain\nOrders have lines.\n")
    text = memory_bank_grounding(repo)
    assert "Orders have lines" in text and "hunter2" not in text


def test_grounding_keeps_a_link_that_stays_inside(repo: Path) -> None:
    (repo / "docs" / "terms.md").write_text("# Terms\nA line is a row.\n")
    os.symlink(repo / "docs" / "terms.md", repo / "episteme" / "glossary.md")
    assert "A line is a row" in memory_bank_grounding(repo)


def test_a_bank_directory_that_links_out_is_no_bank(repo: Path) -> None:
    (repo / "episteme").rmdir()
    os.symlink(repo.parent / "outside", repo / "episteme")
    assert memory_bank_grounding(repo) == ""
    out = read_memory_bank(repo)
    assert out["exists"] is False and out["sections"] == []
    section = read_memory_bank(repo, "secret")
    assert section.get("content") is None and "hunter2" not in str(section)


def test_the_bank_index_is_not_read_through_a_link_out(repo: Path) -> None:
    os.symlink(repo.parent / "outside" / "secret.md", repo / "episteme" / "README.md")
    out = read_memory_bank(repo)
    assert out["exists"] is True and "hunter2" not in out["index"]


def test_design_skips_a_bank_page_that_links_out(repo: Path) -> None:
    os.symlink(repo.parent / "outside" / "secret.md", repo / "episteme" / "conventions.md")
    (repo / "episteme" / "tech-context.md").write_text("# Tech\nPython.\n")
    pages = design_bank(repo)
    assert "tech-context.md" in pages and "conventions.md" not in pages


async def test_a_file_source_walk_does_not_follow_links_out_of_the_tree(repo: Path) -> None:
    docs = repo / "docs"
    os.symlink(repo.parent / "outside" / "secret.md", docs / "leak.md")
    os.symlink(repo.parent / "outside", docs / "elsewhere")
    os.symlink(docs / "guide" / "intro.md", docs / "alias.md")  # inside the tree: kept
    adapter = FileSourceAdapter()
    result = await adapter.fetch_tree(str(docs))
    bodies = " ".join(d.body for d in result.documents)
    assert "hunter2" not in bodies
    assert bodies.count("real page") == 2  # the page and its in-tree alias


def test_an_operator_set_bank_outside_the_repo_is_still_read(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ORCHESTRATOR_MEMORY_BANK_DIR`` is the operator's choice, not a committed link: a bank
    kept outside the repository on purpose is read as before."""
    bank = tmp_path / "banks" / "repo"
    bank.mkdir(parents=True)
    (bank / "domain-model.md").write_text("# Domain\nOperator bank.\n")
    (bank / "tech-context.md").write_text("# Tech\nGo.\n")
    (bank / "README.md").write_text("# Index\n")
    monkeypatch.setenv("ORCHESTRATOR_MEMORY_BANK_DIR", str(bank))
    assert "Operator bank" in memory_bank_grounding(repo)
    out = read_memory_bank(repo)
    assert out["exists"] is True and "Index" in out["index"]
    assert "tech-context.md" in design_bank(repo)
    # ...and a link out of *that* directory is still refused.
    os.symlink(repo.parent / "outside" / "secret.md", bank / "glossary.md")
    assert "hunter2" not in memory_bank_grounding(repo)
