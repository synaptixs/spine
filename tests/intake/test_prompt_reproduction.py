"""B94 (SSPN-202): intake is told what a bug report's criteria are, and where the code goes.

Intake's prompts said "copy each stated criterion VERBATIM" — a rule written for specs that state
criteria. A bug report states none, so the model copied what looked like requirements: lines of the
reporter's code, a session, a traceback. Those parked 17 of 100 P6 issues at the validity gate. The
prompts now say what a bug report's criteria are (what SHOULD happen, in the reporter's words), send
the reporter's code to a ``reproduction`` field, and ask the spec writer to propose testable
criteria from the report's own words, each with the sentence it rests on.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.intake import intents as intents_mod
from orchestrator.intake import specs as specs_mod
from orchestrator.intake.intents import Intent, IntentExtractor
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec, SpecWriter

CALL = "voter.fit(X, y, sample_weight=np.ones(y.shape))"
EXPECTED = "fit should not fail when an estimator is None."


def _llm(payload: dict[str, Any]) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=json.dumps(payload),
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


# ---- the extraction prompt ------------------------------------------------------------------


def test_the_extractor_prompt_says_a_bug_report_has_no_criteria_section_and_what_to_take() -> None:
    prompt = intents_mod._SYSTEM_PROMPT
    assert "BUG REPORT" in prompt
    assert "SHOULD happen" in prompt
    assert "NEVER put code" in prompt and "reproduction" in prompt
    assert "VERBATIM" in prompt, "the provenance rule stays"


def test_the_extractor_may_return_a_reproduction_field() -> None:
    item = intents_mod._SUBMIT_TOOL.parameters["properties"]["intents"]["items"]["properties"]
    assert "reproduction" in item
    assert "reproduction" in item["acceptance_criteria"]["description"]
    assert '"reproduction"' in intents_mod._SYSTEM_PROMPT, "and the JSON example shows it"


@pytest.mark.asyncio
async def test_reproduction_is_kept_apart_from_the_criteria() -> None:
    payload = {
        "intents": [
            {
                "title": "Fix fit with a None estimator",
                "description": "d",
                "acceptance_criteria": [EXPECTED, CALL],  # the model slipped: a code line
                "reproduction": [CALL, "AttributeError: 'NoneType' object has no attribute 'fit'"],
                "source_title": "issue",
            }
        ]
    }
    docs = [SourceDocument(id="1", title="issue", body="body")]
    intent = (await IntentExtractor(_llm(payload)).extract(docs))[0]
    assert intent.acceptance_criteria == [EXPECTED]
    assert intent.reproduction == [CALL, "AttributeError: 'NoneType' object has no attribute 'fit'"]


def test_an_intent_without_reproduction_serialises_exactly_as_before() -> None:
    assert "reproduction" not in Intent(id="i", title="t").model_dump()


# ---- the spec writer ------------------------------------------------------------------------


def test_the_spec_writer_is_told_to_propose_criteria_from_the_reports_own_words() -> None:
    prompt = specs_mod._SYSTEM_PROMPT
    assert "BUG REPORTS" in prompt
    assert "Given" in prompt and "when" in prompt and "then" in prompt
    assert "[from:" in prompt, "each proposed criterion names the sentence it rests on"
    assert "AFTER the verbatim ones" in prompt, "the stated-first rule stays"


def test_the_spec_writer_is_shown_the_reproduction_as_examples() -> None:
    intent = Intent(id="i", title="t", reproduction=[CALL])
    message = SpecWriter(_llm({}))._build_user_message(intent)
    assert "REPRODUCTION" in message and CALL in message
    assert "not requirements" in message


def test_no_reproduction_block_when_the_intent_has_none() -> None:
    message = SpecWriter(_llm({}))._build_user_message(Intent(id="i", title="t"))
    assert "REPRODUCTION" not in message


@pytest.mark.asyncio
async def test_the_spec_carries_stated_proposed_and_reproduction_apart() -> None:
    intent = Intent(id="i", title="t", acceptance_criteria=[EXPECTED], reproduction=[CALL])
    inferred = (
        "Given a VotingClassifier with one estimator set to None, when fit is called with sample_weight, "
        "then it completes without error. "
        '[from: "will fail at fit if weights are passed and an estimator is None"]'
    )
    spec = await SpecWriter(_llm({"summary": "s", "acceptance_criteria": [EXPECTED, inferred]})).write(intent)
    assert spec.acceptance_criteria == [EXPECTED]
    assert spec.proposed_criteria == [inferred]
    assert spec.reproduction == [CALL]


@pytest.mark.asyncio
async def test_the_minimal_spec_fallback_keeps_the_reproduction_too() -> None:
    intent = Intent(id="i", title="t", acceptance_criteria=[EXPECTED], reproduction=[CALL])
    client = MockLLMClient()

    async def junk(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text="not json", model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        )

    client.complete = junk  # type: ignore[method-assign]
    spec = await SpecWriter(client).write(intent)
    assert spec.reproduction == [CALL] and spec.acceptance_criteria == [EXPECTED]


# ---- a quote is only a quote when the report says it ----------------------------------------
#
# Replay on 25 P6 issues: of 81 `[from: "..."]` tags, 43 were exact substrings of the report, 13
# more once JSON escapes and ellipses were undone, and 20 (25%) were paraphrases or text from the
# intent's own scope presented as a quote. A forged provenance tag is worse than none, so the
# service checks each one against the source text and relabels the ones that are not there.

REPORT = (
    "Voting estimator will fail at fit if weights are passed and an estimator is None\n"
    "Because we don't check for an estimator to be `None` in `sample_weight` support, `fit` is failing`."
)


def _with(proposed: str):  # type: ignore[no-untyped-def]
    from orchestrator.intake.specs import verify_source_quotes

    spec = FeatureSpec(intent_id="i", title="t", proposed_criteria=[proposed])
    return verify_source_quotes(spec, REPORT).proposed_criteria[0]


def test_a_quote_that_is_in_the_report_is_kept_as_written() -> None:
    text = 'Given X, when fit is called, then it works. [from: "will fail at fit if weights are passed"]'
    assert _with(text) == text


def test_a_quote_survives_json_escapes_and_whitespace_differences() -> None:
    text = 'Given X, then it works. [from: "Voting estimator will fail at fit\\nif weights are passed"]'
    assert _with(text) == text


def test_an_ellipsis_joins_two_real_fragments() -> None:
    text = 'Given X, then it works. [from: "Voting estimator will fail ... an estimator is None"]'
    assert _with(text) == text


def test_a_paraphrase_is_relabelled_not_left_looking_like_a_quote() -> None:
    out = _with('Given X, then it works. [from: "fit must skip estimators that are None"]')
    assert out == "Given X, then it works. [from: paraphrase, not a quote of the report]"


def test_a_criterion_without_a_tag_is_untouched() -> None:
    assert _with("Given X, then it works.") == "Given X, then it works."


def test_the_service_checks_quotes_against_the_documents_it_read() -> None:
    import inspect

    from orchestrator.intake import service

    assert "verify_source_quotes" in inspect.getsource(service.BacklogService.analyze)
