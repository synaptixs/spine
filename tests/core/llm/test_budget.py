"""Per-run LLM budget enforcement (G9): RunBudget + BudgetedLLMClient."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchestrator.core.llm import BudgetedLLMClient, BudgetExceededError, RunBudget, run_budget_from_env
from orchestrator.core.llm.client import CompletionResult, Message, ToolCall
from orchestrator.core.llm.pricing import UnpricedRate


class _FakeLLM:
    """Returns a fixed-cost completion and counts calls."""

    def __init__(self, cost_usd: float = 1.0) -> None:
        self.cost_usd = cost_usd
        self.calls = 0

    async def complete(self, messages: list[Message], *, model: str, **_: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text="ok",
            model=model,
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=self.cost_usd,
            latency_ms=1.0,
        )


def _msg() -> list[Message]:
    return [Message(role="user", content="hi")]


async def test_budget_allows_until_cap_then_refuses() -> None:
    budget = RunBudget(max_cost_usd=2.5)
    client = BudgetedLLMClient(_FakeLLM(cost_usd=1.0), budget)

    with budget.activate("run-1"):
        await client.complete(_msg(), model="m")
        await client.complete(_msg(), model="m")
        # spent 2.0 < 2.5 — the crossing call still completes...
        await client.complete(_msg(), model="m")
        # ...but now spent 3.0 >= 2.5: refused before any spend.
        with pytest.raises(BudgetExceededError, match="run-1"):
            await client.complete(_msg(), model="m")

    assert budget.spent("run-1") == pytest.approx(3.0)


async def test_runs_are_independently_capped() -> None:
    budget = RunBudget(max_cost_usd=1.5)
    client = BudgetedLLMClient(_FakeLLM(cost_usd=1.0), budget)

    with budget.activate("run-a"):
        await client.complete(_msg(), model="m")
        await client.complete(_msg(), model="m")
        with pytest.raises(BudgetExceededError):
            await client.complete(_msg(), model="m")

    # A different run starts from zero even on the same client.
    with budget.activate("run-b"):
        await client.complete(_msg(), model="m")
    assert budget.spent("run-b") == pytest.approx(1.0)


async def test_concurrent_runs_attribute_to_their_own_key() -> None:
    """contextvar scoping: parallel tasks charge the run they activated."""
    budget = RunBudget(max_cost_usd=0)  # tracking only
    client = BudgetedLLMClient(_FakeLLM(cost_usd=1.0), budget)

    async def run(key: str, n: int) -> None:
        with budget.activate(key):
            for _ in range(n):
                await client.complete(_msg(), model="m")

    await asyncio.gather(run("r1", 2), run("r2", 3))
    assert budget.spent("r1") == pytest.approx(2.0)
    assert budget.spent("r2") == pytest.approx(3.0)


async def test_zero_cap_disables_enforcement_but_tracks() -> None:
    budget = RunBudget(max_cost_usd=0)
    client = BudgetedLLMClient(_FakeLLM(cost_usd=50.0), budget)
    with budget.activate("r"):
        await client.complete(_msg(), model="m")
        await client.complete(_msg(), model="m")
    assert budget.spent("r") == pytest.approx(100.0)


async def test_unscoped_calls_are_charged_and_capped() -> None:
    budget = RunBudget(max_cost_usd=1.0)
    client = BudgetedLLMClient(_FakeLLM(cost_usd=1.0), budget)
    await client.complete(_msg(), model="m")  # no activate() — "unscoped"
    with pytest.raises(BudgetExceededError, match="unscoped"):
        await client.complete(_msg(), model="m")


# --- an unpriced model must never be silently uncapped (SSPN-180 / B87) -------------------------------


class _UnpricedLLM:
    """A model whose price is not known: $0 recorded, ``cost_known`` false, usage as given."""

    def __init__(
        self,
        *,
        prompt: int = 1000,
        completion: int = 500,
        reported: bool = True,
        text: str = "ok",
        model: str = "gpt-6-astra",
        known: bool = False,
        cost: float = 0.0,
    ) -> None:
        self.prompt, self.completion, self.reported = prompt, completion, reported
        self.text, self.model, self.known, self.cost = text, model, known, cost
        self.calls = 0

    async def complete(self, messages: list[Message], *, model: str, **_: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text=self.text,
            model=self.model,
            prompt_tokens=self.prompt if self.reported else 0,
            completion_tokens=self.completion if self.reported else 0,
            cost_usd=self.cost,
            latency_ms=1.0,
            usage_reported=self.reported,
            cost_known=self.known,
        )


async def test_an_unpriced_model_is_charged_an_estimate_so_the_cap_trips() -> None:
    """1,000 prompt + 500 completion tokens at $5/$25 per million is $0.0175 a call."""
    budget = RunBudget(max_cost_usd=0.03)
    client = BudgetedLLMClient(_UnpricedLLM(), budget, unpriced_rate=UnpricedRate(5.0, 25.0))

    with budget.activate("run-1"):
        await client.complete(_msg(), model="gpt-6-astra")
        await client.complete(_msg(), model="gpt-6-astra")  # 0.035 >= 0.03: the crossing call completes
        with pytest.raises(BudgetExceededError, match="estimated"):
            await client.complete(_msg(), model="gpt-6-astra")

    assert budget.spent("run-1") == pytest.approx(0.035)
    assert budget.estimated("run-1") == pytest.approx(0.035)


async def test_the_error_says_how_much_was_estimated_and_for_which_model() -> None:
    budget = RunBudget(max_cost_usd=0.01)
    client = BudgetedLLMClient(
        _UnpricedLLM(model="gpt-6-astra"), budget, unpriced_rate=UnpricedRate(5.0, 25.0)
    )
    with budget.activate("run-1"):
        await client.complete(_msg(), model="gpt-6-astra")
        with pytest.raises(BudgetExceededError) as info:
            await client.complete(_msg(), model="gpt-6-astra")
    text = str(info.value)
    assert "gpt-6-astra" in text and "$0.02" in text and "SDLC_MODEL_PRICES" in text


async def test_a_priced_model_is_charged_what_it_recorded_and_nothing_is_estimated() -> None:
    budget = RunBudget(max_cost_usd=100.0)
    client = BudgetedLLMClient(_FakeLLM(cost_usd=0.25), budget)
    with budget.activate("run-1"):
        await client.complete(_msg(), model="m")
    assert budget.spent("run-1") == pytest.approx(0.25)
    assert budget.estimated("run-1") == 0.0


async def test_a_genuinely_free_model_costs_nothing_and_is_not_estimated() -> None:
    """A table entry at $0 (a local model) is a known price of zero, not an unknown one."""
    budget = RunBudget(max_cost_usd=0.01)
    client = BudgetedLLMClient(_UnpricedLLM(known=True, cost=0.0, model="ollama/llama3"), budget)
    with budget.activate("run-1"):
        for _ in range(5):
            await client.complete(_msg(), model="ollama/llama3")
    assert budget.spent("run-1") == 0.0 and budget.estimated("run-1") == 0.0


async def test_with_no_usage_tokens_are_estimated_from_characters() -> None:
    """Unpriced *and* usage not reported: about four characters a token, both ways."""
    inner = _UnpricedLLM(reported=False, text="r" * 200)
    budget = RunBudget(max_cost_usd=100.0)
    client = BudgetedLLMClient(inner, budget, unpriced_rate=UnpricedRate(5.0, 25.0))
    with budget.activate("run-1"):
        await client.complete([Message(role="user", content="p" * 400)], model="gpt-6-astra")
    # 400 chars -> 100 prompt tokens, 200 chars -> 50 completion tokens: (100*5 + 50*25) / 1e6
    assert budget.spent("run-1") == pytest.approx(0.00175)


async def test_a_tool_call_reply_counts_toward_the_character_estimate() -> None:
    """A forced-tool reply has no text; its arguments are what the model produced."""

    class _ToolReply(_UnpricedLLM):
        async def complete(self, messages: list[Message], *, model: str, **_: Any) -> CompletionResult:
            base = await super().complete(messages, model=model)
            call = ToolCall(id="1", name="submit_files", arguments={"files": "z" * 400})
            return CompletionResult(**{**base.__dict__, "tool_calls": (call,)})

    budget = RunBudget(max_cost_usd=100.0)
    client = BudgetedLLMClient(
        _ToolReply(reported=False, text=""), budget, unpriced_rate=UnpricedRate(0.0, 10.0)
    )
    with budget.activate("run-1"):
        await client.complete([Message(role="user", content="")], model="m")
    assert budget.spent("run-1") > 0.0


async def test_the_rate_is_read_from_the_environment_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_UNPRICED_USD_PER_MTOK", "50,250")
    budget = RunBudget(max_cost_usd=100.0)
    client = BudgetedLLMClient(_UnpricedLLM(), budget)
    with budget.activate("run-1"):
        await client.complete(_msg(), model="gpt-6-astra")
    assert budget.spent("run-1") == pytest.approx(0.175)


def test_a_malformed_rate_fails_when_the_client_is_built_not_mid_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_UNPRICED_USD_PER_MTOK", "five")
    with pytest.raises(ValueError, match="SDLC_UNPRICED_USD_PER_MTOK"):
        BudgetedLLMClient(_UnpricedLLM(), RunBudget(max_cost_usd=1.0))


async def test_a_disabled_cap_tracks_the_estimate_but_never_refuses() -> None:
    budget = RunBudget(max_cost_usd=0.0)
    client = BudgetedLLMClient(_UnpricedLLM(), budget, unpriced_rate=UnpricedRate(5.0, 25.0))
    with budget.activate("run-1"):
        for _ in range(4):
            await client.complete(_msg(), model="gpt-6-astra")
    assert budget.spent("run-1") == pytest.approx(0.07)
    assert budget.estimated("run-1") == pytest.approx(0.07)


async def test_priced_and_unpriced_spend_add_and_only_the_unpriced_part_is_estimated() -> None:
    budget = RunBudget(max_cost_usd=100.0)
    priced = BudgetedLLMClient(_FakeLLM(cost_usd=1.0), budget)
    unpriced = BudgetedLLMClient(_UnpricedLLM(), budget, unpriced_rate=UnpricedRate(5.0, 25.0))
    with budget.activate("run-1"):
        await priced.complete(_msg(), model="m")
        await unpriced.complete(_msg(), model="gpt-6-astra")
    assert budget.spent("run-1") == pytest.approx(1.0175)
    assert budget.estimated("run-1") == pytest.approx(0.0175)


async def test_the_documented_cap_stops_a_run_on_an_unpriced_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ticket's own test, end to end through the env readers: never silently uncapped."""
    monkeypatch.setenv("SDLC_RUN_BUDGET_USD", "1")
    monkeypatch.delenv("SDLC_UNPRICED_USD_PER_MTOK", raising=False)
    budget = run_budget_from_env()
    inner = _UnpricedLLM(prompt=20_000, completion=4_000)  # $0.20 a call at the default rate
    client = BudgetedLLMClient(inner, budget)
    with budget.activate("run-1"), pytest.raises(BudgetExceededError):
        for _ in range(50):
            await client.complete(_msg(), model="gpt-6-astra")
    assert 5 <= inner.calls <= 7, "the cap must trip near $1, not after fifty free calls"


