from __future__ import annotations

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import SummarizeTextHandler


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.summarize_text",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
    )


def _expected_messages(text: str, target_words: int) -> list[Message]:
    return [
        Message(
            role="system",
            content=(
                "You are a concise summarizer. Produce a faithful summary at the "
                "requested length. Do not invent facts, quote sources, or add commentary."
            ),
        ),
        Message(
            role="user",
            content=(
                f"Summarize the following in roughly {target_words} words. "
                "Output the summary only — no preamble.\n\n"
                f"{text}"
            ),
        ),
    ]


async def test_summarize_returns_summary_and_cost() -> None:
    mock = MockLLMClient()
    mock.register(
        messages=_expected_messages("Once upon a time, the orchestrator ran.", 50),
        model="claude-haiku-4-5-20251001",
        result=CompletionResult(
            text="  The orchestrator ran.  ",
            model="claude-haiku-4-5-20251001",
            prompt_tokens=20,
            completion_tokens=4,
            cost_usd=0.00005,
            latency_ms=0.0,
        ),
    )
    out = await SummarizeTextHandler(client=mock).__call__(
        {"text": "Once upon a time, the orchestrator ran.", "target_words": 50},
        _ctx(),
    )
    assert out["summary"] == "The orchestrator ran."
    assert out["__cost_usd__"] == pytest.approx(0.00005)


async def test_empty_text_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        await SummarizeTextHandler(client=MockLLMClient()).__call__({"text": "   "}, _ctx())


async def test_model_override_honoured() -> None:
    mock = MockLLMClient()
    msgs = _expected_messages("hello", 150)
    mock.register(
        messages=msgs,
        model="gpt-4o",
        result=CompletionResult(
            text="hi", model="gpt-4o", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        ),
    )
    out = await SummarizeTextHandler(client=mock).__call__({"text": "hello", "model": "gpt-4o"}, _ctx())
    assert out["model"] == "gpt-4o"
