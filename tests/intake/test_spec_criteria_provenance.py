"""SSPN-31: the spec writer partitions criteria by provenance.

Stated criteria stay in ``acceptance_criteria`` verbatim; anything the model
added lands in ``proposed_criteria``. Exercised through ``SpecWriter.write``
(and ``load_spec_file`` for the file path), not through ``_merge_criteria``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, LLMClient, Message, ToolSpec
from orchestrator.intake.intents import Intent
from orchestrator.intake.specs import FeatureSpec, SpecWriter
from orchestrator.sdlc.spec_file import SpecFileError, load_spec_file


class _ScriptedLLM:
    """Returns one canned JSON payload as the assistant's text answer."""

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self._text = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.0,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
        **_: Any,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        return CompletionResult(
            text=self._text,
            model=model or "fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


def _writer(payload: dict[str, Any] | str) -> SpecWriter:
    llm: LLMClient = _ScriptedLLM(payload)  # type: ignore[assignment]
    return SpecWriter(llm, model="fake-model")


def test_feature_spec_has_proposed_criteria_defaulting_to_empty() -> None:
    spec = FeatureSpec(intent_id="i-1", title="T")
    assert spec.proposed_criteria == []
    assert "proposed_criteria" in spec.model_dump()


async def test_stated_criteria_stay_verbatim_and_inferred_go_to_proposed() -> None:
    intent = Intent(
        id="i-1",
        title="Add stats module",
        description="d",
        acceptance_criteria=["mean() returns a float", "median() raises on empty input"],
    )
    writer = _writer(
        {
            "summary": "s",
            "acceptance_criteria": [
                "mean() returns a float",
                "median() raises on empty input",
                "meta.generated_at is an ISO-8601 timestamp",
            ],
        }
    )
    spec = await writer.write(intent)
    assert spec.acceptance_criteria == [
        "mean() returns a float",
        "median() raises on empty input",
    ]
    assert spec.proposed_criteria == ["meta.generated_at is an ISO-8601 timestamp"]


async def test_three_stated_nine_produced_yields_three_and_at_most_six() -> None:
    stated = ["c one", "c two", "c three"]
    produced = stated + [f"inferred {n}" for n in range(1, 7)]
    intent = Intent(id="i-2", title="Ticket", description="d", acceptance_criteria=list(stated))
    spec = await _writer({"summary": "s", "acceptance_criteria": produced}).write(intent)
    assert spec.acceptance_criteria == stated
    assert len(spec.acceptance_criteria) == 3
    assert len(spec.proposed_criteria) <= 6
    assert not set(stated) & set(spec.proposed_criteria)


async def test_unsourced_intent_puts_everything_in_proposed() -> None:
    intent = Intent(id="i-3", title="No criteria", description="d")
    spec = await _writer({"summary": "s", "acceptance_criteria": ["a", "b"]}).write(intent)
    assert spec.acceptance_criteria == []
    assert spec.proposed_criteria == ["a", "b"]


async def test_whitespace_variant_of_a_stated_criterion_is_not_also_proposed() -> None:
    intent = Intent(id="i-4", title="T", description="d", acceptance_criteria=["mean() returns a float"])
    spec = await _writer(
        {"summary": "s", "acceptance_criteria": ["mean()   returns\n a float", "extra one"]}
    ).write(intent)
    assert spec.acceptance_criteria == ["mean() returns a float"]
    assert spec.proposed_criteria == ["extra one"]


async def test_duplicate_inferred_criterion_is_listed_once() -> None:
    intent = Intent(id="i-5", title="T", description="d", acceptance_criteria=["stated"])
    spec = await _writer({"summary": "s", "acceptance_criteria": ["extra", "extra"]}).write(intent)
    assert spec.acceptance_criteria == ["stated"]
    assert spec.proposed_criteria == ["extra"]


async def test_unparseable_output_keeps_stated_criteria_and_proposes_nothing() -> None:
    intent = Intent(id="i-6", title="T", description="d", acceptance_criteria=["stated one"])
    spec = await _writer("not json at all").write(intent)
    assert spec.acceptance_criteria == ["stated one"]
    assert spec.proposed_criteria == []


def test_spec_file_accepts_proposed_criteria(tmp_path: Path) -> None:
    path = tmp_path / "SSPN-31.json"
    path.write_text(
        json.dumps(
            {
                "title": "Partition criteria",
                "acceptance_criteria": ["stated one"],
                "proposed_criteria": ["inferred one"],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_spec_file(path)
    assert loaded["acceptance_criteria"] == ["stated one"]
    assert loaded["proposed_criteria"] == ["inferred one"]
    assert loaded["intent_id"] == "SSPN-31"


def test_spec_file_without_proposed_criteria_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"title": "Legacy", "acceptance_criteria": ["only stated"]}), encoding="utf-8")
    loaded = load_spec_file(path)
    assert loaded["acceptance_criteria"] == ["only stated"]
    assert loaded["proposed_criteria"] == []


def test_spec_file_with_only_proposed_criteria_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsourced.json"
    path.write_text(
        json.dumps({"title": "Unsourced", "acceptance_criteria": [], "proposed_criteria": ["a"]}),
        encoding="utf-8",
    )
    with pytest.raises(SpecFileError, match="acceptance_criteria"):
        load_spec_file(path)
