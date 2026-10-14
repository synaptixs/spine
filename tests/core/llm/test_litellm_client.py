"""LiteLLMClient param wiring — focus on the JSON-object (Ollama-friendly) mode.

``litellm`` is imported lazily inside ``complete``; we install a fake module so
the test asserts what params reach ``litellm.acompletion`` without a network call.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import types
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm.client import LLMError, Message, ToolSpec
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


@pytest.fixture(autouse=True)
def _forget_temperature_refusals() -> Any:
    """The refusal cache is module-level and outlives a client, so it outlives a test.

    Without this every test after the first refusal would exercise the *cached* path while
    appearing to exercise the retry — a green suite that stopped testing what it names.
    """
    from orchestrator.core.llm import litellm_client

    litellm_client._TEMPERATURE_REFUSED.clear()
    yield
    litellm_client._TEMPERATURE_REFUSED.clear()


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


async def test_the_refusal_is_learned_once_not_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cost this closes: a failed round-trip on *every* call, not just the first."""
    calls: list[dict[str, Any]] = []
    _install_temperature_refusing_litellm(monkeypatch, calls)

    # Two separate clients, because callers construct one per operation as well as one per
    # run — an instance-level cache would pass a same-client test and change nothing there.
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
    )
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
    )

    # Call 1 carried it and failed, its retry did not, and call 2 never tried: three
    # round-trips for two completions rather than four.
    assert [("temperature" in c) for c in calls] == [True, False, False]


async def test_the_refusal_is_remembered_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """One model's refusal must not silently drop the parameter for every other model."""
    calls: list[dict[str, Any]] = []
    _install_temperature_refusing_litellm(monkeypatch, calls)

    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
    )
    await LiteLLMClient().complete([Message(role="user", content="hi")], model="gpt-4o", temperature=0.0)

    # `gpt-4o` was asked — it refuses too, under this fake, and pays its own one-off retry.
    # What must not happen is inheriting the other model's refusal and never being asked.
    asked = [(c["model"], "temperature" in c) for c in calls]
    assert ("gpt-4o", True) in asked


