"""LiteLLM-backed implementation of LLMClient."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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

# Models for which a reasoning level the operator chose was not sent, already said once. Module-level
# for the same reason as `_TEMPERATURE_REFUSED`, and not persisted: a fresh process says it again.
_REASONING_UNSUPPORTED_WARNED: set[str] = set()


def _supports_reasoning(model: str) -> bool:
    """Does litellm report this model as reasoning-capable?

    Read from ``litellm.model_cost`` rather than a hardcoded list, for the same reason
    ``catalog.py`` does: a new frontier model then arrives with a ``litellm`` upgrade and
    no edit here. An unknown model answers ``False`` — the conservative direction, because
    sending the parameter to a model that rejects it would break a path that works today.

    The table is keyed by bare name, so ``openai/gpt-5.1`` is absent from it although ``gpt-5.1``
    is not; that miss used to answer ``False`` and silently ran the model at its own default
    level instead of the one the operator chose (B79). A name the table lacks is therefore
    handed to ``litellm.supports_reasoning``, which resolves the provider prefix. The exact key
    is still read first and its answer is final, so a name that answered before answers the same
    now: this only ever turns a ``False`` for an absent name into the model's real answer.
    """
    try:
        import litellm

        entry = litellm.model_cost.get(model)
        if entry is not None:
            return bool(entry.get("supports_reasoning"))
        return bool(litellm.supports_reasoning(model))
    except Exception:
        return False


def _supports_native_structured_output(model: str) -> bool:
    """Does litellm report this model as able to constrain its output to a JSON schema?

    Read from the same table as ``_supports_reasoning``, and answering ``False`` for the same
    reason: an unknown model, or a litellm without the table, gets the request Spine has always sent.
    """
    try:
        import litellm

        return bool(litellm.model_cost.get(model, {}).get("supports_native_structured_output"))
    except Exception:
        return False


def _disables_thinking(model: str) -> bool:
    """Should the native request switch this model's thinking off explicitly? (B83, SSPN-167)

    Only for a model litellm lists under the ``anthropic`` provider **and** reports as a reasoning model.
    That is the set where the parameter is a documented API setting that litellm carries to the request
    (checked across the table: carried on the 22 Anthropic-provider entries). Sending it everywhere would
    break working native requests: for 31 of the 125 native-capable entries litellm raises
    ``UnsupportedParamsError`` *only* because of this parameter, and that is a ``BadRequestError``
    subclass the exact-name fallback does not catch. Same direction as the two reads above: an unknown
    model, a missing field or a litellm without the table answers ``False`` and sends nothing.
    """
    try:
        import litellm

        entry = litellm.model_cost.get(model, {})
        return entry.get("litellm_provider") == "anthropic" and bool(entry.get("supports_reasoning"))
    except Exception:
        return False


# The GPT-6 family as OpenAI names it: `gpt-6-astra`, `gpt-6.1-sol`. A dash and a suffix are required, so
# `gpt-6` alone and `gpt-60` are not guessed at, and only a bare or `openai/` name matches: `azure/gpt-6-...`
# would be sent to OpenAI by the route below instead of to the Azure deployment it names.
_GPT6_FAMILY = re.compile(r"(?:openai/)?(gpt-6(?:\.\d+)?-.+)")


def _responses_route(model: str) -> str | None:
    """The litellm name that calls this model through OpenAI's Responses API, or ``None`` for any other.

    GPT-6 models cannot be driven over chat completions the way every other model Spine calls is (B45):
    OpenAI refuses ``max_tokens`` on them and refuses function tools together with reasoning, and litellm
    itself refuses ``reasoning_effort`` for them before the request leaves the process (all three measured;
    the last two on litellm 1.97 against a local server, the first from the API's own message). litellm's
    Responses bridge, ``openai/responses/<model>``, takes the same arguments and carries ``max_tokens`` as
    ``max_output_tokens`` and the effort as ``reasoning.effort`` itself.

    A name rule, not a read of ``model_cost``: the table lists ``/v1/chat/completions`` as supported for these
    models, so nothing in it says they must not be used there -- and the price table Spine runs on (litellm's
    bundled copy, pinned by ``catalog``) lists no GPT-6 model at all, so under it litellm does not even know
    the bare name's provider ("LLM Provider NOT provided", measured). Naming ``openai/responses/`` supplies
    it.
    """
    match = _GPT6_FAMILY.fullmatch(model)
    return f"openai/responses/{match.group(1)}" if match else None


# Models whose provider refused the native structured-output request earlier in this process, and
# accepted the forced tool in its place. Module-level for the same reason as `_TEMPERATURE_REFUSED`
# above (callers build clients both per run and per operation), and deliberately not persisted: a
# fresh process re-probes it.
_NATIVE_SCHEMA_REFUSED: set[str] = set()


def _native_schema_tool(
    model: str,
    tools: list[ToolSpec] | None,
    tool_choice: str | None,
    response_format: type[BaseModel] | None,
) -> ToolSpec | None:
    """The one forced tool to send as native structured output, or ``None`` for today's request.

    B77 (SSPN-161). A forced tool call on ``claude-opus-5`` returned ``files`` as a garbled string in about a
    quarter of calls. Constraining the output to the tool's own schema removes that, but only the **cheap**
    form: sending ``effort`` (or ``thinking`` enabled) on that path switches thinking on (14x the output
    tokens, 17x the latency, 1.6x the cost, one call in ten truncated at ``max_tokens``), so the caller of
    this path sends no ``reasoning_effort``. Sending *nothing* does not keep thinking off either (B83: the
    model thinks by default, 5,157 to 7,935 output tokens a reply live against about 2,008 under the forced
    tool), so a model that takes it gets ``thinking: disabled`` explicitly (``_disables_thinking``).
    Everything here is monotone: any doubt answers ``None``.

    Translated only when the tool opted in (``native_schema``), it is the single tool and the forced one,
    the caller gave no schema of its own (an explicit schema wins), the model's capability table says it is
    supported, the model has not refused it earlier in this process, and the operator has not set
    ``ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT=0``.
    """
    if not tools or len(tools) != 1 or response_format is not None:
        return None
    tool = tools[0]
    if not tool.native_schema or tool_choice != tool.name:
        return None
    if os.environ.get("ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT", "1").strip() == "0":
        return None
    if model in _NATIVE_SCHEMA_REFUSED:
        return None
    return tool if _supports_native_structured_output(model) else None


def _is_bad_request(exc: Exception) -> bool:
    """Whether the provider rejected the request itself (litellm's ``BadRequestError``).

    Matched on the exact class name: its subclasses (context window exceeded, content policy, unsupported
    params) are failures of the *prompt* or of another parameter, and falling back would only repeat them.
    """
    return type(exc).__name__ == "BadRequestError"


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
        # Whether the operator chose that level. The `high` default is not a choice, and saying
        # "not sent" about it for every model that has no reasoning would be noise, not news.
        self._reasoning_chosen = bool(os.getenv("ORCHESTRATOR_REASONING_EFFORT"))

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
        native_tool = _native_schema_tool(model, tools, tool_choice, response_format)

        def _use_forced_tool() -> None:
            if not tools:
                return
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
            #
            # Every GPT-6 model is a reasoning model (litellm's table says so for each one it lists), but
            # Spine runs litellm on its bundled price table, which lists none of them -- so for this family
            # the answer comes from the name (B45), the same name rule that picks the Responses route below.
            if _supports_reasoning(model) or _responses_route(model) is not None:
                params["reasoning_effort"] = self._reasoning_effort
            elif self._reasoning_chosen and model not in _REASONING_UNSUPPORTED_WARNED:
                # Said once per model: a level that was asked for and not sent is the difference
                # between the run the operator thinks they configured and the one that happened.
                _REASONING_UNSUPPORTED_WARNED.add(model)
                logger.warning(
                    "ORCHESTRATOR_REASONING_EFFORT=%s is not sent to %s: litellm does not report it as "
                    "a reasoning model, so it runs at its own default level.",
                    self._reasoning_effort,
                    model,
                    extra={
                        "event": "llm.reasoning_unsupported",
                        "model": model,
                        "reasoning_effort": self._reasoning_effort,
                    },
                )

        if native_tool is not None:
            # B77: the tool's own schema as the response format -- no `tools`, no `tool_choice`, and
            # deliberately no `reasoning_effort` (see `_native_schema_tool`). The reply is text; the
            # caller's existing "the provider answered in text" path reads it.
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": native_tool.name, "schema": native_tool.parameters},
            }
            # B83: with no parameter at all the model thinks anyway (a forced tool prevents that; this
            # request does not), so say so explicitly -- but only where it is known to work.
            if _disables_thinking(model):
                params["thinking"] = {"type": "disabled"}
        else:
            _use_forced_tool()
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

        # B45: a GPT-6 model goes through the Responses API. `allowed_openai_params` is what lets litellm
        # forward `reasoning_effort` for it; it is only named when the level is actually in the request.
        if (routed := _responses_route(model)) is not None:
            params["model"] = routed
            if "reasoning_effort" in params:
                params["allowed_openai_params"] = ["reasoning_effort"]

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

        using_native = native_tool is not None
        rejection: str | None = None

        async def _send() -> Any:
            """``_call``, with one fallback: a provider that rejects the native request gets the forced tool.

            The same pattern as the temperature refusal below: a rejected request costs nothing and a failure
            that is not a rejection is not retried. The refusal is remembered for the model only once the
            forced tool has *worked* where the native request did not: a bad request that both share (an
            exhausted balance) says nothing about the native format. A rejected ``temperature`` is not a
            refusal of it either; the temperature retry below drops the parameter and sends the same request.
            """
            nonlocal using_native, rejection
            if using_native:
                try:
                    return await _call()
                except Exception as native_exc:
                    if not _is_bad_request(native_exc):
                        raise
                    if "temperature" in params and _rejects_temperature(native_exc):
                        raise
                    using_native = False
                    rejection = str(native_exc)[:200]
                    params.pop("response_format", None)
                    params.pop("thinking", None)  # the forced tool is today's request, which never carried it
                    _use_forced_tool()
            response = await _call()
            if rejection is not None:
                _NATIVE_SCHEMA_REFUSED.add(model)
                logger.warning(
                    "%s rejected the native structured-output request (%s) -- retried with the forced tool, "
                    "and will not be asked again in this process. Set "
                    "ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT=0 to skip the attempt.",
                    model,
                    rejection,
                    extra={"event": "llm.native_schema_refused", "model": model},
                )
                rejection = None
            return response

        start = time.perf_counter()
        try:
            response = await _send()
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
                response = await _send()
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
