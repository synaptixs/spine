"""LiteLLMClient param wiring — focus on the JSON-object (Ollama-friendly) mode.

``litellm`` is imported lazily inside ``complete``; we install a fake module so
the test asserts what params reach ``litellm.acompletion`` without a network call.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm.client import Message
from orchestrator.core.llm.litellm_client import LiteLLMClient


def _install_fake_litellm(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    mod = types.ModuleType("litellm")

    async def acompletion(**params: Any) -> dict[str, Any]:
        captured.update(params)
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    def completion_cost(**_: Any) -> float:
        return 0.0

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = completion_cost  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)


async def test_json_object_sets_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_litellm(monkeypatch, captured)
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="ollama/qwen2.5-coder", json_object=True
    )
    assert captured["response_format"] == {"type": "json_object"}


async def test_no_json_object_leaves_response_format_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_litellm(monkeypatch, captured)
    await LiteLLMClient().complete([Message(role="user", content="hi")], model="gpt-4o")
    assert "response_format" not in captured


async def test_explicit_schema_wins_over_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_litellm(monkeypatch, captured)

    class Schema(BaseModel):
        x: int

    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="gpt-4o", response_format=Schema, json_object=True
    )
    assert captured["response_format"] is Schema
