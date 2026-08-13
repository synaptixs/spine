"""LiteLLM-backed implementation of LLMClient."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from pydantic import BaseModel

from orchestrator.core.llm.client import (
    CompletionResult,
    LLMError,
    Message,
    ToolCall,
    ToolSpec,
)

logger = logging.getLogger("orchestrator.core.llm")


def _auth_failure(exc: Exception) -> bool:
    """Whether this failure is "no usable credentials for that model".

    Keyed off the failure itself rather than a pre-flight check, because
    ``litellm.validate_environment`` proved unreliable in-process — it reported a missing
    key standalone and reported the same environment fine once the CLI was imported. A
    gate that silently never fires is worse than no gate.
    """
    text = str(exc).lower()
    return "authenticationerror" in text or "no key is set" in text or "missing" in text and "api key" in text


def _rejects_temperature(exc: Exception) -> bool:
    """Whether this failure is the provider refusing the temperature we asked for.

    Matched on the message because litellm raises ``UnsupportedParamsError`` for several
    unrelated parameters, and retrying a prompt that failed for another reason would turn
    one clear error into two.
    """
    text = str(exc).lower()
    return "temperature" in text and ("unsupported" in text or "only temperature" in text)


class LiteLLMClient:
    """Thin async wrapper over ``litellm.acompletion``.

    LiteLLM dispatches by model name (``claude-opus-4-7``, ``gpt-4o``,
    ``bedrock/anthropic.claude-3-opus-...``, etc.), so callers pick the
    provider via the agent template's ``model`` field. Provider credentials
    are read from env vars (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ...).
    """

    def __init__(
        self,
        *,
        num_retries: int = 2,
        request_timeout_seconds: float | None = None,
        fallbacks: list[str] | None = None,
    ) -> None:
        self._num_retries = num_retries
        # Configurable so large-page intake / 16k-token codegen don't trip the
        # client timeout. Default 300s: heavy codegen calls have been observed at
        # ~180s, well past litellm's own 60s default. See the timeout param below —
        # litellm honors `timeout`, not the older `request_timeout` alias, so a
        # value passed only as `request_timeout` is silently ignored (the bug that
        # let a 60s default time out a 182s call).
        self._request_timeout = (
            request_timeout_seconds
            if request_timeout_seconds is not None
            else float(os.getenv("ORCHESTRATOR_LLM_TIMEOUT_SECONDS", "300"))
        )
        self._fallbacks = fallbacks

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
        import litellm  # imported lazily so unit tests can mock the symbol

        # litellm prints a "Give Feedback / Get Help" banner to the console every time it
        # *maps* an exception — including ones we catch and recover from. A run that
        # succeeded after the temperature retry below emitted six of those blocks, which
        # reads as six failures. The exceptions still propagate; only the banner is off.
        litellm.suppress_debug_info = True

        params: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "num_retries": self._num_retries,
            # `timeout` is the parameter litellm actually applies; `request_timeout`
            # is kept as the back-compat alias. Without `timeout`, litellm falls
            # back to its built-in 60s and ignores our value.
            "timeout": self._request_timeout,
            "request_timeout": self._request_timeout,
        }
        if tools:
            params["tools"] = [t.to_dict() for t in tools]
            # Forcing a named tool is the one output-shape constraint every provider
            # honors: litellm maps this to OpenAI's `tool_choice` and to Anthropic's
            # `{"type": "tool", "name": ...}`. The model cannot answer with prose,
            # so the arguments arrive schema-valid instead of needing to be parsed
            # out of whatever it decided to say (see `json_object` below, which
            # Anthropic has no equivalent for and drops).
            if tool_choice:
                params["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        # Newer reasoning models (e.g. claude-opus-4-7) reject `temperature`
        # entirely. Only forward it when the caller explicitly opts in.
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if self._fallbacks:
            params["fallbacks"] = self._fallbacks
        # An explicit schema wins; otherwise `json_object` asks for plain-JSON
        # output. LiteLLM maps `{"type": "json_object"}` to each provider's
        # native mode (OpenAI json_object, Ollama `format=json`, …) — the lever
        # that makes smaller/local models reliably emit parseable JSON for the
        # codegen + intake stages.
        if response_format is not None:
            params["response_format"] = response_format
        elif json_object:
            params["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        try:
            response = await litellm.acompletion(**params)
        except Exception as exc:  # litellm raises a wide variety of exceptions
            # Some models accept `temperature` but only at their own fixed value —
            # `claude-opus-5` takes 1 and rejects everything else. Intake pins 0.0 for
            # determinism, so on those models every `--source` run died on the first
            # call. Retry once without the parameter rather than fail the run.
            #
            # **The determinism the pin buys is lost when this fires**, so it is said out
            # loud rather than swallowed: the caller asked for a stable temperature and
            # did not get one, and a spec that silently varies between runs is worse than
            # a warning nobody reads.
            if _auth_failure(exc):
                # Say which model, because the key and the model are separate settings and
                # the mismatch is the confusing case: someone with OPENAI_API_KEY set is
                # told the *Anthropic* key is missing, and nothing connects the two.
                raise LLMError(
                    f"no usable credentials for `{model}` — set the API key for its provider, "
                    "or point ORCHESTRATOR_MODEL at a model you have access to "
                    "(`orchestrator models` lists them)."
                ) from exc
            if "temperature" not in params or not _rejects_temperature(exc):
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            refused = params.pop("temperature")
            # A sentence, not an event name: this is printed at a human on a terminal, and
            # what it costs them (a spec that may differ between runs) is the part worth
            # reading. The structured fields stay for whoever is parsing logs.
            logger.warning(
                "%s rejected temperature=%s — retried without it, so this call is not "
                "deterministic. A spec derived here may differ between runs.",
                model,
                refused,
                extra={"event": "llm.temperature_unsupported", "model": model, "temperature": refused},
            )
            try:
                response = await litellm.acompletion(**params)
            except Exception as retry_exc:
                raise LLMError(f"{type(retry_exc).__name__}: {retry_exc}") from retry_exc
        latency_ms = (time.perf_counter() - start) * 1000.0

        text, prompt_tokens, completion_tokens = _extract(response)

        try:
            cost_usd = float(litellm.completion_cost(completion_response=response))
        except Exception:  # cost lookup is best-effort
            cost_usd = 0.0

        return CompletionResult(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=round(latency_ms, 3),
            raw=_as_dict(response),
            tool_calls=_extract_tool_calls(response),
        )


def _extract(response: Any) -> tuple[str, int, int]:
    choices = getattr(response, "choices", None) or response.get("choices", [])
    if not choices:
        raise LLMError("LLM returned no choices.")
    message = choices[0].message if hasattr(choices[0], "message") else choices[0]["message"]
    content = getattr(message, "content", None) or message.get("content", "")

    usage = getattr(response, "usage", None) or response.get("usage", {}) or {}
    prompt_tokens = int(getattr(usage, "prompt_tokens", None) or usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(
        getattr(usage, "completion_tokens", None) or usage.get("completion_tokens", 0) or 0
    )
    return content or "", prompt_tokens, completion_tokens


def _extract_tool_calls(response: Any) -> tuple[ToolCall, ...]:
    """Pull OpenAI-style tool calls off the first choice (empty when none)."""
    choices = getattr(response, "choices", None) or (
        response.get("choices", []) if isinstance(response, dict) else []
    )
    if not choices:
        return ()
    message = choices[0].message if hasattr(choices[0], "message") else choices[0]["message"]
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    if not raw_calls:
        return ()
    out: list[ToolCall] = []
    for call in raw_calls:
        fn = getattr(call, "function", None) or (call.get("function", {}) if isinstance(call, dict) else {})
        name = getattr(fn, "name", None) or (fn.get("name", "") if isinstance(fn, dict) else "")
        raw_args = getattr(fn, "arguments", None) or (fn.get("arguments", "") if isinstance(fn, dict) else "")
        call_id = getattr(call, "id", None) or (call.get("id", "") if isinstance(call, dict) else "")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        out.append(
            ToolCall(id=str(call_id), name=str(name), arguments=dict(args) if isinstance(args, dict) else {})
        )
    return tuple(out)


def _as_dict(response: Any) -> dict[str, Any] | None:
    if hasattr(response, "model_dump"):
        try:
            dumped: dict[str, Any] = response.model_dump()
        except Exception:
            return None
        return dumped
    if isinstance(response, dict):
        return response
    return None
