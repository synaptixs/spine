"""Regression eval: a single-feature ticket must yield exactly ONE intent.

Run #16's live lesson: the extractor split "add median_call_count + its
tests" into two intents despite the FIDELITY rule; the ``--max-features``
cap contained it, but the second intent was silently dropped. This eval
pins the split discipline against a real provider.

Real-LLM eval (prompt behaviour can't be validated with a mock):
``pytest tests/intake/test_intent_split_eval.py -m real_llm`` — needs
ANTHROPIC_API_KEY or OPENAI_API_KEY (honors ORCHESTRATOR_INTAKE_MODEL).
Skipped in the default suite.
"""

from __future__ import annotations

import os

import pytest

from orchestrator.intake.intents import IntentExtractor
from orchestrator.intake.source import SourceDocument

# Mirrors the run-#16/#17 ticket shape: one function + its tests + quality
# gates, all in one ticket. Exactly one buildable capability.
_SINGLE_FEATURE_TICKET = SourceDocument(
    id="eval-1",
    title="SDLC Ticket: median call count for PKG graph statistics",
    body=(
        "The module src/orchestrator/pkg/stats.py aggregates statistics over an "
        "extracted fact graph (FunctionCallFrequency, GraphStats, summarise). "
        "Requirement: modify the existing file src/orchestrator/pkg/stats.py to add "
        "a module-level function median_call_count(frequencies: "
        "list[FunctionCallFrequency]) -> float returning the median of the "
        "call_count values (0.0 for an empty list; the input list is not mutated).\n"
        "Acceptance criteria:\n"
        "- median_call_count is added to the existing src/orchestrator/pkg/stats.py; "
        "no new module is created.\n"
        "- Odd-length lists return the middle value; even-length lists the mean of "
        "the two middle values.\n"
        "- median_call_count([]) returns 0.0.\n"
        "- Tests are added to the existing test_stats.py file; all existing tests "
        "keep passing.\n"
        "Technical notes: surgical change to the two existing files only. No new "
        "dependencies. Full type annotations (mypy --strict) and ruff-clean "
        "formatting, as CI enforces both."
    ),
    url="",
    space="EVAL",
)


@pytest.mark.real_llm
async def test_single_feature_ticket_yields_one_intent() -> None:
    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient

    # Loaded lazily here, NOT at collection time — a module-level .env load
    # would leak CONFLUENCE_*/JIRA_* into the "unconfigured adapter" tests.
    load_local_env()
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("no LLM provider key in env/.env")
    model = os.getenv("ORCHESTRATOR_INTAKE_MODEL")
    extractor = IntentExtractor(LiteLLMClient(), **({"model": model} if model else {}))

    intents = await extractor.extract([_SINGLE_FEATURE_TICKET])

    titles = [i.title for i in intents]
    assert len(intents) == 1, f"expected ONE intent, got {len(intents)}: {titles}"
    intent = intents[0]
    # The file path must survive extraction verbatim (run #15's lesson).
    text = (intent.description + " " + intent.scope).lower()
    assert "src/orchestrator/pkg/stats.py" in text, "intent lost the concrete file path the ticket named"
    # The API contract must survive into acceptance_criteria (run #23's lesson):
    # the ticket names median_call_count; without it codegen invents its own API.
    criteria_blob = " ".join(intent.acceptance_criteria).lower()
    assert "median_call_count" in criteria_blob, (
        f"intent dropped the stated API contract; criteria={intent.acceptance_criteria}"
    )


@pytest.mark.real_llm
async def test_api_contract_survives_intent_to_spec() -> None:
    """End-to-end (run #23): the named function/signature the ticket states
    must reach the FeatureSpec's acceptance_criteria, so codegen targets the
    specified API instead of inventing one."""
    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient
    from orchestrator.intake.specs import SpecWriter

    load_local_env()
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("no LLM provider key in env/.env")
    model = os.getenv("ORCHESTRATOR_INTAKE_MODEL")
    kw = {"model": model} if model else {}
    extractor = IntentExtractor(LiteLLMClient(), **kw)
    writer = SpecWriter(LiteLLMClient(), **kw)

    intents = await extractor.extract([_SINGLE_FEATURE_TICKET])
    assert intents, "no intent extracted"
    spec = await writer.write(intents[0])

    blob = " ".join(spec.acceptance_criteria).lower()
    assert "median_call_count" in blob, (
        f"spec acceptance_criteria lost the API contract: {spec.acceptance_criteria}"
    )
