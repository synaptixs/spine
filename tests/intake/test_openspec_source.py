"""OpenSpec source adapter — spec-driven intake (deterministic, no LLM)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.core.llm import CompletionResult, MockLLMClient
from orchestrator.intake.factory import SUPPORTED_SOURCE_KINDS, build_service_for
from orchestrator.intake.gaps import GapAnalyzer
from orchestrator.intake.intents import IntentExtractor, StructuredIntentSource
from orchestrator.intake.jira import JiraAdapter, JiraConfig
from orchestrator.intake.openspec_source import (
    OpenSpecSourceAdapter,
    OpenSpecSourceConfig,
    change_to_intent,
)
from orchestrator.intake.service import BacklogService, parse_source_uri
from orchestrator.intake.specs import SpecWriter

_PROPOSAL = """\
# Proposal: Add single-URL input

## Why
Users need to submit one page URL to assess. The MVP entry point.

## What Changes
Add a `parse_url(raw: str) -> Url` helper and a CLI flag `--url`.

## Impact
New module src/aeo/input.py; no breaking changes.
"""

_SPEC = """\
# Delta for Input

## ADDED Requirements

### Requirement: Single URL input
The system SHALL accept exactly one absolute http(s) URL via `--url`.

#### Scenario: Valid https URL
- GIVEN the CLI is invoked
- WHEN the user passes --url https://example.com/page
- THEN parse_url returns a Url with host "example.com"

#### Scenario: Rejects non-http scheme
- GIVEN the CLI is invoked
- WHEN the user passes --url ftp://x
- THEN a ValueError is raised
"""


def _write_change(
    root: Path, change_id: str, proposal: str, *, cap: str = "input", spec: str = _SPEC
) -> None:
    d = root / "changes" / change_id
    (d / "specs" / cap).mkdir(parents=True, exist_ok=True)
    (d / "proposal.md").write_text(proposal, encoding="utf-8")
    (d / "specs" / cap / "spec.md").write_text(spec, encoding="utf-8")


def _reply(text: str) -> CompletionResult:
    return CompletionResult(
        text=text, model="mock", prompt_tokens=0, completion_tokens=0, cost_usd=0.0, latency_ms=0.0
    )


# --- parser (pure) ---------------------------------------------------------


def test_change_to_intent_maps_requirements_and_scenarios_to_criteria() -> None:
    intent = change_to_intent("add-url-input", proposal_md=_PROPOSAL, spec_texts=(_SPEC,))
    assert intent.id == "intent-add-url-input"
    assert intent.title == "Add single-URL input"  # H1 with the "Proposal:" prefix stripped
    assert "submit one page URL" in intent.description  # ## Why
    assert "parse_url" in intent.scope  # ## What Changes
    # the SHALL statement + each scenario land as VERBATIM acceptance criteria
    assert any("SHALL accept exactly one absolute http(s) URL" in c for c in intent.acceptance_criteria)
    assert any("Scenario: Valid https URL" in c and "example.com" in c for c in intent.acceptance_criteria)
    assert any("Rejects non-http scheme" in c and "ValueError" in c for c in intent.acceptance_criteria)


def test_change_to_intent_tolerates_section_name_variants() -> None:
    # older OpenSpec proposals use ## Intent / ## Scope / ## Approach
    proposal = "# Change: Foo\n\n## Intent\nWhy foo.\n\n## Scope\nIn scope: foo.\n\n## Approach\nUse bar.\n"
    intent = change_to_intent("foo", proposal_md=proposal, spec_texts=())
    assert intent.title == "Foo"
    assert intent.description == "Why foo."
    assert "In scope: foo." in intent.scope and "Approach: Use bar." in intent.scope


def test_change_to_intent_no_specs_yields_no_criteria() -> None:
    intent = change_to_intent("bare", proposal_md="# Proposal: Bare\n\n## Why\nx\n")
    assert intent.acceptance_criteria == [] and intent.title == "Bare"


# --- adapter ---------------------------------------------------------------


def _adapter(tmp_path: Path) -> OpenSpecSourceAdapter:
    return OpenSpecSourceAdapter(OpenSpecSourceConfig(root=tmp_path / "openspec"))


async def test_fetch_tree_all_changes_excludes_archive(tmp_path: Path) -> None:
    root = tmp_path / "openspec"
    _write_change(root, "add-url-input", _PROPOSAL)
    _write_change(root, "add-crawler", "# Proposal: Add crawler\n\n## Why\nfetch pages\n")
    (root / "changes" / "archive" / "old").mkdir(parents=True)
    (root / "changes" / "archive" / "old" / "proposal.md").write_text("# archived\n")

    res = await _adapter(tmp_path).fetch_tree("")  # empty root → every change
    ids = sorted(d.id for d in res.documents)
    assert ids == ["add-crawler", "add-url-input"]  # archive excluded


async def test_fetch_tree_single_change(tmp_path: Path) -> None:
    root = tmp_path / "openspec"
    _write_change(root, "add-url-input", _PROPOSAL)
    res = await _adapter(tmp_path).fetch_tree("add-url-input")
    assert [d.id for d in res.documents] == ["add-url-input"]
    assert res.documents[0].labels == ("openspec", "change")


async def test_structured_intents_and_seam(tmp_path: Path) -> None:
    root = tmp_path / "openspec"
    _write_change(root, "add-url-input", _PROPOSAL)
    ad = _adapter(tmp_path)
    assert isinstance(ad, StructuredIntentSource)  # the seam analyze() branches on
    res = await ad.fetch_tree("")
    intents = ad.structured_intents(res.documents)
    assert [i.id for i in intents] == ["intent-add-url-input"]
    assert intents[0].acceptance_criteria  # criteria parsed, not LLM-guessed


# --- wiring ----------------------------------------------------------------


def test_parse_source_uri_openspec() -> None:
    assert parse_source_uri("openspec://") == ("openspec", "")  # all changes
    assert parse_source_uri("openspec://add-url-input") == ("openspec", "add-url-input")


def test_openspec_is_a_supported_kind() -> None:
    assert "openspec" in SUPPORTED_SOURCE_KINDS
    assert isinstance(build_service_for("openspec://", dry_run=True), BacklogService)  # no creds needed


# --- analyze uses the structured path (skips the LLM extractor) ------------


async def test_analyze_skips_llm_extractor_for_openspec(tmp_path: Path) -> None:
    root = tmp_path / "openspec"
    _write_change(root, "add-url-input", _PROPOSAL)
    # A boom-extractor: an empty script raises if ever invoked, proving the structured
    # path bypasses the LLM extractor. The spec writer still runs (enrich) — its mock
    # returns trivial JSON, and the OpenSpec criteria still flow through verbatim.
    service = BacklogService(
        source=_adapter(tmp_path),
        extractor=IntentExtractor(MockLLMClient(script=[])),  # would raise if called
        analyzer=GapAnalyzer(),
        spec_writer=SpecWriter(MockLLMClient(script=[_reply("{}")])),
        tracker=JiraAdapter(JiraConfig(dry_run=True)),
    )
    plan = await service.analyze("")  # no MissingFixtureError ⇒ extractor never called
    assert [i.id for i in plan.intents] == ["intent-add-url-input"]
    assert plan.specs and any(
        "SHALL accept exactly one" in c for c in plan.specs[0].acceptance_criteria
    )  # criteria survived intent → spec, no LLM extraction
