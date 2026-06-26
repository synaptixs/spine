"""The additive tool-calling extension to the LLM seam."""

from __future__ import annotations

import json

from orchestrator.core.llm import CompletionResult, Message, ToolCall, ToolSpec
from orchestrator.core.llm.litellm_client import _extract_tool_calls


def test_plain_message_to_dict_unchanged() -> None:
    # Backward compatibility: a plain message serializes exactly as before.
    assert Message("user", "hi").to_dict() == {"role": "user", "content": "hi"}


def test_assistant_tool_call_message_shape() -> None:
    msg = Message("assistant", "", tool_calls=(ToolCall("c1", "echo", {"q": 1}),))
    out = msg.to_dict()
    assert out["tool_calls"][0]["id"] == "c1"
    assert out["tool_calls"][0]["function"]["name"] == "echo"
    # Arguments are wire-encoded as a JSON string (OpenAI shape).
    assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"q": 1}


def test_tool_result_message_carries_call_id() -> None:
    out = Message("tool", "observed", tool_call_id="c1").to_dict()
    assert out["role"] == "tool" and out["tool_call_id"] == "c1"


def test_toolspec_to_dict_is_openai_function_shape() -> None:
    spec = ToolSpec("echo", "echo it", {"type": "object", "properties": {}})
    d = spec.to_dict()
    assert d["type"] == "function" and d["function"]["name"] == "echo"


def test_completion_result_defaults_to_no_tool_calls() -> None:
    r = CompletionResult(
        text="x", model="m", prompt_tokens=0, completion_tokens=0, cost_usd=0.0, latency_ms=0.0
    )
    assert r.tool_calls == ()


def test_extract_tool_calls_from_dict_response() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "abc", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
                    ],
                }
            }
        ]
    }
    calls = _extract_tool_calls(response)
    assert len(calls) == 1
    assert calls[0].name == "read_file" and calls[0].arguments == {"path": "a.py"}


def test_extract_tool_calls_empty_when_none() -> None:
    assert _extract_tool_calls({"choices": [{"message": {"content": "just text"}}]}) == ()
