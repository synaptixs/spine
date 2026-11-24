"""B172 (SSPN-398): codegen asks each model for no more output than that model allows.

Both codegen call sites asked every model for 32,000 output tokens. `gpt-4o` allows 16,384 and rejects the
whole request, so an OpenAI-only setup could not build. The cap now comes from
`catalog.codegen_output_cap`, whose rule has its own tests in `tests/core/llm/test_catalog.py`; these pin
that both call sites use it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.codegen import LLMCodegenAdapter


class _RecordingLLM:
    def __init__(self) -> None:
        self.max_tokens: list[int | None] = []

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.max_tokens.append(kwargs.get("max_tokens"))
        return CompletionResult(
            text=json.dumps({"files": [], "summary": "ok"}),
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


@pytest.mark.parametrize(
    ("model", "expected"),
    [("gpt-4o", 16_384), ("openai/gpt-4o", 16_384), ("claude-opus-5", 32_000), ("no-such-model-9", 32_000)],
)
async def test_the_single_shot_call_asks_for_the_models_own_cap(model: str, expected: int) -> None:
    llm = _RecordingLLM()
    await LLMCodegenAdapter(llm, model=model)._complete_reply("system", "user")
    assert llm.max_tokens == [expected]


def test_the_agentic_loop_is_given_the_models_own_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.agentic as agentic

    seen: dict[str, Any] = {}

    class _Loop:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(agentic, "AgentLoop", _Loop)
    LLMCodegenAdapter(_RecordingLLM(), model="gpt-4o")._build_loop([])
    assert seen["max_tokens"] == 16_384


async def test_the_override_reaches_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_CODEGEN_MAX_TOKENS", "9000")
    llm = _RecordingLLM()
    await LLMCodegenAdapter(llm, model="gpt-4o")._complete_reply("system", "user")
    assert llm.max_tokens == [9000]
