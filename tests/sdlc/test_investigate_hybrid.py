"""Hybrid search behind its switches (D11, D14): on per stage, and byte-identical to lexical when held off.

Hybrid search is on by default since every stage passed its pre-registered bar (P6; the default itself is
pinned in ``tests/pkg/test_hybrid_switch_on.py``). Here ``SPINE_HYBRID_SEARCH=0`` holds it off and ``=1``
turns it on, so each test states the switch it depends on; a stage can still be held back with
``SPINE_HYBRID_<STAGE>=0``, and a caller that names no stage never gets it. When it does run, a landing says
what found it, and the brief says how it was ranked instead of calling it lexical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.hybrid import hybrid_enabled
from orchestrator.sdlc.design import _landing_files
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

_BILLING = '''\
def persist(record, path):
    """Append one row to the accounting ledger."""
    with open(path, "a") as fh:
        fh.write(record)


def render(record):
    return str(record)
'''

TITLE, BODY = "Ledger rows go missing", "Rows written to the accounting ledger are lost after a restart."


@pytest.fixture
def repo(tmp_path: Path) -> tuple[FactStore, Path]:
    (tmp_path / "billing.py").write_text(_BILLING)
    return FactStore(RepoCodeExtractor().extract(tmp_path)), tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SPINE_HYBRID_INVESTIGATE", "SPINE_HYBRID_DESIGN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "0")


def _brief(store: FactStore, root: Path, stage: str | None = None) -> str:
    return render_investigation_md(
        build_investigation(TITLE, BODY, store=store, root=root, rank_on_title=True, stage=stage)
    )


def test_held_off_the_brief_is_byte_identical_to_lexical(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    assert not hybrid_enabled("investigate")
    assert _brief(store, root, stage="investigate") == _brief(store, root)
    inv = build_investigation(TITLE, BODY, store=store, root=root, stage="investigate")
    assert inv.retrieval == "" and all(h.found_by == "" for h in inv.landing)


def test_switched_on_the_brief_finds_the_body_match_and_says_how(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    inv = build_investigation(TITLE, BODY, store=store, root=root, rank_on_title=True, stage="investigate")
    persist = next(h for h in inv.landing if h.name == "persist")
    assert "content" in persist.found_by
    md = render_investigation_md(inv)
    assert "found by: " in md
    assert "hybrid search (name, content, graph)" in md
    assert "Retrieval is **lexical**" not in md
    assert "no embedding model was loaded" in md


def test_a_stage_can_be_held_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    monkeypatch.setenv("SPINE_HYBRID_INVESTIGATE", "0")
    assert not hybrid_enabled("investigate")
    assert hybrid_enabled("design")


def test_a_caller_that_names_no_stage_never_gets_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    assert not hybrid_enabled(None)
    assert not hybrid_enabled("no-such-stage")


def test_design_proposes_the_body_match_only_when_its_stage_is_on(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    spec = {"title": TITLE, "description": BODY}
    assert _landing_files(spec, store, root) == []  # no symbol is named for the ledger
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    assert _landing_files(spec, store, root) == ["billing.py"]
    monkeypatch.setenv("SPINE_HYBRID_DESIGN", "0")
    assert _landing_files(spec, store, root) == []
