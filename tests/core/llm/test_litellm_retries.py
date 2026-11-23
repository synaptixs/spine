"""B173 (SSPN-399): a request the provider rejects as configured is sent once, and says what to change.

litellm 1.97's async path retries *every* exception when ``num_retries`` is set, with no filter: a
``max_tokens`` above the model's limit was sent three times, and the run record said only
``BadRequestError: …``. Spine now owns its retries: litellm is told ``num_retries=0``, and the client resends
only what is worth resending (rate limits, connection failures, 5xx). A rejection of the request's own
settings becomes an ``LLMConfigError`` naming the model, the limit the provider stated and the setting to
change. Every other behaviour — the temperature retry, the native-schema fallback, the credentials message —
keeps its own tests, which this file does not repeat.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from orchestrator.core.llm.client import LLMConfigError, LLMError, Message, ToolSpec
from orchestrator.core.llm.litellm_client import LiteLLMClient

_module = importlib.import_module("orchestrator.core.llm.litellm_client")


# Named as litellm names them: the client matches classes by name, anywhere in the ancestry.
class BadRequestError(Exception):
    pass


class ContextWindowExceededError(BadRequestError):
    pass


class APIConnectionError(Exception):
    pass


class RateLimitError(Exception):
    pass


class Timeout(APIConnectionError):  # noqa: N818 — litellm's own name; it inherits APIConnectionError
    pass


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


_OPENAI_TOO_MANY = (
    "OpenAIException - max_tokens is too large: 32000. This model supports at most 16384 completion "
    "tokens, whereas you provided 32000."
)
_ANTHROPIC_TOO_MANY = (
    "AnthropicException - max_tokens: 200000 > 128000, which is the maximum allowed number of output "
    "tokens for claude-opus-5"
)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    failures: list[Exception] | Callable[[dict[str, Any]], Exception | None],
) -> None:
    """A litellm whose ``acompletion`` raises the queued failures in order, then answers."""
    mod = types.ModuleType("litellm")
    queue = failures if isinstance(failures, list) else None

    async def acompletion(**params: Any) -> dict[str, Any]:
        calls.append(dict(params))
        exc = (queue.pop(0) if queue else None) if queue is not None else failures(params)  # type: ignore[operator]
        if exc is not None:
            raise exc
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = lambda **_: 0.0  # type: ignore[attr-defined]
    mod.model_cost = {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)


@pytest.fixture(autouse=True)
def _no_backoff_and_no_memory(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_module, "_RETRY_BACKOFF_SECONDS", 0.0, raising=False)
    monkeypatch.delenv("ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT", raising=False)
    for refused in (_module._NATIVE_SCHEMA_REFUSED, _module._TEMPERATURE_REFUSED):
        refused.clear()
    yield
    for refused in (_module._NATIVE_SCHEMA_REFUSED, _module._TEMPERATURE_REFUSED):
        refused.clear()


async def _complete(model: str = "gpt-4o", *, max_tokens: int | None = 32_000, **kwargs: Any) -> Any:
    return await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model=model, max_tokens=max_tokens, **kwargs
    )


# --- litellm no longer retries ---------------------------------------------------------------------


async def test_litellm_is_told_not_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [])
    await _complete()
    assert calls[0]["num_retries"] == 0


async def test_a_max_tokens_rejection_is_sent_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [BadRequestError(_OPENAI_TOO_MANY)] * 5)
    with pytest.raises(LLMConfigError):
        await _complete()
    assert len(calls) == 1


async def test_any_bad_request_is_sent_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [BadRequestError("something else is wrong")] * 5)
    with pytest.raises(LLMError) as raised:
        await _complete()
    assert len(calls) == 1
    assert not isinstance(raised.value, LLMConfigError), "only a rejection of the settings is a config error"


# --- the error says what to change -----------------------------------------------------------------


async def test_the_config_error_names_model_limit_and_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [], [BadRequestError(_OPENAI_TOO_MANY)])
    with pytest.raises(LLMConfigError) as raised:
        await _complete("gpt-4o")
    text = str(raised.value)
    assert "`gpt-4o` rejected max_tokens=32,000: it allows at most 16,384 output tokens" in text
    assert "SDLC_CODEGEN_MAX_TOKENS=16384" in text
    assert "SDLC_CODEGEN_MODEL" in text
    assert "sent once and not retried" in text
    assert "supports at most 16384 completion tokens" in text, "the provider's own words are kept"


async def test_the_anthropic_wording_yields_its_limit_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [], [BadRequestError(_ANTHROPIC_TOO_MANY)])
    with pytest.raises(LLMConfigError) as raised:
        await _complete("claude-opus-5", max_tokens=200_000)
    assert "it allows at most 128,000 output tokens" in str(raised.value)
    assert "SDLC_CODEGEN_MAX_TOKENS=128000" in str(raised.value)


async def test_an_unstated_limit_still_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [], [BadRequestError("max_tokens is too large for this model")])
    with pytest.raises(LLMConfigError) as raised:
        await _complete()
    assert "SDLC_CODEGEN_MAX_TOKENS=a smaller value" in str(raised.value)


async def test_an_unsupported_parameter_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [], [BadRequestError("Unsupported parameter: 'logprobs' is not supported")])
    with pytest.raises(LLMConfigError) as raised:
        await _complete()
    assert "rejected a request parameter" in str(raised.value)


async def test_a_context_window_overflow_is_not_called_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its text mentions tokens too, but the prompt is what is too big, not a setting."""
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [ContextWindowExceededError("maximum context length; max_tokens 32000")])
    with pytest.raises(LLMError) as raised:
        await _complete()
    assert not isinstance(raised.value, LLMConfigError)
    assert len(calls) == 1


