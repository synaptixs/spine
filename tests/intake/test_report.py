"""Unit tests for the traceability report renderer."""

from __future__ import annotations

from orchestrator.core.llm.client import CompletionResult
from orchestrator.core.llm.recording import TokenLedger
from orchestrator.intake.report import (
    intent_number,
    render_traceability_report,
    requirement_number,
    spec_number,
)

_INTENTS = [
    {
        "id": "intent-decide-stack",
        "title": "Decide Stack",
        "description": "Pick a stack.",
        "open_questions": ["Python or .NET?"],
    },
    {
        "id": "intent-implement-metadata-parsing",
        "title": "Implement Metadata Parsing",
        "description": "Parse HTML metadata.",
        "open_questions": [],
    },
]
_SPECS = [
    {
        "intent_id": "intent-decide-stack",
        "title": "Decide Stack",
        "summary": "Choose stack.",
        "user_story": "As a lead, I want a stack chosen.",
        "acceptance_criteria": [],
        "estimate": "S",
    },
    {
        "intent_id": "intent-implement-metadata-parsing",
        "title": "Implement Metadata Parsing",
        "summary": "Extract title/description/keywords.",
        "user_story": "As a user, I want metadata parsed.",
        "acceptance_criteria": ["parse(html) returns title", "missing tags yield None"],
        "estimate": "M",
    },
]


def _ledger() -> TokenLedger:
    led = TokenLedger()
    led.record(
        "intent_extraction",
        CompletionResult(
            text="",
            model="gpt-4o",
            prompt_tokens=1000,
            completion_tokens=200,
            cost_usd=0.02,
            latency_ms=1500.0,
        ),
    )
    led.record(
        "spec_writing",
        CompletionResult(
            text="",
            model="gpt-4o",
            prompt_tokens=4000,
            completion_tokens=900,
            cost_usd=0.08,
            latency_ms=8000.0,
        ),
    )
    led.record(
        "codegen",
        CompletionResult(
            text="",
            model="gpt-5-codex",
            prompt_tokens=6000,
            completion_tokens=3000,
            cost_usd=0.40,
            latency_ms=20000.0,
        ),
    )
    return led


def test_numbering_helpers() -> None:
    assert intent_number(1) == "I-01"
    assert requirement_number(12) == "R-12"
    assert spec_number(3) == "S-03"


def test_report_has_four_tables_and_traces() -> None:
    html = render_traceability_report(
        source_title="Buildable Intents",
        source_url="https://wiki/x/1234567891",
        intents=_INTENTS,
        specs=_SPECS,
        buildable_intent_ids=["intent-implement-metadata-parsing"],
        jira_keys={"intent-implement-metadata-parsing": "PROJ-5"},
        jira_browse_base="https://jira.example",
        ledger=_ledger(),
    )
    # Four sections.
    assert "1. Intents" in html
    assert "2. Requirements" in html
    assert "3. Design specs" in html
    assert "4. Token audit" in html
    # Source link present and used in intents table.
    assert "https://wiki/x/1234567891" in html
    # Traceability: design spec row carries I-02/R-02 refs and the Jira key.
    assert "I-02, R-02" in html
    assert "PROJ-5" in html
    assert "https://jira.example/browse/PROJ-5" in html
    # Test-criteria column carries the acceptance criteria as a list.
    assert "missing tags yield None" in html


def test_only_buildable_specs_appear_in_design_table() -> None:
    html = render_traceability_report(
        source_title="src",
        source_url="",
        intents=_INTENTS,
        specs=_SPECS,
        buildable_intent_ids=["intent-implement-metadata-parsing"],
        jira_keys={},
        jira_browse_base="https://j",
        ledger=_ledger(),
    )
    design = html.split("3. Design specs")[1]
    assert "S-01" in design  # the one buildable spec, renumbered from 1
    assert "Decide Stack" not in design  # non-buildable excluded from design table
    # But Decide Stack IS in the intents/requirements section.
    assert "Decide Stack" in html


def test_audit_total_row_present() -> None:
    html = render_traceability_report(
        source_title="src",
        source_url="",
        intents=_INTENTS,
        specs=_SPECS,
        buildable_intent_ids=[],
        jira_keys={},
        jira_browse_base="https://j",
        ledger=_ledger(),
    )
    assert "TOTAL" in html
    # Grand total tokens = 1000+200 + 4000+900 + 6000+3000 = 15,100
    assert "15,100" in html
