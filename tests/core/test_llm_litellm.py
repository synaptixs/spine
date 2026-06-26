"""Unit tests for LiteLLMClient with the underlying ``litellm`` symbol mocked."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.core.llm import LiteLLMClient, LLMError, Message


def _fake_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


class _LitellmStub:
    def __init__(self, response: Any, cost: float = 0.0001, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.cost = cost
        self.raise_exc = raise_exc
        self.captured: dict[str, Any] = {}

    async def acompletion(self, **kwargs: Any) -> Any:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.captured = kwargs
        return self.response

    def completion_cost(self, *, completion_response: Any) -> float:
        return self.cost


@pytest.fixture()
def stub_litellm(monkeypatch: pytest.MonkeyPatch) -> _LitellmStub:
    stub = _LitellmStub(_fake_response("hello"))
    monkeypatch.setitem(sys.modules, "litellm", stub)
    return stub


async def test_complete_returns_text_and_cost(stub_litellm: _LitellmStub) -> None:
    client = LiteLLMClient()
    result = await client.complete([Message(role="user", content="hi")], model="claude-opus-4-7")
    assert result.text == "hello"
    assert result.model == "claude-opus-4-7"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.cost_usd == pytest.approx(0.0001)


async def test_complete_forwards_temperature_and_retries(stub_litellm: _LitellmStub) -> None:
    client = LiteLLMClient(num_retries=3, request_timeout_seconds=42.0)
    await client.complete([Message(role="user", content="x")], model="gpt-4o", temperature=0.7)
    assert stub_litellm.captured["temperature"] == 0.7
    assert stub_litellm.captured["num_retries"] == 3
    # litellm honors `timeout`; `request_timeout` kept as the back-compat alias.
    assert stub_litellm.captured["timeout"] == 42.0
    assert stub_litellm.captured["request_timeout"] == 42.0
    assert stub_litellm.captured["model"] == "gpt-4o"


async def test_default_timeout_is_generous_for_codegen(
    stub_litellm: _LitellmStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default applied timeout is 300s (heavy codegen ~180s > litellm's 60s default),
    passed as the honored `timeout` param. Overridable via env."""
    monkeypatch.delenv("ORCHESTRATOR_LLM_TIMEOUT_SECONDS", raising=False)
    await LiteLLMClient().complete([Message(role="user", content="x")], model="m")
    assert stub_litellm.captured["timeout"] == 300.0

    monkeypatch.setenv("ORCHESTRATOR_LLM_TIMEOUT_SECONDS", "180")
    await LiteLLMClient().complete([Message(role="user", content="x")], model="m")
    assert stub_litellm.captured["timeout"] == 180.0


async def test_complete_omits_temperature_by_default(stub_litellm: _LitellmStub) -> None:
    """Newer reasoning models (e.g. claude-opus-4-7) reject the temperature arg
    outright. The client must not include it unless the caller asked for it."""
    client = LiteLLMClient()
    await client.complete([Message(role="user", content="x")], model="claude-opus-4-7")
    assert "temperature" not in stub_litellm.captured


async def test_underlying_exception_maps_to_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _LitellmStub(_fake_response(), raise_exc=RuntimeError("provider blew up"))
    monkeypatch.setitem(sys.modules, "litellm", stub)
    client = LiteLLMClient()
    with pytest.raises(LLMError, match="provider blew up"):
        await client.complete([Message(role="user", content="x")], model="m")


async def test_cost_lookup_failure_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadCost(_LitellmStub):
        def completion_cost(self, *, completion_response: Any) -> float:
            raise ValueError("model unknown")

    stub = BadCost(_fake_response("hi"))
    monkeypatch.setitem(sys.modules, "litellm", stub)
    client = LiteLLMClient()
    result = await client.complete([Message(role="user", content="x")], model="m")
    assert result.cost_usd == 0.0
    assert result.text == "hi"
