"""In-loop human approval (Bet 2c-i): require_approval pauses, resume continues.

Deterministic — the LLM is a scripted Mock, no Temporal. A resumed loop is a
*fresh* AgentLoop fed the checkpoint (mirroring production, where resume runs in
a new activity invocation), so the script for the continuation lives on a new
MockLLMClient.
"""

from __future__ import annotations

import json

from orchestrator.agentic import (
    AgentLoop,
    HumanDecision,
    LoopCheckpoint,
    LoopResult,
    Policy,
    Tool,
)
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall, ToolSpec


def _checkpoint(result: LoopResult) -> LoopCheckpoint:
    """Assert the run paused and return its (non-None) checkpoint — also narrows
    the Optional for the type checker."""
    assert result.stopped_reason == "needs_approval"
    assert result.checkpoint is not None
    return result.checkpoint


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


def _tool(name: str, ran: list[dict[str, object]], *, terminal: bool = False) -> Tool:
    async def _run(args: dict[str, object]) -> str:
        ran.append({"_tool": name, **args})
        return f"{name} ran with {args}"

    return Tool(ToolSpec(name, name, {"type": "object", "properties": {}}), _run, terminal=terminal)


_GATE_DEPLOY = Policy.from_dict({"tools": {"deploy": "require_approval"}})


async def test_require_approval_pauses_with_checkpoint() -> None:
    ran: list[dict[str, object]] = []
    llm = MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "deploy", {"env": "prod"}),))])
    loop = AgentLoop(llm, model="m", tools=[_tool("deploy", ran)], max_steps=5, policy=_GATE_DEPLOY)

    out = await loop.run("sys", "task")

    assert out.stopped_reason == "needs_approval"
    assert ran == []  # the gated tool did NOT run
    assert out.pending is not None
    assert out.pending.tool == "deploy" and out.pending.arguments == {"env": "prod"}
    assert out.pending.call_id == "c1"
    assert out.checkpoint is not None
    # the gated tool's name is recorded as attempted (consistent with 2a blocks)
    assert out.tool_calls_made == ["deploy"]


async def test_resume_approve_runs_the_gated_call() -> None:
    ran: list[dict[str, object]] = []
    loop1 = AgentLoop(
        MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "deploy", {"env": "prod"}),))]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    paused = await loop1.run("sys", "task")

    # Fresh loop (new activity) + the continuation script: model finalizes.
    loop2 = AgentLoop(
        MockLLMClient(script=[_result(text="deployed, done")]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    out = await loop2.resume(_checkpoint(paused), HumanDecision(action="approve"))

    assert out.stopped_reason == "final" and out.final_text == "deployed, done"
    assert ran == [{"_tool": "deploy", "env": "prod"}]  # approved → it ran, with original args
    assert out.policy_blocks == []


async def test_resume_reject_feeds_denial_and_continues() -> None:
    ran: list[dict[str, object]] = []
    loop1 = AgentLoop(
        MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "deploy", {"env": "prod"}),))]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    paused = await loop1.run("sys", "task")

    loop2 = AgentLoop(
        MockLLMClient(script=[_result(text="understood, skipping deploy")]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    out = await loop2.resume(
        _checkpoint(paused), HumanDecision(action="reject", rationale="not in this window")
    )

    assert ran == []  # rejected → never ran
    assert out.stopped_reason == "final"
    assert out.policy_blocks == [{"tool": "deploy", "action": "rejected", "reason": "not in this window"}]


async def test_resume_modify_input_runs_with_patched_args() -> None:
    ran: list[dict[str, object]] = []
    loop1 = AgentLoop(
        MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "deploy", {"env": "prod"}),))]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    paused = await loop1.run("sys", "task")

    loop2 = AgentLoop(
        MockLLMClient(script=[_result(text="done")]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    out = await loop2.resume(
        _checkpoint(paused),
        HumanDecision(action="modify_input", modified_input={"env": "staging"}),
    )

    assert ran == [{"_tool": "deploy", "env": "staging"}]  # ran with the human's safer args
    assert out.stopped_reason == "final"


async def test_resume_approve_terminal_tool_submits() -> None:
    # A gated *terminal* tool, when approved, ends the loop as "submitted".
    ran: list[dict[str, object]] = []
    policy = Policy.from_dict({"tools": {"submit": "require_approval"}})
    loop1 = AgentLoop(
        MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "submit", {}),))]),
        model="m",
        tools=[_tool("submit", ran, terminal=True)],
        max_steps=5,
        policy=policy,
    )
    paused = await loop1.run("sys", "task")
    assert paused.stopped_reason == "needs_approval"

    loop2 = AgentLoop(
        MockLLMClient(script=[]),  # terminal tool ends the loop — no further completion
        model="m",
        tools=[_tool("submit", ran, terminal=True)],
        max_steps=5,
        policy=policy,
    )
    out = await loop2.resume(_checkpoint(paused), HumanDecision(action="approve"))
    assert out.stopped_reason == "submitted" and len(ran) == 1


async def test_remaining_sibling_calls_resume_after_the_gated_one() -> None:
    # One model turn emits write (allowed) + deploy (gated) + log (allowed).
    # write runs, deploy pauses; on approve, deploy then log run, all in order.
    ran: list[dict[str, object]] = []
    tools = [_tool("write", ran), _tool("deploy", ran), _tool("log", ran)]
    turn = _result(
        tool_calls=(
            ToolCall("w", "write", {"f": "a"}),
            ToolCall("d", "deploy", {"env": "prod"}),
            ToolCall("l", "log", {"m": "x"}),
        )
    )
    loop1 = AgentLoop(MockLLMClient(script=[turn]), model="m", tools=tools, max_steps=5, policy=_GATE_DEPLOY)
    paused = await loop1.run("sys", "task")
    assert [r["_tool"] for r in ran] == ["write"]  # only the pre-gate call ran
    assert paused.pending is not None and paused.pending.tool == "deploy"

    loop2 = AgentLoop(
        MockLLMClient(script=[_result(text="all done")]),
        model="m",
        tools=tools,
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    out = await loop2.resume(_checkpoint(paused), HumanDecision(action="approve"))
    assert [r["_tool"] for r in ran] == ["write", "deploy", "log"]  # siblings finished, in order
    assert out.stopped_reason == "final"


async def test_checkpoint_survives_json_serialization() -> None:
    ran: list[dict[str, object]] = []
    loop1 = AgentLoop(
        MockLLMClient(script=[_result(tool_calls=(ToolCall("c1", "deploy", {"env": "prod"}),))]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    paused = await loop1.run("sys", "task")

    # Serialize → JSON string → back (the activity → workflow → activity hop).
    wire = json.dumps(_checkpoint(paused).to_dict())
    restored = LoopCheckpoint.from_dict(json.loads(wire))

    loop2 = AgentLoop(
        MockLLMClient(script=[_result(text="done")]),
        model="m",
        tools=[_tool("deploy", ran)],
        max_steps=5,
        policy=_GATE_DEPLOY,
    )
    out = await loop2.resume(restored, HumanDecision(action="approve"))
    assert out.stopped_reason == "final"
    assert ran == [{"_tool": "deploy", "env": "prod"}]
