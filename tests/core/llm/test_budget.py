"""Per-run LLM budget enforcement (G9): RunBudget + BudgetedLLMClient."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchestrator.core.llm import BudgetedLLMClient, BudgetExceededError, RunBudget
from orchestrator.core.llm.client import CompletionResult, Message


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