async def test_the_determinism_warning_is_not_repeated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn once. Six identical warnings in one run read as six failures."""
    _install_temperature_refusing_litellm(monkeypatch, [])
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        for _ in range(3):
            await LiteLLMClient().complete(
                [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
            )
    assert [getattr(r, "event", "") for r in caplog.records].count("llm.temperature_unsupported") == 1


async def test_the_skipped_calls_are_still_explicable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn-once must not mean *silent* — a spec that varies has to be traceable to this."""
    _install_temperature_refusing_litellm(monkeypatch, [])
    with caplog.at_level("DEBUG", logger="orchestrator.core.llm"):
        for _ in range(2):
            await LiteLLMClient().complete(
                [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.0
            )
    events = [getattr(r, "event", "") for r in caplog.records]
    assert events.count("llm.temperature_unsupported") == 1  # the refusal, warned
    assert events.count("llm.temperature_skipped") == 1  # the second call, at debug


async def test_an_unrelated_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a prompt that failed for another reason turns one error into two."""

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


# --- a provider-prefixed model name keeps its reasoning level (B79, SSPN-163) ---------------


@pytest.fixture(autouse=True)
def _forget_reasoning_warnings() -> Any:
    """Like the refusal cache above: a module-level once-per-model memory must not outlive a test."""
    from orchestrator.core.llm import litellm_client

    litellm_client._REASONING_UNSUPPORTED_WARNED.clear()
    yield
    litellm_client._REASONING_UNSUPPORTED_WARNED.clear()


_TOOL = ToolSpec(name="submit", description="d", parameters={"type": "object", "properties": {}})


def _install_reasoning_litellm(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    *,
    table: dict[str, dict[str, Any]] | None = None,
    resolves: dict[str, bool] | None = None,
    resolver_raises: bool = False,
) -> list[str]:
    """A litellm with a ``model_cost`` table and a prefix-resolving ``supports_reasoning``.

    The table is keyed by bare name, as the real one is. The resolver is the part the real
    library adds on top: it answers for ``openai/gpt-5.1`` where the table has no such key.
    Returns the names the resolver was asked about.
    """
    mod = types.ModuleType("litellm")
    asked: list[str] = []

    async def acompletion(**params: Any) -> dict[str, Any]:
        calls.append(dict(params))
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    def completion_cost(**_: Any) -> float:
        return 0.0

    def supports_reasoning(model: str, **_: Any) -> bool:
        asked.append(model)
        if resolver_raises:
            raise ValueError(f"LLM Provider NOT provided. You passed model={model}")
        return bool((resolves or {}).get(model, False))

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = completion_cost  # type: ignore[attr-defined]
    mod.supports_reasoning = supports_reasoning  # type: ignore[attr-defined]
    mod.model_cost = table or {}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)
    return asked


async def _ask(model: str) -> None:
    await LiteLLMClient().complete([Message(role="user", content="hi")], model=model, tools=[_TOOL])


async def test_a_prefixed_name_gets_the_reasoning_level_its_bare_name_gets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`openai/gpt-5.1` is the same model as `gpt-5.1`; only the table key differs (B79)."""
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(
        monkeypatch,
        calls,
        table={"gpt-5.1": {"supports_reasoning": True}},
        resolves={"openai/gpt-5.1": True},
    )
    await _ask("gpt-5.1")
    await _ask("openai/gpt-5.1")
    assert [c.get("reasoning_effort") for c in calls] == ["high", "high"]


async def test_a_name_the_table_knows_is_not_resolved_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact key is read first, so every name that worked before answers exactly as before."""
    calls: list[dict[str, Any]] = []
    asked = _install_reasoning_litellm(monkeypatch, calls, table={"gpt-5.1": {"supports_reasoning": True}})
    await _ask("gpt-5.1")
    assert calls[0]["reasoning_effort"] == "high" and asked == []


async def test_a_table_entry_that_says_no_is_not_overruled_by_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The change only ever adds answers for names the table lacks; it never flips one it has."""
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(
        monkeypatch, calls, table={"gpt-4o": {"supports_reasoning": False}}, resolves={"gpt-4o": True}
    )
    await _ask("gpt-4o")
    assert "reasoning_effort" not in calls[0]


@pytest.mark.parametrize("raises", [False, True], ids=["resolver-says-no", "resolver-raises"])
async def test_an_unknown_model_still_sends_no_reasoning_level(
    monkeypatch: pytest.MonkeyPatch, raises: bool
) -> None:
    """Sending the parameter to a model that rejects it would break a path that works today."""
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, resolver_raises=raises)
    await _ask("my-private-model")
    assert "reasoning_effort" not in calls[0]


async def test_a_litellm_without_the_resolver_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing fakes (and an older litellm) have a table and no `supports_reasoning`."""
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, table={"gpt-5.1": {"supports_reasoning": True}})
    del sys.modules["litellm"].supports_reasoning
    await _ask("openai/gpt-5.1")
    assert "reasoning_effort" not in calls[0]


async def test_a_chosen_level_that_is_not_sent_is_said_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Silently running at the model's default instead of the level you chose is the B79 harm."""
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "medium")
    _install_reasoning_litellm(monkeypatch, [])
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await _ask("my-private-model")
        await _ask("my-private-model")
        await _ask("another-private-model")
    said = [r for r in caplog.records if getattr(r, "event", "") == "llm.reasoning_unsupported"]
    assert [getattr(r, "model", "") for r in said] == ["my-private-model", "another-private-model"]
    assert all(getattr(r, "reasoning_effort", "") == "medium" for r in said)


