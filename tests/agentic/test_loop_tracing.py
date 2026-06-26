"""AgentLoop tracing (Phase 2): agent.step / tool.<name> spans + events.

All tests ``importorskip`` opentelemetry and drive an in-memory exporter via
``tracing.configure_for_testing``. With tracing off the loop behaves identically
(covered by test_loop.py); here we only assert the spans it emits when on.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from orchestrator.agentic import AgentLoop, Tool
from orchestrator.agentic.policy import Policy, PolicyAction, ToolRule
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall, ToolSpec
from orchestrator.obs import tracing


@pytest.fixture
def exporter() -> Iterator[object]:
    pytest.importorskip("opentelemetry")
    exp = tracing.configure_for_testing()
    try:
        yield exp
    finally:
        tracing.reset()


def _result(*, text: str = "", tool_calls: tuple[ToolCall, ...] = ()) -> CompletionResult:
    return CompletionResult(
        text=text,
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=tool_calls,
    )


def _echo_tool() -> Tool:
    async def _run(args: dict[str, object]) -> str:
        return f"observed {args.get('q')}"

    spec = ToolSpec("echo", "echo a value", {"type": "object", "properties": {"q": {"type": "string"}}})
    return Tool(spec, _run)


async def test_step_and_tool_spans_nest(exporter: object) -> None:
    llm = MockLLMClient(
        script=[
            _result(tool_calls=(ToolCall("c1", "echo", {"q": "hi"}),)),
            _result(text="done"),
        ]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool()], max_steps=5)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "final"

    tracing.flush()
    spans = exporter.get_finished_spans()  # type: ignore[attr-defined]
    by_name = [s.name for s in spans]
    # two model turns → two agent.step spans; one tool call → one tool.echo span
    assert by_name.count("agent.step") == 2
    assert "tool.echo" in by_name

    step_spans = {s.context.span_id: s for s in spans if s.name == "agent.step"}
    tool_span = next(s for s in spans if s.name == "tool.echo")
    # the tool span nests under an agent.step span
    assert tool_span.parent.span_id in step_spans
    assert tool_span.attributes["tool.name"] == "echo"
    assert tool_span.attributes["tool.terminal"] is False
    assert tool_span.attributes["tool.observation_len"] > 0
    # the final turn's step records the stop reason
    assert any(s.attributes.get("agent.stopped") == "final" for s in spans if s.name == "agent.step")


async def test_policy_block_recorded_as_span_event(exporter: object) -> None:
    llm = MockLLMClient(
        script=[
            _result(tool_calls=(ToolCall("c1", "echo", {"q": "x"}),)),
            _result(text="done"),
        ]
    )
    policy = Policy(default=PolicyAction.ALLOW, rules={"echo": ToolRule(action=PolicyAction.DENY)})
    loop = AgentLoop(llm, model="m", tools=[_echo_tool()], max_steps=5, policy=policy)
    out = await loop.run("sys", "task")
    assert any(b["tool"] == "echo" for b in out.policy_blocks)

    tracing.flush()
    spans = exporter.get_finished_spans()  # type: ignore[attr-defined]
    events = [(e.name, e.attributes.get("tool")) for s in spans for e in s.events]
    assert ("policy_block", "echo") in events
    # a denied call never dispatches → no tool.echo span
    assert not any(s.name == "tool.echo" for s in spans)


async def test_unknown_tool_span_marks_error(exporter: object) -> None:
    llm = MockLLMClient(
        script=[
            _result(tool_calls=(ToolCall("c1", "ghost", {}),)),
            _result(text="done"),
        ]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool()], max_steps=5)
    await loop.run("sys", "task")

    tracing.flush()
    spans = exporter.get_finished_spans()  # type: ignore[attr-defined]
    ghost = next(s for s in spans if s.name == "tool.ghost")
    assert ghost.attributes["tool.error"] == "unknown_tool"
