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
from orchestrator.sdlc.autorun import RunContext, _research_pass
from orchestrator.sdlc.evidence import (
    Evidence,
    LandingFact,
    _tool_investigate,
    build_evidence,
    render_evidence_md,
    to_dict,
)

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
    """Unmarked, so the suite holds hybrid off: the name-only ranking cannot reach ``persist``.

    The lexical ranking lands nothing at all for this ticket, so the missing ``persist`` alone would also pass
    on a hybrid ranking that returned nothing. No legs named is what says the lexical ranking ran.
    """
    store, root = repo
    value = _tool_investigate(store=store, title=TITLE, problem=BODY, root=root)
    assert "retrieval" not in value
    assert "persist" not in _landed(value)


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
    value = _tool_investigate(store=store, title=TITLE, problem=BODY, root=root)
    assert "retrieval" not in value
    assert "persist" not in _landed(value)


@pytest.mark.hybrid_default
@pytest.mark.parametrize("other", ["INVESTIGATE", "RCA", "DESIGN", "PLAN"])
def test_another_stages_off_switch_does_not_reach_research(
    other: str, repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research answers to ``autorun`` and to no other stage's switch."""
    store, root = repo
    monkeypatch.setenv(f"SPINE_HYBRID_{other}", "0")
    assert "persist" in _landed(_tool_investigate(store=store, title=TITLE, problem=BODY, root=root))


# --- the evidence says what ranked it ---------------------------------------------------------------------

# A ticket the lexical ranking does land (``render`` is in its words), for the tests that run with hybrid off.
PLAIN_TITLE, PLAIN_BODY = "render record fails", "the render function fails on a record"
FOUR_LEGS = "name, content, meaning (BAAI/bge-small-en-v1.5@5c38ec7c), graph"
# The serialised shape before this change. With hybrid off it must not move, so the off-switch is a rollback.
_EVIDENCE_KEYS = {
    "areas",
    "blast_radius",
    "files",
    "grounded",
    "issue_type",
    "landing",
    "problem",
    "rca",
    "recently_changed",
    "title",
}
_EVIDENCE_ROW = {"name", "where", "kind", "callers", "module"}
_TOOL_ROW = _EVIDENCE_ROW | {"score", "matched", "weak"}


@pytest.mark.hybrid_default
def test_the_tool_names_the_legs_and_what_found_each_landing(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    value = _tool_investigate(store=store, title=TITLE, problem=BODY, root=root)
    assert value["retrieval"] == "name, content, graph"
    persist = next(row for row in value["landing"] if row["name"] == "persist")
    assert "content" in persist["found_by"]


def test_with_hybrid_off_the_tool_value_keeps_the_shape_it_always_had(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    value = _tool_investigate(store=store, title=PLAIN_TITLE, problem=PLAIN_BODY, root=root)
    assert set(value) == {"landing", "areas", "grounded"}
    assert value["landing"] and all(set(row) == _TOOL_ROW for row in value["landing"])


@pytest.mark.hybrid_default
async def test_the_evidence_and_its_serialised_form_carry_both(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    evidence = await build_evidence(TITLE, BODY, store=store, root=root)
    assert evidence.retrieval == "name, content, graph"
    persist = next(fact for fact in evidence.landing if fact.name == "persist")
    assert "content" in persist.found_by

    data = to_dict(evidence)
    assert data["retrieval"] == evidence.retrieval
    assert next(row for row in data["landing"] if row["name"] == "persist")["found_by"] == persist.found_by


async def test_with_hybrid_off_the_serialised_evidence_keeps_the_shape_it_always_had(
    repo: tuple[FactStore, Path],
) -> None:
    store, root = repo
    evidence = await build_evidence(PLAIN_TITLE, PLAIN_BODY, store=store, root=root)
    assert evidence.retrieval == ""
    data = to_dict(evidence)
    assert set(data) == _EVIDENCE_KEYS
    assert data["landing"] and all(set(row) == _EVIDENCE_ROW for row in data["landing"])


def _evidence(retrieval: str, found_by: str) -> Evidence:
    fact = LandingFact(
        name="persist", where="billing.py:1", kind="Function", callers=0, module="billing", found_by=found_by
    )
    return Evidence(title="t", landing=(fact,), files=("billing.py",), grounded=True, retrieval=retrieval)


def test_without_a_hybrid_ranking_the_evidence_keeps_its_original_promise() -> None:
    """The whole line, byte for byte as it was before SSPN-430: with hybrid off the report must not move."""
    md = render_evidence_md(_evidence("", ""))
    assert md.splitlines()[2] == (
        "_Deterministic: the graph, the call sites and git history answer here. No model ran, "
        "and the same commit produces the same bytes._"
    )
    assert "hybrid search" not in md
    assert "found by" not in md


def test_three_legs_say_no_language_model_was_called_and_why_meaning_is_off() -> None:
    md = render_evidence_md(_evidence("name, content, graph", "content"))
    assert "No language model was called." in md
    assert "ranked by hybrid search (name, content, graph)." in md
    assert "The meaning leg is off" in md
    assert "`semantic` extra" in md
    assert "`SPINE_EMBED_MODEL=none`" in md
    assert "The same commit produces the same bytes._" in md
    assert "No model ran" not in md
    assert "found by: content" in md


def test_four_legs_name_the_pinned_model_and_tie_the_bytes_to_it() -> None:
    md = render_evidence_md(_evidence(FOUR_LEGS, "meaning"))
    assert "No language model was called." in md
    assert (
        "ranked by hybrid search (name, content, meaning, graph) using the pinned local embedding model "
        "`BAAI/bge-small-en-v1.5@5c38ec7c`." in md
    )
    assert "The same commit and the same model produce the same bytes._" in md
    assert "The meaning leg is off" not in md
    assert "No model ran" not in md


def _run_context(root: Path, title: str, summary: str) -> RunContext:
    return RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=root,
        artifacts_dir=root / "artifacts",
        issue_key="SPN-1",
        spec={"title": title, "summary": summary, "acceptance_criteria": []},
    )


@pytest.mark.hybrid_default
async def test_the_run_says_on_its_research_line_which_legs_ranked(repo: tuple[FactStore, Path]) -> None:
    store, root = repo
    lines: list[str] = []
    await _research_pass(_run_context(root, TITLE, BODY), store=store, issue_type="Bug", emit=lines.append)
    research = next(line for line in lines if line.startswith("[research]") and "landing site" in line)
    assert "hybrid search (name, content, graph)" in research


async def test_with_hybrid_off_the_research_line_says_the_ranking_was_lexical(
    repo: tuple[FactStore, Path],
) -> None:
    store, root = repo
    lines: list[str] = []
    ctx = _run_context(root, PLAIN_TITLE, PLAIN_BODY)
    await _research_pass(ctx, store=store, issue_type="Bug", emit=lines.append)
    research = next(line for line in lines if line.startswith("[research]") and "landing site" in line)
    assert "ranked lexically, rca" in research
    assert "hybrid search" not in research
