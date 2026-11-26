"""Autorun's research tool ranks with hybrid search (SSPN-430).

Hybrid search has been the default for ``investigate``, ``rca``, ``design``, ``plan`` and ``autorun`` since
3.70, but the tool graph-mode autorun runs for research, ``sdlc.investigate``, named no stage, so it kept the
lexical ranking while ``sdlc.rca`` beside it already used hybrid. It now names ``autorun``, and that stage's
off-switch reaches it.

What this does and does not change: the landing sites feed the evidence report, the blast radius handed to
design, the validity gate's landing check and the criteria badge. They do not pick the files the design
proposes; design takes the paths the spec states, or runs its own search.

The rest of the suite runs with hybrid search held off (``tests/conftest.py``); the tests marked
``hybrid_default`` here run at the shipped default. No model is loaded: the suite points the model lookup at
an empty directory, so the default is the three-leg ranking (name, content, graph).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.runtime.tool_registry import default_registry
from orchestrator.sdlc.evidence import _tool_investigate, build_evidence

# ``persist`` is named by neither the title nor the body; only its docstring says "accounting ledger". A
# ranking on symbol names cannot land it, and one that reads each symbol's own source text does.
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


def _landed(value: dict[str, Any]) -> list[str]:
    """The symbols research really lands on: a weak row is a reason to look, not a landing site."""
    return [str(row["name"]) for row in value["landing"] if not row["weak"]]


def test_with_hybrid_search_held_off_research_keeps_the_lexical_ranking(repo: tuple[FactStore, Path]) -> None:
    """Unmarked, so the suite holds hybrid off: the name-only ranking cannot reach ``persist``."""
    store, root = repo
    assert "persist" not in _landed(_tool_investigate(store=store, title=TITLE, problem=BODY, root=root))


@pytest.mark.hybrid_default
def test_by_default_research_lands_a_symbol_only_its_source_text_names(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    assert "persist" in _landed(_tool_investigate(store=store, title=TITLE, problem=BODY, root=root))


@pytest.mark.hybrid_default
async def test_the_registered_tool_autorun_runs_gives_the_same_answer(repo: tuple[FactStore, Path]) -> None:
    """Graph-mode autorun reaches the tool by its registry name, never by a direct call."""
    store, root = repo
    result = await default_registry().run(
        "sdlc.investigate", store=store, title=TITLE, problem=BODY, root=root
    )
    assert result.value == _tool_investigate(store=store, title=TITLE, problem=BODY, root=root)
    assert "persist" in _landed(result.value)


@pytest.mark.hybrid_default
async def test_the_evidence_built_from_it_lands_there_too(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    evidence = await build_evidence(TITLE, BODY, store=store, root=root)
    assert "persist" in [fact.name for fact in evidence.landing]
    assert evidence.files == ("billing.py",)


@pytest.mark.hybrid_default
@pytest.mark.parametrize("switch", ["SPINE_HYBRID_SEARCH", "SPINE_HYBRID_AUTORUN"])
def test_either_off_switch_returns_research_to_the_lexical_ranking(
    switch: str, repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = repo
    monkeypatch.setenv(switch, "0")
    assert "persist" not in _landed(_tool_investigate(store=store, title=TITLE, problem=BODY, root=root))


@pytest.mark.hybrid_default
@pytest.mark.parametrize("other", ["INVESTIGATE", "RCA", "DESIGN", "PLAN"])
def test_another_stages_off_switch_does_not_reach_research(
    other: str, repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research answers to ``autorun`` and to no other stage's switch."""
    store, root = repo
    monkeypatch.setenv(f"SPINE_HYBRID_{other}", "0")
    assert "persist" in _landed(_tool_investigate(store=store, title=TITLE, problem=BODY, root=root))