def test_a_config_error_is_an_llm_error() -> None:
    """Every caller that handles `LLMError` handles it unchanged."""
    assert issubclass(LLMConfigError, LLMError)


# --- transient failures are still retried ----------------------------------------------------------


@pytest.mark.parametrize(
    "transient",
    [RateLimitError("slow down"), APIConnectionError("reset"), _StatusError(503), _StatusError(429)],
    ids=["rate-limit", "connection", "503", "429"],
)
async def test_a_transient_failure_is_retried_and_can_succeed(
    monkeypatch: pytest.MonkeyPatch, transient: Exception
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [transient, transient])
    result = await _complete()
    assert result.text == "ok"
    assert len(calls) == 3


async def test_retries_stop_at_the_configured_count(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [RateLimitError("slow down")] * 10)
    with pytest.raises(LLMError, match="RateLimitError"):
        await LiteLLMClient(num_retries=2).complete([Message(role="user", content="x")], model="m")
    assert len(calls) == 3


async def test_no_retries_means_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [RateLimitError("slow down")] * 10)
    with pytest.raises(LLMError):
        await LiteLLMClient(num_retries=0).complete([Message(role="user", content="x")], model="m")
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [Timeout("timed out"), _StatusError(400), _StatusError(404)], ids=str)
async def test_a_timeout_or_a_4xx_is_not_retried(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    """A timeout already waited the whole budget; resending it triples the stall and learns nothing."""
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [failure] * 5)
    with pytest.raises(LLMError):
        await _complete()
    assert len(calls) == 1


async def test_each_attempt_gets_its_own_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under litellm's retries one `wait_for` covered all attempts, so a retry inherited what was left."""
    timeouts: list[float] = []
    real_wait_for = _module.asyncio.wait_for

    async def recording_wait_for(awaitable: Any, timeout: float) -> Any:
        timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(_module.asyncio, "wait_for", recording_wait_for)
    _install(monkeypatch, [], [RateLimitError("slow down")])
    await LiteLLMClient(request_timeout_seconds=42.0).complete([Message(role="user", content="x")], model="m")
    assert timeouts == [42.0, 42.0]


# --- the native-schema fallback does not resend a token-limit rejection ----------------------------


async def test_a_token_limit_rejection_of_the_native_request_is_not_resent_as_a_forced_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forced tool carries the same max_tokens, so falling back would be refused the same way."""
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, [BadRequestError(_OPENAI_TOO_MANY)] * 5)
    monkeypatch.setattr(_module, "_supports_native_structured_output", lambda model: True)
    tool = ToolSpec(name="submit_files", description="d", parameters={"type": "object"}, native_schema=True)
    with pytest.raises(LLMConfigError):
        await _complete(tools=[tool], tool_choice="submit_files", json_object=True)
    assert len(calls) == 1
    assert "response_format" in calls[0], "it was the native request that was refused"
    assert "gpt-4o" not in _module._NATIVE_SCHEMA_REFUSED
