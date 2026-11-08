"""RCA on a prose bug report (D12): with no traceback frame, hybrid search ranks where to look — labelled
as such.

Before this, a bug report with no resolvable frame got the first file its text named, or nothing: the
majority of bug
tickets arrive as prose. With the ``rca`` stage on, the report's own words, its exception class and its
stated files
seed hybrid search; a real traceback frame still wins and is untouched.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.sdlc.rca import build_rca, render_rca_md


@pytest.fixture
def repo(tmp_path: Path) -> tuple[FactStore, Path]:
    (tmp_path / "ledger.py").write_text(
        "class LedgerError(Exception):\n    pass\n\n\n"
        "def append_row(rows, row):\n"
        '    """Append one row to the accounting ledger."""\n'
        "    if not row:\n        raise LedgerError(row)\n    rows.append(row)\n"
    )
    (tmp_path / "render.py").write_text("def render(page):\n    return str(page)\n")
    return FactStore(RepoCodeExtractor().extract(tmp_path)), tmp_path


_PROSE = (
    "Saving an empty row now fails with LedgerError: empty row, and the accounting ledger loses the entry."
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "0")  # held off; a test that wants it on says so
    monkeypatch.delenv("SPINE_HYBRID_RCA", raising=False)


def test_held_off_the_prose_report_gets_no_ranking(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    report = asyncio.run(build_rca(_PROSE, store=store, root=root))
    assert report.ranked_files == []
    assert "not traced" not in render_rca_md(report)


def test_on_the_prose_report_is_ranked_and_says_it_was_not_traced(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    report = asyncio.run(build_rca(_PROSE, store=store, root=root))
    assert report.ranked_files[0] == "ledger.py"
    assert len(report.ranked_by) == len(report.ranked_files)
    md = render_rca_md(report)
    assert "**not traced**" in md and "`ledger.py`" in md
    # Nothing traced and nothing named: the top ranked file stands in for the regression checks.
    assert report.fault_module == "ledger.py"
    assert report.fault_origin == "ranked"  # B161: never presented as named by the ticket


def test_a_resolved_traceback_still_wins_and_is_not_ranked(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    trace = (
        "Traceback (most recent call last):\n"
        f'  File "{root}/ledger.py", line 8, in append_row\n'
        "    raise LedgerError(row)\nledger.LedgerError: ''\n"
    )
    report = asyncio.run(build_rca(trace, store=store, root=root))
    assert report.fault_site.startswith("append_row")
    assert report.ranked_files == []


def test_the_rca_stage_can_be_held_back(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    monkeypatch.setenv("SPINE_HYBRID_RCA", "0")
    assert asyncio.run(build_rca(_PROSE, store=store, root=root)).ranked_files == []


def test_the_ranked_report_names_the_embedding_model_when_one_ranked_it(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D17: with the semantic extra the ranking depends on the pinned model, so the report says which one."""
    store, root = repo
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    md = render_rca_md(asyncio.run(build_rca(_PROSE, store=store, root=root)))
    assert "stand-in" not in md and "embedding model" not in md
    monkeypatch.setenv("SPINE_EMBED_MODEL", "stand-in")
    md = render_rca_md(asyncio.run(build_rca(_PROSE, store=store, root=root)))
    assert "Ranked by hybrid search with the embedding model `stand-in/hash-64`, **not traced**" in md
