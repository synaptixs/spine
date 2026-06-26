"""Text-summarization handler that wraps an LLMClient."""

from __future__ import annotations

from typing import Any

from orchestrator.core.llm import LiteLLMClient, LLMClient, Message
from orchestrator.gateway.invocation import InvocationContext

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TARGET_WORDS = 150


class SummarizeTextHandler:
    contract_id: str = "tool.summarize_text"
    contract_version: str = "0.1.0"

    def __init__(self, client: LLMClient | None = None, *, default_model: str = DEFAULT_MODEL) -> None:
        self._client = client or LiteLLMClient()
        self._default_model = default_model

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx
        text = str(inputs["text"]).strip()
        if not text:
            raise ValueError("summarize_text: 'text' must be non-empty")
        target_words = int(inputs.get("target_words", DEFAULT_TARGET_WORDS))
        model = str(inputs.get("model") or self._default_model)

        messages = [
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

        # Don't force temperature — newer reasoning models reject the param.
        result = await self._client.complete(messages, model=model)
        return {
            "summary": result.text.strip(),
            "model": result.model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "__cost_usd__": result.cost_usd,
        }


SUMMARIZE_TEXT_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.summarize_text",
        "version": "0.1.0",
        "description": "Summarize free-text input via an LLM call.",
        "tags": ["llm", "summarize"],
    },
    "spec": {
        "purpose": "Return a faithful summary of input text at a target length.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 60, "burst": 10},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
