"""B77 (SSPN-161): a forced single tool that opts in is sent as native structured output.

On ``claude-opus-5`` codegen's forced ``submit_files`` call came back with ``files`` as a garbled string in
about a quarter of calls (a captured request replayed unchanged, straight to the API: 3 of 12; the original
responses to the same capture: 3 of 11). Constraining the output removes the malformed shape, and the
experiment found the variant to send: the tool's own schema as a ``json_schema`` response format, with **no
reasoning parameters** -- adding ``thinking``/``effort`` on that path switched thinking on (14x the output
tokens, 17x the latency, 1.6x the cost, one call in ten truncated at ``max_tokens``).

The rule under test is monotone: a ``ToolSpec`` that sets ``native_schema`` is translated **only** when it is
the one forced tool, the model's capability table says it supports native structured output, and the
operator has not switched it off. Everything else is the request Spine has always sent, and a provider that
rejects the native request gets that request instead, once, and is not asked again.

The new field does not exist before this change, so the tests set and read it dynamically: they then fail on
behaviour (the request still carries the tool) rather than on a ``TypeError`` at construction.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from orchestrator.core.llm.client import LLMError, Message, ToolSpec
from orchestrator.core.llm.litellm_client import LiteLLMClient

_CAPABLE = "claude-opus-5"
_REASONING_ONLY = "gpt-5.6-sol"
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}}}},
        "summary": {"type": "string"},
    },
    "required": ["files"],
}
_MODEL_COST: dict[str, dict[str, Any]] = {
    _CAPABLE: {"supports_native_structured_output": True, "supports_reasoning": True},
    _REASONING_ONLY: {"supports_reasoning": True},
    "gpt-4o": {},
}
_REPLY_JSON = '{"files": [], "summary": "x"}'


class BadRequestError(Exception):
    """Named as litellm names it: the client recognises a provider rejection by this class name."""


def _tool(*, native: bool = True, name: str = "submit_files") -> ToolSpec:
    tool = ToolSpec(name=name, description="d", parameters=_SCHEMA)
    if native:
        object.__setattr__(tool, "native_schema", True)  # the field this change adds
    return tool


def _install(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    *,
    model_cost: dict[str, dict[str, Any]] | None = None,
    raise_on: Callable[[dict[str, Any]], Exception | None] | None = None,
) -> None:
    mod = types.ModuleType("litellm")

    async def acompletion(**params: Any) -> dict[str, Any]:
        calls.append(params)
        if raise_on is not None and (exc := raise_on(params)) is not None:
            raise exc
        if "tools" in params:
            message: dict[str, Any] = {
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "submit_files", "arguments": _REPLY_JSON}}],
            }
        else:
            message = {"content": _REPLY_JSON}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def completion_cost(**_: Any) -> float:
        return 0.0

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = completion_cost  # type: ignore[attr-defined]
    if model_cost is not None:
        mod.model_cost = model_cost  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)


@pytest.fixture(autouse=True)
def _forget_native_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal memory is module-level (a fact about the model), so it outlives a test."""
    monkeypatch.delenv("ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT", raising=False)
    module = importlib.import_module("orchestrator.core.llm.litellm_client")
    getattr(module, "_NATIVE_SCHEMA_REFUSED", set()).clear()


async def _complete(
    model: str, tools: list[ToolSpec], tool_choice: str | None = "submit_files", **kwargs: Any
) -> Any:
    return await LiteLLMClient().complete(
        [Message(role="user", content="hi")],
        model=model,
        json_object=True,
        max_tokens=1000,
        tools=tools,
        tool_choice=tool_choice,
        **kwargs,
    )


# --- the translation ------------------------------------------------------------------------------


async def test_a_native_schema_tool_is_sent_as_a_json_schema_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    await _complete(_CAPABLE, [_tool()])

    request = calls[0]
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "submit_files", "schema": _SCHEMA},
    }
    assert "tools" not in request and "tool_choice" not in request
    assert request["max_tokens"] == 1000


async def test_the_native_request_carries_no_reasoning_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    """The measured trap: the model supports reasoning, and sending it here switches thinking on."""
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    await _complete(_CAPABLE, [_tool()])

    assert "reasoning_effort" not in calls[0] and "thinking" not in calls[0]


async def test_the_native_reply_is_text_with_no_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    result = await _complete(_CAPABLE, [_tool()])

    assert result.text == _REPLY_JSON
    assert result.tool_calls == ()


# --- everything else is the request Spine has always sent -----------------------------------------


def _assert_forced_tool(request: dict[str, Any], *, reasoning: bool) -> None:
    assert request["tools"] == [_tool(native=False).to_dict()]
    assert request["tool_choice"] == {"type": "function", "function": {"name": "submit_files"}}
    assert "response_format" not in request
    assert ("reasoning_effort" in request) is reasoning


