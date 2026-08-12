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
    # The event name is a structured field, not part of the sentence a human reads —
    # log parsers key off this, and the message is free to be prose.
    assert any(
        getattr(r, "event", "") == "llm.temperature_unsupported" and getattr(r, "temperature", None) == 0.0
        for r in caplog.records
    )


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


async def test_no_credentials_says_which_model_and_how_to_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key and the model are separate settings; the error must connect them."""
    from orchestrator.core.llm.client import LLMError

    mod = types.ModuleType("litellm")

    async def acompletion(**_: Any) -> dict[str, Any]:
        raise ValueError(
            "litellm.AuthenticationError: Missing Anthropic API Key - A call is being made to "
            "anthropic but no key is set"
        )

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = lambda **_: 0.0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)

    with pytest.raises(LLMError) as caught:
        await LiteLLMClient().complete(
            [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
        )

    message = str(caught.value)
    assert "claude-opus-5" in message
    assert "ORCHESTRATOR_MODEL" in message
    # Not the provider's own wording — that is what named Anthropic at someone who had
    # configured OpenAI.
    assert "Missing Anthropic API Key" not in message


async def test_litellms_console_banner_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """It prints on every mapped exception, including ones we catch and recover from."""
    captured: dict[str, Any] = {}
    _install_fake_litellm(monkeypatch, captured)
    mod = sys.modules["litellm"]
    mod.suppress_debug_info = False  # type: ignore[attr-defined]

    await LiteLLMClient().complete([Message(role="user", content="hi")], model="gpt-5.4")

    assert mod.suppress_debug_info is True


async def test_the_temperature_warning_reads_as_a_sentence(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_temperature_refusing_litellm(monkeypatch, [])
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await LiteLLMClient().complete(
            [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
        )
    assert "not deterministic" in caplog.text
    assert "claude-opus-5 rejected temperature=0.0" in caplog.text
