"""Block B.4 unit tests: SpecWriter parsing + degradation."""

from __future__ import annotations

import json as jsonlib

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.intake.intents import Intent
from orchestrator.intake.specs import SpecWriter


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def _intent() -> Intent:
    return Intent(
        id="intent-add-export",
        title="Add CSV export",
        description="Let users download data as CSV.",
        scope="CSV only.",
        dependencies=["data layer"],
        nfrs=["export <5s for 10k rows"],
        open_questions=["Which columns?"],
    )


async def test_write_parses_spec_fields() -> None:
    payload = {
        "summary": "Export grid data to CSV.",
        "user_story": "As an analyst, I want CSV export, so that I can use Excel.",
        "acceptance_criteria": ["Given 10k rows, when I click export, then a CSV downloads <5s."],
        "technical_notes": "Stream the response; resolve columns from the active view.",
        "nfrs": ["export <5s for 10k rows"],
        "dependencies": ["data layer"],
        "estimate": "m",
    }
    writer = SpecWriter(_llm_returning(jsonlib.dumps(payload)))
    spec = await writer.write(_intent())
    assert spec.intent_id == "intent-add-export"
    assert spec.title == "Add CSV export"  # carried from intent, not LLM
    assert spec.summary == "Export grid data to CSV."
    assert spec.acceptance_criteria[0].startswith("Given 10k rows")
    assert spec.estimate == "M"  # upper-cased


async def test_write_carries_intent_fields_when_llm_omits() -> None:
    payload = {"summary": "Just a summary.", "acceptance_criteria": []}
    writer = SpecWriter(_llm_returning(jsonlib.dumps(payload)))
    spec = await writer.write(_intent())
    # nfrs / dependencies fall back to the intent's when the LLM omits them
    assert spec.nfrs == ["export <5s for 10k rows"]
    assert spec.dependencies == ["data layer"]


async def test_write_degrades_to_minimal_spec_on_garbage() -> None:
    writer = SpecWriter(_llm_returning("the model produced prose, no json"))
    spec = await writer.write(_intent())
    assert spec.intent_id == "intent-add-export"
    assert spec.title == "Add CSV export"
    assert spec.summary == "Let users download data as CSV."  # intent description
    assert spec.acceptance_criteria == []


async def test_write_all_maps_one_to_one() -> None:
    payload = {"summary": "s", "acceptance_criteria": ["ac"]}
    writer = SpecWriter(_llm_returning(jsonlib.dumps(payload)))
    intents = [
        _intent(),
        Intent(id="intent-auth", title="Add SSO", description="Okta SSO."),
    ]
    specs = await writer.write_all(intents)
    assert [s.intent_id for s in specs] == ["intent-add-export", "intent-auth"]
    assert all(s.summary == "s" for s in specs)


def _intent_with_criteria() -> Intent:
    return Intent(
        id="intent-slack",
        title="Slack webhook notifier",
        description="Post approval-gate events to Slack.",
        acceptance_criteria=[
            "async notify_approval_raised(...) -> bool returns True on 2xx, False otherwise",
            "no exception escapes notify_approval_raised under any failure",
        ],
    )


async def test_stated_criteria_survive_when_llm_drops_them() -> None:
    # The model returns its own (wrong) criteria, omitting the stated ones.
    payload = {"summary": "s", "acceptance_criteria": ["sends a notification"]}
    writer = SpecWriter(_llm_returning(jsonlib.dumps(payload)))
    spec = await writer.write(_intent_with_criteria())
    # Stated criteria lead, verbatim; the model's extra follows.
    assert spec.acceptance_criteria[:2] == _intent_with_criteria().acceptance_criteria
    assert "sends a notification" in spec.acceptance_criteria


async def test_stated_criteria_not_double_listed() -> None:
    stated = _intent_with_criteria().acceptance_criteria
    # The model faithfully echoes the stated criteria (possibly re-spaced).
    payload = {"summary": "s", "acceptance_criteria": ["  ".join(stated[0].split()), stated[1]]}
    writer = SpecWriter(_llm_returning(jsonlib.dumps(payload)))
    spec = await writer.write(_intent_with_criteria())
    assert spec.acceptance_criteria == stated  # de-duped, no repeats


async def test_stated_criteria_survive_on_garbage_response() -> None:
    writer = SpecWriter(_llm_returning("not json at all"))
    spec = await writer.write(_intent_with_criteria())
    assert spec.acceptance_criteria == _intent_with_criteria().acceptance_criteria


async def test_spec_prompt_includes_stated_criteria() -> None:
    writer = SpecWriter(_llm_returning('{"summary": "s"}'))
    msg = writer._build_user_message(_intent_with_criteria())
    assert "STATED ACCEPTANCE CRITERIA" in msg
    assert "notify_approval_raised" in msg
