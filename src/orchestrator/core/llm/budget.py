"""Per-run LLM spend enforcement (G9).

``BudgetedLLMClient`` wraps any ``LLMClient`` and refuses to start a new
completion once the active run's cumulative cost reaches its cap. The cap is
a circuit breaker, not an estimator: the call that *crosses* the cap still
completes (cost is only known after the fact), and every call after that
raises ``BudgetExceededError`` before spending anything.

Runs are scoped with a contextvar (``RunBudget.activate``), so one shared
client can serve many concurrent runs — each asyncio task charges the run it
was activated under, and a worker process serving several SDLC runs keeps
their budgets independent. Calls made outside any active run are charged to
a shared ``unscoped`` key so nothing escapes accounting.

The motivating case is run #6's credit burn: a fan-out of LLM-codegen
children with no ceiling. With this in place the worst case is one cap's
worth of spend per run, and the failure surfaces as a normal stage error the
workflow already knows how to terminate on.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field

from pydantic import BaseModel

from orchestrator.core.llm.client import CompletionResult, LLMClient, LLMError, Message, ToolSpec

_UNSCOPED = "unscoped"

_active_run: ContextVar[str | None] = ContextVar("llm_budget_active_run", default=None)


class BudgetExceededError(LLMError):
    """The active run has spent its LLM budget; no further calls are allowed."""


@dataclass
class RunBudget:
    """Tracks cumulative LLM cost per run key and enforces a per-run cap.

    ``max_cost_usd <= 0`` disables enforcement (spend is still tracked).
    """

    max_cost_usd: float
    spent_usd: dict[str, float] = field(default_factory=dict)

    @contextlib.contextmanager
    def activate(self, run_id: str) -> Iterator[None]:
        """Attribute LLM calls in this (async) context to ``run_id``."""
        token = _active_run.set(run_id)
        try:
            yield
        finally:
            _active_run.reset(token)

    @property
    def active_run(self) -> str:
        return _active_run.get() or _UNSCOPED

    def spent(self, run_id: str | None = None) -> float:
        return self.spent_usd.get(run_id or self.active_run, 0.0)

    def charge(self, cost_usd: float) -> None:
        key = self.active_run
        self.spent_usd[key] = self.spent_usd.get(key, 0.0) + max(cost_usd, 0.0)

    def check(self) -> None:
        """Raise ``BudgetExceededError`` if the active run is at/over its cap."""
        if self.max_cost_usd <= 0:
            return
        key = self.active_run
        spent = self.spent_usd.get(key, 0.0)
        if spent >= self.max_cost_usd:
            raise BudgetExceededError(
                f"LLM budget exhausted for run {key!r}: spent ${spent:.2f} of ${self.max_cost_usd:.2f} cap"
            )


class BudgetedLLMClient:
    """An ``LLMClient`` that enforces a ``RunBudget`` around every call."""

    def __init__(self, inner: LLMClient, budget: RunBudget) -> None:
        self._inner = inner
        self.budget = budget

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: type[BaseModel] | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> CompletionResult:
        self.budget.check()
        result = await self._inner.complete(
            messages,
            model=model,
            response_format=response_format,
            json_object=json_object,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )
        self.budget.charge(result.cost_usd)
        return result


__all__ = ["BudgetExceededError", "BudgetedLLMClient", "RunBudget"]
