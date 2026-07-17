"""Regression tests for read_memory_bank path-traversal containment.

Confirmed finding (security review Phase 3): `section` is an untrusted MCP tool
argument concatenated into a filesystem path, so "../…" or an absolute path — or a
symlink inside the bank pointing out of it — reached read_text() and disclosed
arbitrary files. read_memory_bank must confine the resolved path to the bank dir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orchestrator.knowledge.access import read_memory_bank


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    bank = tmp_path / "episteme"
    bank.mkdir()
    (bank / "glossary.md").write_text("# real content", encoding="utf-8")
    # A secret that lives OUTSIDE the bank, as a sibling of the repo root.
    (tmp_path.parent / "outside-secret.md").write_text("TOP SECRET", encoding="utf-8")
    return tmp_path


def test_reads_a_legitimate_section(repo: Path) -> None:
    out = read_memory_bank(repo, section="glossary")
    assert out["content"] == "# real content"


def test_relative_traversal_is_blocked(repo: Path) -> None:
    # "../outside-secret" (+ .md) escapes the bank dir → must not be read.
    out = read_memory_bank(repo, section="../outside-secret")
    assert out["content"] is None


def test_absolute_path_is_blocked(repo: Path) -> None:
    secret = repo.parent / "outside-secret.md"
    # Path("bank") / "/abs/path" == Path("/abs/path"), so an absolute section escapes.
    out = read_memory_bank(repo, section=str(secret))
    assert out["content"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_symlink_escaping_the_bank_is_blocked(repo: Path) -> None:
    secret = repo.parent / "outside-secret.md"
    link = repo / "episteme" / "pwn.md"
    link.symlink_to(secret)
    out = read_memory_bank(repo, section="pwn")
    assert out["content"] is None
