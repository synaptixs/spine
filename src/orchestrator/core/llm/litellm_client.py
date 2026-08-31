"""LiteLLM-backed implementation of LLMClient."""

from __future__ import annotations

import asyncio
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


# Models that refused `temperature` earlier in this process. A fact about the model, not
# state of one client, so it is module-level: callers construct `LiteLLMClient` both once
# per run (`sdlc/worker.py`) and once per operation (`gateway/tools/summarize_text.py`),
# and an instance attribute would leave the per-operation callers re-learning it forever.
#
# **Deliberately not persisted.** A fresh process re-probes, so a transient refusal — a
# router landing on a different backend, a provider changing what it accepts — costs one
# retry rather than permanently downgrading a model on the strength of one bad call.
_TEMPERATURE_REFUSED: set[str] = set()


def _supports_reasoning(model: str) -> bool:
    """Does litellm report this model as reasoning-capable?

    Read from ``litellm.model_cost`` rather than a hardcoded list, for the same reason
    ``catalog.py`` does: a new frontier model then arrives with a ``litellm`` upgrade and
    no edit here. An unknown model answers ``False`` — the conservative direction, because
    sending the parameter to a model that rejects it would break a path that works today.
    """
    try:
        import litellm

        return bool(litellm.model_cost.get(model, {}).get("supports_reasoning"))
    except Exception:
        return False


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
        # Reasoning level sent to reasoning-capable models when tools are in play (see
        # `complete`). `high` preserves the reasoning these models are chosen for;
        # override to trade quality for latency/cost, never to `none` unless you mean it.
        self._reasoning_effort = os.getenv("ORCHESTRATOR_REASONING_EFFORT", "high")

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
            # A reasoning model must be told its reasoning level *explicitly* when tools
            # are present. OpenAI rejects function tools alongside the model's own default
            # reasoning mode on `/v1/chat/completions` — verified on `gpt-5.6-sol`, where
            # omitting the parameter fails every call while `low`/`medium`/`high` all
            # succeed and return schema-valid tool arguments. Naming a level is therefore
            # *not* a trade of reasoning for tool-calling: the error text suggests
            # `reasoning_effort='none'`, which would disable the reasoning that makes these
            # models good at codegen, and that is the wrong fix. Anthropic accepts the
            # parameter either way, so this is safe to send to any reasoning model.
            if _supports_reasoning(model):
                params["reasoning_effort"] = self._reasoning_effort
        # Newer reasoning models (e.g. claude-opus-4-7) reject `temperature`
        # entirely. Only forward it when the caller explicitly opts in — and not at all
        # once this model has refused it in this process, which turns the retry below from
        # a per-call tax into a one-off.
        if temperature is not None and model not in _TEMPERATURE_REFUSED:
            params["temperature"] = temperature
        elif temperature is not None:
            # Warn *once*, at the refusal. Repeating the sentence on every later call is
            # the noise `suppress_debug_info` above exists to kill, and the second one adds
            # nothing a reader did not learn from the first. The fact still has to be
            # recoverable after the fact — a spec that varies between runs must be
            # explicable — so it stays in the log, at debug, under its own event name: this
            # call skipped the parameter and paid no retry, which is not what
            # `llm.temperature_unsupported` means.
            logger.debug(
                "%s refused temperature earlier in this process — sending the request without "
                "temperature=%s rather than retrying it.",
                model,
                temperature,
                extra={"event": "llm.temperature_skipped", "model": model, "temperature": temperature},
            )
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
        elif json_object and not tools:
            # `json_object` is the fallback for providers with no tool-calling — it is
            # redundant whenever a tool is forced, because `tool_choice` above already
            # guarantees schema-valid arguments rather than prose.
            #
            # It is also actively harmful on OpenAI: sending `tools` *and* `json_object`
            # together routes the call to the Responses API, which rejects it with
            # *"Response input messages must contain the word 'json' in some form to use
            # 'text.format' of type 'json_object'."* — and unlike the chat-completions
            # form of that rule, adding the word to a system message does **not** satisfy
            # it. Verified on `gpt-5.6-sol`: tools+tool_choice+reasoning succeeds, and the
            # same call plus `json_object` fails however the prompt is worded. Untreated,
            # this aborted 6-8 of every 10 codegen tickets.
            params["response_format"] = {"type": "json_object"}

        # `timeout`/`request_timeout` are passed to litellm above, but they are honoured by
        # the provider path litellm picks — and it does not pick the same one for every
        # request. A `gpt-5.6-sol` call carrying tools is routed to OpenAI's Responses API,
        # where neither parameter took effect: a stalled request hung with no timeout and no
        # error. Measured 2026-08-16 — one benchmark arm sat for **4h16m at 0% CPU with no
        # child processes**, blocked on a single call, then resumed as if nothing happened.
        #
        # That is worse in production than in a benchmark: an `sdlc` run would hang for
        # hours with nothing in the logs and the run budget never tripping, because a call
        # that never returns never bills.
        #
        # `asyncio.wait_for` enforces the timeout in *our* process, so it holds regardless of
        # which endpoint litellm chooses or whether that endpoint honours its own parameter.
        # The litellm-level values stay: they let the provider fail fast when it can.
        async def _call() -> Any:
            return await asyncio.wait_for(litellm.acompletion(**params), timeout=self._request_timeout)

        start = time.perf_counter()
        try:
            response = await _call()
        except TimeoutError as exc:
            raise LLMError(
                f"`{model}` did not respond within {self._request_timeout:.0f}s "
                "(client-side timeout; the provider path may not enforce its own). "
                "Raise ORCHESTRATOR_LLM_TIMEOUT_SECONDS if this model is legitimately slow."
            ) from exc
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
            # Learned once per process: every later call to this model skips the parameter
            # at assembly instead of discovering the same refusal again. Before this, a run
            # against such a model paid a failed round-trip *per call* and printed the
            # warning below each time.
            _TEMPERATURE_REFUSED.add(model)
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
                response = await _call()
            except TimeoutError as retry_exc:
                raise LLMError(
                    f"`{model}` did not respond within {self._request_timeout:.0f}s on the "
                    "temperature retry (client-side timeout)."
                ) from retry_exc
            except Exception as retry_exc:
                raise LLMError(f"{type(retry_exc).__name__}: {retry_exc}") from retry_exc
        latency_ms = (time.perf_counter() - start) * 1000.0

        text, prompt_tokens, completion_tokens = _extract(response)

        try:
            cost_usd = float(litellm.completion_cost(completion_response=response))
        except Exception:  # cost lookup is best-effort
            cost_usd = 0.0

        # The model that *answered*, not the one we asked for — so a fallback, a router, or
        # a provider substitution shows up in the ledger instead of being recorded as the
        # model we requested.
        #
        # It does **not** pin a snapshot, and that limit is worth stating rather than
        # discovering later: probed 2026-08-15, both `claude-opus-5` and `gpt-5.6-sol` echo
        # the *alias* back rather than a dated id, so the weights behind a benchmark number
        # can still move without anything here changing. Pair a published figure with its
        # run date; the response alone will not reproduce it.
        served_model = str(getattr(response, "model", "") or model)

        return CompletionResult(
            text=text,
            model=served_model,
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
