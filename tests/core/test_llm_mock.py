from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.core.llm.client import prompt_fingerprint
from orchestrator.core.llm.mock import MissingFixtureError, record_fixture


def _msgs() -> list[Message]:
    return [
        Message(role="system", content="be terse"),
        Message(role="user", content="hello"),
    ]


async def test_inline_registration_round_trip() -> None:
    client = MockLLMClient()
    expected = CompletionResult(
        text="hi",
        model="claude-opus-4-7",
        prompt_tokens=2,
        completion_tokens=1,
        cost_usd=0.0,
        latency_ms=0.0,
    )
    client.register(messages=_msgs(), model="claude-opus-4-7", result=expected)
    actual = await client.complete(_msgs(), model="claude-opus-4-7")
    assert actual.text == "hi"
    assert client.calls == [("claude-opus-4-7", _msgs())]


async def test_missing_fixture_raises() -> None:
    client = MockLLMClient()
    with pytest.raises(MissingFixtureError, match="No fixture"):
        await client.complete(_msgs(), model="gpt-4o")


async def test_fingerprint_is_deterministic_and_order_sensitive() -> None:
    a = prompt_fingerprint(_msgs(), model="m")
    b = prompt_fingerprint(_msgs(), model="m")
    flipped = list(reversed(_msgs()))
    c = prompt_fingerprint(flipped, model="m")
    assert a == b
    assert a != c


async def test_fixture_file_round_trip(tmp_path: Path) -> None:
    client = MockLLMClient(fixture_root=tmp_path)
    msgs = _msgs()
    recorded = CompletionResult(
        text="recorded answer",
        model="claude-opus-4-7",
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.0001,
        latency_ms=0.0,
    )
    path = record_fixture(messages=msgs, model="claude-opus-4-7", result=recorded, root=tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["text"] == "recorded answer"

    replayed = await client.complete(msgs, model="claude-opus-4-7")
    assert replayed.text == "recorded answer"
    assert replayed.completion_tokens == 5
