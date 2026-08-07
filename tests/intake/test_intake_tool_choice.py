"""Intake constrains its output with a forced tool call (SSPN-31).

A scripted fake ``LLMClient`` records the kwargs each stage sends, so these tests
assert on the *request* — a `tool_choice` naming the submit tool and no
`json_object=True` — not just on the parsed result, which would pass either way.
The graceful-degradation paths (unparseable output) and the `_merge_criteria`
guarantee are pinned here too, since this change routes through them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message, ToolCall
from orchestrator.intake.intents import Intent, IntentExtractor
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import SpecWriter, _merge_criteria


class _ScriptedLLM:
    """Records every ``complete`` kwarg and replays queued responses in order."""

    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: type[BaseModel] | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: object = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "response_format": response_format,
                "json_object": json_object,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return self._responses.pop(0)


def _result(text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        model="fake",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=tuple(tool_calls or []),
    )


def _docs() -> list[SourceDocument]:
    return [SourceDocument(id="doc-1", title="Export", body="Users need CSV export of invoices.")]


def _intent() -> Intent:
    return Intent(
        id="intent-add-csv-export",
        title="Add CSV export",
        description="Let users download invoices as CSV.",
        acceptance_criteria=["async export_csv(invoice_id: str) -> bytes returns the file body"],
        nfrs=["p95 under 500ms"],
        dependencies=["billing service"],
    )


def _names(tools: object) -> list[str]:
    assert isinstance(tools, list)
    return [t.name for t in tools]


# ---- the request the model receives (the bug this replaces) ----


async def test_intent_extraction_forces_the_submit_tool_call() -> None:
    llm = _ScriptedLLM(
        [_result(tool_calls=[ToolCall(id="1", name="submit_intents", arguments={"intents": []})])]
    )

    await IntentExtractor(llm, model="claude-opus-5").extract(_docs())

    (call,) = llm.calls
    assert call["tool_choice"] == "submit_intents"
    assert "submit_intents" in _names(call["tools"])
    # `response_format` is OpenAI/Ollama-only, so JSON mode is not the constraint any more.
    assert call["json_object"] is False


async def test_spec_writing_forces_the_submit_tool_call() -> None:
    llm = _ScriptedLLM(
        [_result(tool_calls=[ToolCall(id="1", name="submit_feature_spec", arguments={"summary": "s"})])]
    )

    await SpecWriter(llm, model="claude-opus-5").write(_intent())

    (call,) = llm.calls
    assert call["tool_choice"] == "submit_feature_spec"
    assert "submit_feature_spec" in _names(call["tools"])
    assert call["json_object"] is False


# ---- the forced call's arguments are parsed, not salvaged ----


async def test_intents_come_from_the_forced_call_arguments() -> None:
    args = {
        "intents": [
            {
                "title": "Add CSV export",
                "description": "Download invoices as CSV.",
                "scope": "src/orchestrator/pkg/stats.py only",
                "acceptance_criteria": ["export_csv returns bytes"],
                "source_title": "Export",
            }
        ]
    }
    llm = _ScriptedLLM(
        [_result(text="", tool_calls=[ToolCall(id="1", name="submit_intents", arguments=args)])]
    )

    intents = await IntentExtractor(llm).extract(_docs())

    assert [i.title for i in intents] == ["Add CSV export"]
    assert intents[0].id == "intent-add-csv-export"
    assert intents[0].scope == "src/orchestrator/pkg/stats.py only"
    assert intents[0].source_doc_ids == ["doc-1"]


async def test_spec_comes_from_the_forced_call_arguments() -> None:
    args = {
        "summary": "Export invoices as CSV.",
        "user_story": "As a user, I want CSV, so that I can reconcile.",
        "acceptance_criteria": ["headers are written first"],
        "technical_notes": "streams the response",
        "estimate": "m",
    }
    llm = _ScriptedLLM(
        [_result(text="", tool_calls=[ToolCall(id="1", name="submit_feature_spec", arguments=args)])]
    )

    spec = await SpecWriter(llm).write(_intent())

    assert spec.summary == "Export invoices as CSV."
    assert spec.technical_notes == "streams the response"
    assert spec.estimate == "M"
    # Stated criteria still lead, verbatim, ahead of anything the model added.
    assert spec.acceptance_criteria[0] == _intent().acceptance_criteria[0]
    assert "headers are written first" in spec.acceptance_criteria


# ---- a provider that ignores the tool still answers in text ----


async def test_a_text_answer_is_still_parsed_for_intents() -> None:
    payload = json.dumps({"intents": [{"title": "Add CSV export", "description": "d"}]})
    llm = _ScriptedLLM([_result(text=f"```json\n{payload}\n```")])

    intents = await IntentExtractor(llm).extract(_docs())

    assert [i.title for i in intents] == ["Add CSV export"]


async def test_a_text_answer_is_still_parsed_for_specs() -> None:
    payload = json.dumps({"summary": "from text", "acceptance_criteria": []})
    llm = _ScriptedLLM([_result(text=f"Here you go:\n{payload}")])

    spec = await SpecWriter(llm).write(_intent())

    assert spec.summary == "from text"


# ---- graceful degradation is unchanged ----


async def test_unparseable_output_yields_no_intents_rather_than_raising() -> None:
    llm = _ScriptedLLM([_result(text="I could not do that.")])

    assert await IntentExtractor(llm).extract(_docs()) == []


async def test_unparseable_output_yields_a_minimal_spec_carried_by_the_intent() -> None:
    intent = _intent()
    llm = _ScriptedLLM([_result(text="sorry, no JSON here")])

    spec = await SpecWriter(llm).write(intent)

    assert spec.intent_id == intent.id
    assert spec.title == intent.title
    assert spec.summary == intent.description
    assert spec.acceptance_criteria == intent.acceptance_criteria
    assert spec.nfrs == intent.nfrs
    assert spec.dependencies == intent.dependencies
    assert spec.estimate == ""


async def test_no_usable_documents_skips_the_llm_entirely() -> None:
    llm = _ScriptedLLM([])

    assert await IntentExtractor(llm).extract([SourceDocument(id="d", title="t", body="")]) == []
    assert llm.calls == []


# ---- _merge_criteria's guarantee is unchanged ----


@pytest.mark.parametrize(
    "produced",
    [
        [],
        ["The exporter writes a CSV file"],
    ],
)
async def test_a_stated_criterion_survives_a_model_that_drops_or_paraphrases_it(produced: list[str]) -> None:
    intent = _intent()
    stated = intent.acceptance_criteria[0]
    args = {"summary": "s", "acceptance_criteria": produced}
    llm = _ScriptedLLM([_result(tool_calls=[ToolCall(id="1", name="submit_feature_spec", arguments=args)])])

    spec = await SpecWriter(llm).write(intent)

    assert spec.acceptance_criteria[0] == stated
    for c in produced:
        assert c in spec.acceptance_criteria


def test_merge_criteria_leads_with_stated_and_dedupes_on_whitespace() -> None:
    stated = ["add(2, 3) returns 5", "raises TypeError on strings"]
    produced = ["add(2,  3)   returns 5", "logs the call"]

    merged = _merge_criteria(stated, produced)

    assert merged[: len(stated)] == stated
    assert merged == [*stated, "logs the call"]