async def test_the_default_level_is_not_announced_for_a_model_that_cannot_use_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nobody chose `high`; warning on every non-reasoning model would be noise, not information."""
    monkeypatch.delenv("ORCHESTRATOR_REASONING_EFFORT", raising=False)
    _install_reasoning_litellm(monkeypatch, [])
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await _ask("my-private-model")
    assert not [r for r in caplog.records if getattr(r, "event", "") == "llm.reasoning_unsupported"]


async def test_a_level_that_is_sent_is_not_announced(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "medium")
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, resolves={"openai/gpt-5.1": True})
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await _ask("openai/gpt-5.1")
    assert calls[0]["reasoning_effort"] == "medium"
    assert not [r for r in caplog.records if getattr(r, "event", "") == "llm.reasoning_unsupported"]


async def test_a_call_without_tools_never_mentions_a_reasoning_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The level is only ever sent alongside tools, so only there can its absence be news."""
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "medium")
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls)
    with caplog.at_level("WARNING", logger="orchestrator.core.llm"):
        await LiteLLMClient().complete([Message(role="user", content="hi")], model="my-private-model")
    assert "reasoning_effort" not in calls[0]
    assert not [r for r in caplog.records if getattr(r, "event", "") == "llm.reasoning_unsupported"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.1", True),
        ("openai/gpt-5.1", True),
        ("openai/gpt-5", True),
        ("azure/gpt-5.1", True),
        ("anthropic/claude-opus-4-5", True),
        ("gpt-4o", False),
        ("openai/gpt-4o", False),
        ("ollama/llama3", False),
        ("not-a-model", False),
    ],
)
def test_the_rows_of_the_ticket_against_the_locked_litellm(model: str, expected: bool) -> None:
    """The reproduction table in SSPN-163, run against the real library rather than a fake."""
    from orchestrator.core.llm.litellm_client import _supports_reasoning

    assert _supports_reasoning(model) is expected


# --- GPT-6 models are called through the Responses API (B45, SSPN-90) ----------------------------
#
# Measured offline with litellm 1.97 against a local recording server (no API call): for `gpt-6-*` the
# chat-completions request either never leaves the process (`reasoning_effort` -> UnsupportedParamsError)
# or goes out with `max_tokens`, which OpenAI refuses. Through litellm's Responses bridge,
# `openai/responses/<model>` carries `max_tokens` as `max_output_tokens` and the effort as
# `reasoning.effort` on its own -- so only the route and the pass-through need adding here.

_GPT6_TABLE = {name: {"supports_reasoning": True} for name in ("gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol")}


async def _gpt6_request(monkeypatch: pytest.MonkeyPatch, model: str, **kwargs: Any) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, table=_GPT6_TABLE, resolves={"openai/gpt-6-sol": True})
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model=model, tools=[_TOOL], max_tokens=100, **kwargs
    )
    return calls[0]


@pytest.mark.parametrize(
    ("asked", "sent"),
    [
        ("gpt-6-astra", "openai/responses/gpt-6-astra"),
        ("gpt-6.1-sol", "openai/responses/gpt-6.1-sol"),
        ("openai/gpt-6-sol", "openai/responses/gpt-6-sol"),
    ],
)
async def test_a_gpt6_model_is_called_through_the_responses_api(
    monkeypatch: pytest.MonkeyPatch, asked: str, sent: str
) -> None:
    request = await _gpt6_request(monkeypatch, asked)
    assert request["model"] == sent


async def test_a_gpt6_model_keeps_the_reasoning_level_litellm_would_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """litellm rejects `reasoning_effort` for gpt-6-* client-side although the API accepts it."""
    request = await _gpt6_request(monkeypatch, "gpt-6-astra")
    assert request["reasoning_effort"] == "high"
    assert "reasoning_effort" in request["allowed_openai_params"]


