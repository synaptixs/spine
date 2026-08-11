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


# --- a model that accepts `temperature` but only at its own value -----------


def _install_temperature_refusing_litellm(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    """Rejects any request carrying `temperature`, the way `claude-opus-5` does."""
    mod = types.ModuleType("litellm")

    async def acompletion(**params: Any) -> dict[str, Any]:
        calls.append(dict(params))
        if "temperature" in params:
            raise ValueError(
                "litellm.UnsupportedParamsError: claude-opus-5 does not support "
                "temperature=0.0. Only temperature=1 is supported."
            )
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    def completion_cost(**_: Any) -> float:
        return 0.0

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = completion_cost  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)


async def test_a_refused_temperature_is_retried_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intake pins temperature=0.0; on such a model every --source run died on call one."""
    calls: list[dict[str, Any]] = []
    _install_temperature_refusing_litellm(monkeypatch, calls)

    result = await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
    )

    assert result.text == "{}"
    assert [("temperature" in c) for c in calls] == [True, False]


async def test_the_lost_determinism_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The caller asked for a stable temperature and did not get one — say so."""
    _install_temperature_refusing_litellm(monkeypatch, [])
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await LiteLLMClient().complete(
            [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
        )
    assert "llm.temperature_unsupported" in caplog.text


async def test_an_unrelated_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a prompt that failed for another reason turns one error into two."""
    from orchestrator.core.llm.client import LLMError

    calls: list[dict[str, Any]] = []
    mod = types.ModuleType("litellm")

    async def acompletion(**params: Any) -> dict[str, Any]:
        calls.append(dict(params))
        raise ValueError("RateLimitError: slow down")

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = lambda **_: 0.0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)

    with pytest.raises(LLMError, match="RateLimitError"):
        await LiteLLMClient().complete(
            [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
        )
    assert len(calls) == 1