async def test_a_model_without_the_capability_gets_the_forced_tool_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    await _complete(_REASONING_ONLY, [_tool()])

    _assert_forced_tool(calls[0], reasoning=True)


async def test_a_tool_that_did_not_opt_in_is_unchanged_on_a_capable_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    await _complete(_CAPABLE, [_tool(native=False)])

    _assert_forced_tool(calls[0], reasoning=True)


@pytest.mark.parametrize(
    ("tools", "tool_choice"),
    [
        pytest.param(lambda: [_tool(), _tool(name="other")], "submit_files", id="two-tools"),
        pytest.param(lambda: [_tool()], None, id="the-tool-is-not-forced"),
        pytest.param(lambda: [_tool()], "other", id="another-tool-is-forced"),
    ],
)
async def test_only_a_single_forced_tool_is_ever_translated(
    monkeypatch: pytest.MonkeyPatch,
    tools: Callable[[], list[ToolSpec]],
    tool_choice: str | None,
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)

    await _complete(_CAPABLE, tools(), tool_choice)

    assert "response_format" not in calls[0]
    assert "tools" in calls[0]


async def test_a_litellm_without_a_capability_table_is_treated_as_incapable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every older test installs a stand-in litellm with no ``model_cost``; an unknown answers False."""
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=None)

    await _complete(_CAPABLE, [_tool()])

    assert "response_format" not in calls[0]
    assert "tools" in calls[0]


def test_the_flag_defaults_to_false_and_is_never_sent_to_a_provider() -> None:
    plain = ToolSpec(name="t", description="d", parameters={})
    assert getattr(plain, "native_schema", None) is False
    flagged = _tool()
    assert flagged.to_dict() == {
        "type": "function",
        "function": {"name": "submit_files", "description": "d", "parameters": _SCHEMA},
    }


# --- the operator's lever --------------------------------------------------------------------------


async def test_the_kill_switch_restores_the_forced_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)
    monkeypatch.setenv("ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT", "0")

    await _complete(_CAPABLE, [_tool()])

    _assert_forced_tool(calls[0], reasoning=True)


@pytest.mark.parametrize("value", ["1", "true", ""])
async def test_only_a_zero_switches_it_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST)
    monkeypatch.setenv("ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT", value)

    await _complete(_CAPABLE, [_tool()])

    assert "response_format" in calls[0]


# --- a provider that refuses the native request -----------------------------------------------------


def _refuse_native(params: dict[str, Any]) -> Exception | None:
    return BadRequestError("output_format.schema: not accepted") if "response_format" in params else None


async def test_a_rejected_native_request_falls_back_to_the_forced_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST, raise_on=_refuse_native)

    result = await _complete(_CAPABLE, [_tool()])

    assert len(calls) == 2
    assert "response_format" in calls[0]
    _assert_forced_tool(calls[1], reasoning=True)
    assert [c.name for c in result.tool_calls] == ["submit_files"]


async def test_the_rejection_is_remembered_for_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install(monkeypatch, calls, model_cost=_MODEL_COST, raise_on=_refuse_native)
    client = LiteLLMClient()
    messages = [Message(role="user", content="hi")]

    for _ in range(2):
        await client.complete(
            messages, model=_CAPABLE, json_object=True, tools=[_tool()], tool_choice="submit_files"
        )

    assert len(calls) == 3, "one refused native attempt, then the forced tool both times"
    assert "response_format" not in calls[2]


async def test_a_failure_that_is_not_a_rejection_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fail(params: dict[str, Any]) -> Exception | None:
        return RuntimeError("connection reset") if "response_format" in params else None

    _install(monkeypatch, calls, model_cost=_MODEL_COST, raise_on=fail)

    with pytest.raises(LLMError):
        await _complete(_CAPABLE, [_tool()])

    assert len(calls) == 1


async def test_a_rejection_of_the_forced_tool_itself_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback must not loop or hide a genuine bad request: its own failure propagates."""
    calls: list[dict[str, Any]] = []

    def refuse_everything(params: dict[str, Any]) -> Exception | None:
        return BadRequestError("nope")

    _install(monkeypatch, calls, model_cost=_MODEL_COST, raise_on=refuse_everything)

    with pytest.raises(LLMError):
        await _complete(_CAPABLE, [_tool()])

    assert len(calls) == 2


# --- the assumption the whole gate rests on ---------------------------------------------------------


def test_the_installed_litellm_reports_the_capability_for_claude_opus_5() -> None:
    """If a litellm upgrade drops this entry, the gate silently turns the fix off: make that visible."""
    import litellm

    entry = litellm.model_cost.get("claude-opus-5", {})
    assert entry.get("supports_native_structured_output") is True
