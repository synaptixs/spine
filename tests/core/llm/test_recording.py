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
    ) -> CompletionResult:
        _ = (messages, response_format, json_object, temperature, max_tokens, tools)
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


async def test_shared_ledger_across_clients() -> None:
    ledger = TokenLedger()
    a = RecordingLLMClient(_FakeLLM([_result("gpt-4o", 5, 5)]), ledger=ledger)
    b = RecordingLLMClient(_FakeLLM([_result("gpt-4o", 7, 3)]), ledger=ledger)
    with a.stage("extract"):
        await a.complete([Message(role="user", content="x")], model="gpt-4o")
    with b.stage("specs"):
        await b.complete([Message(role="user", content="x")], model="gpt-4o")
    assert ledger.total().total_tokens == 20
