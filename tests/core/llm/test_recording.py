"""Unit tests for the per-stage token ledger wrapper."""

from __future__ import annotations

from pydantic import BaseModel

from orchestrator.core.llm import RecordingLLMClient, TokenLedger
from orchestrator.core.llm.client import CompletionResult, Message


class _FakeLLM:
    """Returns a canned result; records the model it was asked for."""

    def __init__(self, results: list[CompletionResult]) -> None:
        self._results = list(results)
        self.calls = 0

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
        _ = (messages, response_format, json_object, temperature, max_tokens, tools, tool_choice)
        self.calls += 1
        return self._results.pop(0)


def _result(model: str, p: int, c: int, cost: float = 0.01, lat: float = 100.0) -> CompletionResult:
    return CompletionResult(
        text="ok", model=model, prompt_tokens=p, completion_tokens=c, cost_usd=cost, latency_ms=lat
    )


async def test_attributes_calls_to_active_stage() -> None:
    inner = _FakeLLM([_result("gpt-4o", 10, 5), _result("gpt-4o", 20, 8)])
    rec = RecordingLLMClient(inner)
    msgs = [Message(role="user", content="x")]

    with rec.stage("intent_extraction"):
        await rec.complete(msgs, model="gpt-4o")
    with rec.stage("spec_writing"):
        await rec.complete(msgs, model="gpt-4o")

    stages = {u.stage: u for u in rec.ledger.ordered()}
    assert stages["intent_extraction"].prompt_tokens == 10
    assert stages["spec_writing"].prompt_tokens == 20
    assert [u.stage for u in rec.ledger.ordered()] == ["intent_extraction", "spec_writing"]


async def test_total_sums_all_stages() -> None:
    inner = _FakeLLM([_result("gpt-4o", 10, 5, cost=0.02), _result("gpt-5-codex", 100, 50, cost=0.30)])
    rec = RecordingLLMClient(inner)
    msgs = [Message(role="user", content="x")]
    with rec.stage("spec_writing"):
        await rec.complete(msgs, model="gpt-4o")
    with rec.stage("codegen"):
        await rec.complete(msgs, model="gpt-5-codex")

    total = rec.ledger.total()
    assert total.calls == 2
    assert total.prompt_tokens == 110
    assert total.completion_tokens == 55
    assert total.total_tokens == 165
    assert abs(total.cost_usd - 0.32) < 1e-9
    assert total.models == ["gpt-4o", "gpt-5-codex"]


async def test_repeated_stage_accumulates() -> None:
    inner = _FakeLLM([_result("gpt-4o", 10, 5), _result("gpt-4o", 30, 7)])
    rec = RecordingLLMClient(inner)
    msgs = [Message(role="user", content="x")]
    with rec.stage("spec_writing"):
        await rec.complete(msgs, model="gpt-4o")
        await rec.complete(msgs, model="gpt-4o")

    spec = rec.ledger.stages["spec_writing"]
    assert spec.calls == 2
    assert spec.prompt_tokens == 40
    assert spec.models == ["gpt-4o"]  # de-duped


async def test_unattributed_when_no_stage() -> None:
    inner = _FakeLLM([_result("gpt-4o", 1, 1)])
    rec = RecordingLLMClient(inner)
    await rec.complete([Message(role="user", content="x")], model="gpt-4o")
    assert "unattributed" in rec.ledger.stages


def test_record_deterministic_adds_a_zero_token_stage_with_real_duration() -> None:
    ledger = TokenLedger()
    ledger.record_deterministic("required_behavior", 4.2)

    stage = ledger.stages["required_behavior"]
    assert stage.deterministic_seconds == 4.2
    assert stage.calls == 0
    assert stage.total_tokens == 0
    assert stage.cost_usd == 0.0


def test_record_deterministic_accumulates_across_calls() -> None:
    ledger = TokenLedger()
    ledger.record_deterministic("required_behavior", 1.0)
    ledger.record_deterministic("required_behavior", 2.5)
    assert ledger.stages["required_behavior"].deterministic_seconds == 3.5


async def test_deterministic_time_folds_into_the_grand_total() -> None:
    inner = _FakeLLM([_result("gpt-4o", 10, 5)])
    rec = RecordingLLMClient(inner)
    with rec.stage("refine"):
        await rec.complete([Message(role="user", content="x")], model="gpt-4o")
    rec.ledger.record_deterministic("required_behavior", 3.0)

    total = rec.ledger.total()
    assert total.deterministic_seconds == 3.0
    assert total.total_tokens == 15  # the LLM stage's tokens are untouched


async def test_shared_ledger_across_clients() -> None:
    ledger = TokenLedger()
    a = RecordingLLMClient(_FakeLLM([_result("gpt-4o", 5, 5)]), ledger=ledger)
    b = RecordingLLMClient(_FakeLLM([_result("gpt-4o", 7, 3)]), ledger=ledger)
    with a.stage("extract"):
        await a.complete([Message(role="user", content="x")], model="gpt-4o")
    with b.stage("specs"):
        await b.complete([Message(role="user", content="x")], model="gpt-4o")
    assert ledger.total().total_tokens == 20
