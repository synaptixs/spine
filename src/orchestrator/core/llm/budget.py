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
import json
import math
import os
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from orchestrator.core.llm.client import CompletionResult, LLMClient, LLMError, Message, ToolSpec
from orchestrator.core.llm.pricing import (
    PRICES_ENV,
    UnpricedRate,
    price_overrides_from_env,
    unpriced_rate_from_env,
)

_UNSCOPED = "unscoped"

_active_run: ContextVar[str | None] = ContextVar("llm_budget_active_run", default=None)


class BudgetExceededError(LLMError):
    """The active run has spent its LLM budget; no further calls are allowed."""


@dataclass
class RunBudget:
    """Tracks cumulative LLM cost per run key and enforces a per-run cap.

    ``max_cost_usd <= 0`` disables enforcement (spend is still tracked).

    A call on a model with no known price records $0, so counting what was *recorded* left such a run
    uncapped (SSPN-180 / B87). Those calls are charged an **estimate** instead (:func:`estimate_cost_usd`),
    kept apart in ``estimated_usd`` so every surface can say how much of a figure is a guess and for which
    models. Enforcement is the only thing a disabled cap switches off: the estimate is still tracked.
    """

    max_cost_usd: float
    spent_usd: dict[str, float] = field(default_factory=dict)
    estimated_usd: dict[str, float] = field(default_factory=dict)
    estimated_models: dict[str, list[str]] = field(default_factory=dict)

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

    def estimated(self, run_id: str | None = None) -> float:
        """The part of :meth:`spent` that was estimated for calls with no known price."""
        return self.estimated_usd.get(run_id or self.active_run, 0.0)

    def charge(self, cost_usd: float, *, estimated: bool = False, model: str = "") -> None:
        key = self.active_run
        amount = max(cost_usd, 0.0)
        self.spent_usd[key] = self.spent_usd.get(key, 0.0) + amount
        if estimated:
            self.estimated_usd[key] = self.estimated_usd.get(key, 0.0) + amount
            models = self.estimated_models.setdefault(key, [])
            if model and model not in models:
                models.append(model)

    def check(self) -> None:
        """Raise ``BudgetExceededError`` if the active run is at/over its cap."""
        if self.max_cost_usd <= 0:
            return
        key = self.active_run
        spent = self.spent_usd.get(key, 0.0)
        if spent >= self.max_cost_usd:
            message = (
                f"LLM budget exhausted for run {key!r}: spent ${spent:.2f} of ${self.max_cost_usd:.2f} cap"
            )
            guessed = self.estimated_usd.get(key, 0.0)
            if guessed > 0:
                models = ", ".join(self.estimated_models.get(key, [])) or "unknown"
                message += (
                    f" (${guessed:.2f} of it estimated for model(s) with no known price: {models}; "
                    f"set {PRICES_ENV} to price them)"
                )
            raise BudgetExceededError(message)


def _tokens_from_characters(chars: int) -> int:
    """About four characters a token — rough on purpose, and used only when the provider reported no usage."""
    return -(-chars // 4)


def _argument_chars(arguments: dict[str, Any]) -> int:
    """Characters in a tool call's arguments: raw (not escaped); an odd value must not lose the reply."""
    return len(json.dumps(arguments, ensure_ascii=False, default=str))


def estimate_cost_usd(result: CompletionResult, messages: list[Message], rate: UnpricedRate) -> float:
    """What a call with no known price is charged to the cap: its tokens at the unpriced ``rate``.

    The reported token counts when there are any. Otherwise (usage missing too, B81) characters of what
    was sent and received, so "never silently uncapped" has no hole: a forced-tool reply has no text, its
    arguments are what the model produced.
    """
    if result.usage_reported:
        prompt, completion = result.prompt_tokens, result.completion_tokens
    else:
        sent = sum(
            len(m.content) + sum(_argument_chars(tc.arguments) for tc in m.tool_calls) for m in messages
        )
        received = len(result.text) + sum(_argument_chars(tc.arguments) for tc in result.tool_calls)
        prompt, completion = _tokens_from_characters(sent), _tokens_from_characters(received)
    return (prompt * rate.input_per_mtok + completion * rate.output_per_mtok) / 1e6


def run_budget_from_env(default_usd: float = 25.0) -> RunBudget:
    """The documented per-run cap: ``SDLC_RUN_BUDGET_USD`` dollars, $25 when unset, ``0`` to
    disable enforcement (spend is still tracked). Also checks ``SDLC_UNPRICED_USD_PER_MTOK`` and
    ``SDLC_MODEL_PRICES``, so all three fail together, at startup. The one reader behind the Temporal worker,
    ``sdlc feature``, ``sdlc autorun``, the MCP ``sdlc_feature`` tool (B14) and, one budget per
    task, ``sdlc remediate`` and the MCP remediate tool (B33)."""
    raw = os.getenv("SDLC_RUN_BUDGET_USD", "").strip()
    try:
        cap = float(raw) if raw else default_usd
    except ValueError as exc:
        raise ValueError(f"SDLC_RUN_BUDGET_USD must be a number of dollars, got {raw!r}") from exc
    if not math.isfinite(cap) or cap < 0:
        # `nan` would compare false against every spend and silently mean "no cap".
        raise ValueError(f"SDLC_RUN_BUDGET_USD must be 0 or a positive number of dollars, got {raw!r}")
    # The two settings the cap now depends on (SSPN-180): every entry point pre-validates this reader and
    # turns its ValueError into exit 2, so a typo there fails the same way instead of as a traceback mid-run.
    unpriced_rate_from_env()
    price_overrides_from_env()
    return RunBudget(max_cost_usd=cap)


class BudgetedLLMClient:
    """An ``LLMClient`` that enforces a ``RunBudget`` around every call."""

    def __init__(
        self, inner: LLMClient, budget: RunBudget, *, unpriced_rate: UnpricedRate | None = None
    ) -> None:
        self._inner = inner
        self.budget = budget
        # Read now, not per call: a malformed SDLC_UNPRICED_USD_PER_MTOK fails when the run starts.
        self._unpriced_rate = unpriced_rate if unpriced_rate is not None else unpriced_rate_from_env()

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
        tool_choice: str | None = None,
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
            tool_choice=tool_choice,
        )
        if result.cost_known:
            self.budget.charge(result.cost_usd)
        else:
            self.budget.charge(
                estimate_cost_usd(result, messages, self._unpriced_rate),
                estimated=True,
                model=result.model or model,
            )
        return result


__all__ = [
    "BudgetExceededError",
    "BudgetedLLMClient",
    "RunBudget",
    "estimate_cost_usd",
    "run_budget_from_env",
]
