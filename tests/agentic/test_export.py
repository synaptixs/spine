"""Run export + replay (Bet 2b) — bundle structure + deterministic replay."""

from __future__ import annotations

from orchestrator.agentic import (
    AgentLoop,
    Policy,
    Tool,
    build_run_bundle,
    render_bundle_markdown,
    replay_llm_from_trace,
)
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall, ToolSpec


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


def _tool(ran: list[str], *, name: str = "look") -> Tool:
    async def _run(args: dict[str, object]) -> str:
        ran.append(name)
        return f"observed {args.get('q')}"

    return Tool(ToolSpec(name, "t", {"type": "object", "properties": {"q": {"type": "string"}}}), _run)


def _script() -> list[CompletionResult]:
    return [
        _result(text="investigating", tool_calls=(ToolCall("c1", "look", {"q": "x"}),)),
        _result(text="done"),
    ]


async def test_bundle_captures_trace_and_calls() -> None:
    loop = AgentLoop(MockLLMClient(script=_script()), model="m", tools=[_tool([])], max_steps=5)
    result = await loop.run("sys", "task")
    bundle = build_run_bundle(result, persona="tester", cost_usd=0.12, metadata={"k": "v"})
    assert bundle["persona"] == "tester" and bundle["cost_usd"] == 0.12
    assert bundle["tool_calls"] == ["look"]
    assert bundle["metadata"] == {"k": "v"}
    # the look call + its observation are in the trace
    calls = [c for s in bundle["trace"] for c in s["calls"]]
    assert calls[0]["name"] == "look" and "observed x" in calls[0]["observation"]
    assert calls[0]["blocked"] is False


async def test_bundle_records_policy_blocks() -> None:
    policy = Policy.from_dict({"tools": {"look": "deny"}})
    loop = AgentLoop(
        MockLLMClient(script=_script()), model="m", tools=[_tool([])], max_steps=5, policy=policy
    )
    bundle = build_run_bundle(await loop.run("s", "t"))
    assert bundle["policy_blocks"] and bundle["policy_blocks"][0]["tool"] == "look"
    md = render_bundle_markdown(bundle, title="Run X")
    assert "# Run X" in md and "Policy blocks" in md


async def test_replay_reproduces_the_tool_sequence() -> None:
    ran_live: list[str] = []
    live = AgentLoop(MockLLMClient(script=_script()), model="m", tools=[_tool(ran_live)], max_steps=5)
    bundle = build_run_bundle(await live.run("sys", "task"))

    # Rebuild an LLM from the recorded trace and re-run — same tools, no live model.
    ran_replay: list[str] = []
    replay_llm = replay_llm_from_trace(bundle)
    replayed = await AgentLoop(replay_llm, model="m", tools=[_tool(ran_replay)], max_steps=5).run(
        "sys", "task"
    )
    assert replayed.tool_calls_made == ["look"]
    assert ran_replay == ran_live == ["look"]