async def test_the_token_limit_is_left_for_the_responses_route_to_translate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renaming it here as well would be a second translation of something already translated."""
    request = await _gpt6_request(monkeypatch, "gpt-6-astra")
    assert request["max_tokens"] == 100 and "max_completion_tokens" not in request


async def test_nothing_is_passed_through_when_no_reasoning_level_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, table=_GPT6_TABLE)
    await LiteLLMClient().complete([Message(role="user", content="hi")], model="gpt-6-astra")
    assert "reasoning_effort" not in calls[0] and "allowed_openai_params" not in calls[0]


async def test_a_name_already_on_the_responses_route_is_not_prefixed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _gpt6_request(monkeypatch, "openai/responses/gpt-6-astra")
    assert request["model"] == "openai/responses/gpt-6-astra"


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.1",
        "openai/gpt-5.1",
        "gpt-4o",
        "claude-opus-5",
        "anthropic/claude-opus-4-5",
        "gpt-60",
        "gpt-6",  # not a family member litellm lists; never guess a route for it
        "azure/gpt-6-astra",  # routing it to `openai/` would send the call to a different provider
        "bedrock/openai.gpt-6-sol",
    ],
)
async def test_every_other_model_gets_the_request_it_always_got(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, table={model: {"supports_reasoning": True}})
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model=model, tools=[_TOOL], max_tokens=100
    )
    assert calls[0]["model"] == model
    assert "allowed_openai_params" not in calls[0] and calls[0]["max_tokens"] == 100


async def test_a_gpt6_model_litellm_has_never_heard_of_is_still_routed_and_keeps_its_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spine pins litellm to its bundled price table, and that table has no GPT-6 entry (measured: 2,982
    entries, `gpt-6-astra` absent). The route and the level are therefore keyed on the name, not the table."""
    calls: list[dict[str, Any]] = []
    _install_reasoning_litellm(monkeypatch, calls, table={})
    await LiteLLMClient().complete(
        [Message(role="user", content="hi")], model="gpt-6-astra", tools=[_TOOL], max_tokens=100
    )
    assert calls[0]["model"] == "openai/responses/gpt-6-astra"
    assert calls[0]["reasoning_effort"] == "high" and calls[0]["allowed_openai_params"] == [
        "reasoning_effort"
    ]


_CLI_LIKE_CALL = """
import asyncio, sys
import orchestrator.core.llm.catalog  # pins litellm's price table before it loads, as every CLI entry does
from orchestrator.core.llm.client import LLMError, Message, ToolSpec
from orchestrator.core.llm.litellm_client import LiteLLMClient

tool = ToolSpec(name="submit", description="d", parameters={"type": "object", "properties": {}})

async def main() -> None:
    try:
        await LiteLLMClient(num_retries=0).complete(
            [Message(role="user", content="hi")],
            model=sys.argv[1],
            tools=[tool],
            tool_choice="submit",
            max_tokens=100,
        )
    except LLMError as exc:
        print("LLMError:", str(exc).splitlines()[0][:160])

asyncio.run(main())
"""


@pytest.mark.parametrize("model", ["gpt-6-astra", "openai/gpt-6-sol"])
def test_the_gpt6_request_the_real_litellm_builds(model: str) -> None:
    """End to end through the real library, in a process set up the way the CLI is, to a local server.

    What the fakes above cannot show: the failures were litellm's own -- it refused `reasoning_effort` for
    these models, and under the pinned price table it did not recognise the bare name at all ("LLM Provider
    NOT provided"). Only the real library, imported after `catalog`, reproduces either. Nothing leaves the
    machine: the request goes to a server on 127.0.0.1 that records it and refuses it.
    """
    import subprocess

    seen: list[tuple[str, dict[str, Any]]] = []

    class _Record(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", 0))
            seen.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "recorded", "type": "invalid_request_error"}}')

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Record)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "OPENAI_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
        "OPENAI_API_KEY": "not-a-key",
    }
    env.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    env.pop("ORCHESTRATOR_REASONING_EFFORT", None)
    try:
        done = subprocess.run(
            [sys.executable, "-c", _CLI_LIKE_CALL, model],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        server.shutdown()
    assert seen, f"the request never left the process: {done.stdout!r} {done.stderr[-400:]!r}"
    path, body = seen[0]
    assert path == "/v1/responses"
    assert body["max_output_tokens"] == 100 and body["reasoning"] == {"effort": "high"}
    assert "max_tokens" not in body and body["tools"][0]["name"] == "submit"
