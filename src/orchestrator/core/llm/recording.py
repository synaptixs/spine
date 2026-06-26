"""Per-stage token accounting wrapper.

``RecordingLLMClient`` wraps any ``LLMClient`` and tallies token/cost/latency
usage into a ``TokenLedger``, attributing each ``complete()`` call to the
currently-active *stage* (e.g. ``intent_extraction``, ``spec_writing``,
``codegen``). It implements the ``LLMClient`` protocol, so it drops in
anywhere the real client is used — the pipeline code is unchanged; callers
just wrap the client and open a ``stage(...)`` around each leg.

This backs the "audit trail of how many tokens each leg consumed" requirement:
one ``StageUsage`` row per stage, summable into a grand total, rendered into
the traceability report alongside the specs it paid for.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field

from pydantic import BaseModel

from orchestrator.core.llm.client import CompletionResult, LLMClient, Message, ToolSpec
from orchestrator.obs import tracing

_UNATTRIBUTED = "unattributed"


@dataclass
class StageUsage:
    """Accumulated LLM usage for one named pipeline stage."""

    stage: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    # Models seen in this stage, in first-seen order (a stage may fan out
    # across fallbacks/candidates, e.g. codegen trying gpt-5-codex then gpt-5).
    models: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, result: CompletionResult) -> None:
        self.calls += 1
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.cost_usd += result.cost_usd
        self.latency_ms += result.latency_ms
        if result.model not in self.models:
            self.models.append(result.model)


@dataclass
class TokenLedger:
    """Ordered collection of per-stage usage, with a grand total."""

    stages: dict[str, StageUsage] = field(default_factory=dict)

    def record(self, stage: str, result: CompletionResult) -> None:
        self.stages.setdefault(stage, StageUsage(stage=stage)).add(result)

    def ordered(self) -> list[StageUsage]:
        """Stages in insertion order (the order legs first ran)."""
        return list(self.stages.values())

    def total(self) -> StageUsage:
        grand = StageUsage(stage="TOTAL")
        for usage in self.stages.values():
            grand.calls += usage.calls
            grand.prompt_tokens += usage.prompt_tokens
            grand.completion_tokens += usage.completion_tokens
            grand.cost_usd += usage.cost_usd
            grand.latency_ms += usage.latency_ms
            for m in usage.models:
                if m not in grand.models:
                    grand.models.append(m)
        return grand


class RecordingLLMClient:
    """Wraps an ``LLMClient`` and records usage per active stage.

    Usage::

        rec = RecordingLLMClient(LiteLLMClient())
        with rec.stage("spec_writing"):
            await writer.write_all(intents)   # writer was given ``rec``
        print(rec.ledger.total().total_tokens)

    Calls made outside any ``stage(...)`` are attributed to ``unattributed``
    so nothing is silently dropped. ``stage`` blocks may nest; the innermost
    active stage wins.
    """

    def __init__(self, inner: LLMClient, *, ledger: TokenLedger | None = None) -> None:
        self._inner = inner
        self.ledger = ledger or TokenLedger()
        self._stack: list[str] = []

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        self._stack.append(name)
        try:
            yield
        finally:
            self._stack.pop()

    @property
    def current_stage(self) -> str:
        return self._stack[-1] if self._stack else _UNATTRIBUTED

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
        # One span per LLM call — the chokepoint every stage funnels through, so
        # this single insertion traces intake / codegen / review / consolidation
        # alike (Phase 1, docs/specs/live-observability-otel.md). No-op unless an
        # OTEL endpoint is configured.
        stage = self.current_stage
        with tracing.span("llm.complete", **{"llm.stage": stage, "llm.model": model}) as sp:
            result = await self._inner.complete(
                messages,
                model=model,
                response_format=response_format,
                json_object=json_object,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
            sp.set_attribute("llm.response_model", result.model)
            sp.set_attribute("llm.prompt_tokens", result.prompt_tokens)
            sp.set_attribute("llm.completion_tokens", result.completion_tokens)
            sp.set_attribute("llm.total_tokens", result.prompt_tokens + result.completion_tokens)
            sp.set_attribute("llm.cost_usd", result.cost_usd)
            sp.set_attribute("llm.latency_ms", result.latency_ms)
            sp.set_attribute("llm.tool_calls", len(result.tool_calls))
        self.ledger.record(stage, result)
        return result


__all__ = ["RecordingLLMClient", "StageUsage", "TokenLedger"]
