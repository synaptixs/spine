"""AgentLoop: dispatch, termination, caps — deterministic via scripted Mock."""

from __future__ import annotations

import pytest

from orchestrator.agentic import AgentLoop, Tool
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall, ToolSpec
from orchestrator.core.llm.budget import BudgetExceededError, RunBudget


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


def _echo_tool(calls: list[dict[str, object]]) -> Tool:
    async def _run(args: dict[str, object]) -> str:
        calls.append(args)
        return f"observed {args.get('q')}"

    spec = ToolSpec("echo", "echo a value", {"type": "object", "properties": {"q": {"type": "string"}}})
    return Tool(spec, _run)


async def test_loop_calls_tool_then_finalizes() -> None:
    seen: list[dict[str, object]] = []
    llm = MockLLMClient(
        script=[
            _result(tool_calls=(ToolCall("c1", "echo", {"q": "hi"}),)),
            _result(text="done"),
        ]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool(seen)], max_steps=5)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "final"
    assert out.final_text == "done"
    assert out.tool_calls_made == ["echo"]
    assert seen == [{"q": "hi"}]  # the tool actually ran with parsed args


async def test_loop_stops_at_max_steps() -> None:
    # Always asks for a tool, never finalizes → hits the cap.
    llm = MockLLMClient(
        script=[_result(tool_calls=(ToolCall(f"c{i}", "echo", {"q": i}),)) for i in range(10)]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool([])], max_steps=3)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "max_steps" and out.steps == 3


async def test_loop_detects_no_progress() -> None:
    # Same tool + identical args every step → no-progress termination.
    llm = MockLLMClient(
        script=[_result(tool_calls=(ToolCall(f"c{i}", "echo", {"q": "same"}),)) for i in range(10)]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool([])], max_steps=12, no_progress_repeats=3)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "no_progress" and out.steps == 3


async def test_unknown_tool_is_an_observation_not_a_crash() -> None:
    llm = MockLLMClient(
        script=[_result(tool_calls=(ToolCall("c1", "does_not_exist", {}),)), _result(text="ok")]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool([])], max_steps=5)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "final" and out.final_text == "ok"


async def test_tool_exception_is_fed_back_not_raised() -> None:
    async def _boom(_args: dict[str, object]) -> str:
        raise RuntimeError("kaboom")

    bad = Tool(ToolSpec("bad", "always fails", {"type": "object", "properties": {}}), _boom)
    llm = MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "bad", {}),)), _result(text="recovered")])
    loop = AgentLoop(llm, model="m", tools=[bad], max_steps=5)
    out = await loop.run("sys", "task")
    assert out.final_text == "recovered"


async def test_terminal_tool_ends_the_loop() -> None:
    from orchestrator.agentic.loop import Tool

    async def _finish(_args: dict[str, object]) -> str:
        return "submitted"

    done = Tool(ToolSpec("done", "finish", {"type": "object", "properties": {}}), _finish, terminal=True)
    # Asks for the terminal tool first; the second scripted result is never used.
    llm = MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "done", {}),)), _result(text="unused")])
    loop = AgentLoop(llm, model="m", tools=[done], max_steps=5)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "submitted"
    assert out.final_text == "submitted"
    assert out.steps == 1


async def test_require_terminal_nudges_a_prose_answer() -> None:
    from orchestrator.agentic.loop import Tool

    async def _finish(_args: dict[str, object]) -> str:
        return "submitted"

    done = Tool(ToolSpec("done", "finish", {"type": "object", "properties": {}}), _finish, terminal=True)
    # First the model answers in prose (no tool call); require_terminal nudges it,
    # and the second turn calls the terminal tool.
    llm = MockLLMClient(
        script=[_result(text="here is my answer in prose"), _result(tool_calls=(ToolCall("c", "done", {}),))]
    )
    loop = AgentLoop(llm, model="m", tools=[done], max_steps=5, require_terminal=True)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "submitted"


async def test_require_terminal_gives_up_after_max_nudges() -> None:
    from orchestrator.agentic.loop import Tool

    async def _finish(_args: dict[str, object]) -> str:
        return "ok"

    done = Tool(ToolSpec("done", "finish", {"type": "object", "properties": {}}), _finish, terminal=True)
    # Model keeps answering in prose; after max_nudges it returns final, not loops forever.
    llm = MockLLMClient(script=[_result(text=f"prose {i}") for i in range(6)])
    loop = AgentLoop(llm, model="m", tools=[done], max_steps=10, require_terminal=True, max_nudges=2)
    out = await loop.run("sys", "task")
    assert out.stopped_reason == "final"


async def test_budget_trip_pre_empts_a_step() -> None:
    budget = RunBudget(max_cost_usd=1.0)
    with budget.activate("run-x"):
        budget.charge(2.0)  # already over the cap
        llm = MockLLMClient(script=[_result(text="never reached")])
        loop = AgentLoop(llm, model="m", tools=[_echo_tool([])], max_steps=5, budget=budget)
        with pytest.raises(BudgetExceededError):
            await loop.run("sys", "task")