def test_the_cap_reader_also_validates_the_unpriced_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every entry point pre-validates `run_budget_from_env` and turns its ValueError into exit 2, so the two
    settings the cap now depends on get the same treatment instead of a traceback from deep in a run."""
    monkeypatch.setenv("SDLC_UNPRICED_USD_PER_MTOK", "five")
    with pytest.raises(ValueError, match="SDLC_UNPRICED_USD_PER_MTOK"):
        run_budget_from_env()
    monkeypatch.delenv("SDLC_UNPRICED_USD_PER_MTOK")
    monkeypatch.setenv("SDLC_MODEL_PRICES", "{not json")
    with pytest.raises(ValueError, match="SDLC_MODEL_PRICES"):
        run_budget_from_env()


async def test_non_ascii_and_unserialisable_tool_arguments_do_not_break_or_inflate_the_estimate() -> None:
    """`json.dumps` escapes non-ASCII to six characters each, and an odd argument must not lose the reply."""

    class _Odd(_UnpricedLLM):
        async def complete(self, messages: list[Message], *, model: str, **_: Any) -> CompletionResult:
            base = await super().complete(messages, model=model)
            call = ToolCall(id="1", name="t", arguments={"text": "é" * 40, "obj": object()})
            return CompletionResult(**{**base.__dict__, "tool_calls": (call,)})

    budget = RunBudget(max_cost_usd=100.0)
    client = BudgetedLLMClient(_Odd(reported=False, text=""), budget, unpriced_rate=UnpricedRate(0.0, 1e6))
    with budget.activate("run-1"):
        await client.complete([Message(role="user", content="")], model="m")
    # 40 accented characters plus the key and the object's text: tens of tokens, not an escaped dump's count.
    assert 0.0 < budget.spent("run-1") < 40.0
