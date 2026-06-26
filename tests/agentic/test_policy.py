"""Policy-as-code (Bet 2a): decisions + loop enforcement."""

from __future__ import annotations

from orchestrator.agentic import AgentLoop, Policy, PolicyAction, Tool
from orchestrator.agentic.policy import Decision
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall, ToolSpec


def _result(*, tool_calls: tuple[ToolCall, ...] = (), text: str = "") -> CompletionResult:
    return CompletionResult(
        text=text,
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=tool_calls,
    )


# ---- Policy.decide -----------------------------------------------------------


def test_default_allows_unlisted_tools() -> None:
    assert Policy().decide("read_file", {}).allowed


def test_explicit_deny() -> None:
    p = Policy.from_dict({"tools": {"write_files": "deny"}})
    d = p.decide("write_files", {"files": [{"path": "x.py"}]})
    assert d.action is PolicyAction.DENY


def test_default_deny_then_allowlist() -> None:
    p = Policy.from_dict({"default": "deny", "tools": {"read_file": "allow"}})
    assert p.decide("read_file", {}).allowed
    assert p.decide("write_files", {}).action is PolicyAction.DENY


def test_path_scoping_allows_in_scope_writes() -> None:
    p = Policy.from_dict(
        {"tools": {"write_files": {"allow": True, "paths": ["src/**"], "else": "require_approval"}}}
    )
    assert p.decide("write_files", {"files": [{"path": "src/a.py"}]}).allowed
    out = p.decide("write_files", {"files": [{"path": "/etc/passwd"}]})
    assert out.action is PolicyAction.REQUIRE_APPROVAL


def test_glob_matches_mcp_tools() -> None:
    p = Policy.from_dict({"default": "allow", "tools": {"mcp__db__*": "deny"}})
    assert p.decide("mcp__db__insert", {}).action is PolicyAction.DENY
    assert p.decide("mcp__files__read", {}).allowed


# ---- loop enforcement --------------------------------------------------------


def _echo_tool(calls: list[dict[str, object]], *, name: str = "write_files") -> Tool:
    async def _run(args: dict[str, object]) -> str:
        calls.append(args)
        return "ran"

    return Tool(ToolSpec(name, "t", {"type": "object", "properties": {}}), _run)


async def test_loop_blocks_denied_tool_and_records_it() -> None:
    ran: list[dict[str, object]] = []
    policy = Policy.from_dict({"tools": {"write_files": "deny"}})
    llm = MockLLMClient(
        script=[_result(tool_calls=(ToolCall("c", "write_files", {"files": []}),)), _result(text="ok")]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool(ran)], max_steps=5, policy=policy)
    out = await loop.run("sys", "task")
    assert ran == []  # the denied tool never ran
    assert out.policy_blocks == [
        {"tool": "write_files", "action": "deny", "reason": "rule for 'write_files'"}
    ]
    assert out.stopped_reason == "final"  # model adapted to the refusal


async def test_loop_allows_permitted_tool() -> None:
    ran: list[dict[str, object]] = []
    policy = Policy.from_dict({"tools": {"write_files": "allow"}})
    llm = MockLLMClient(
        script=[_result(tool_calls=(ToolCall("c", "write_files", {"files": []}),)), _result(text="done")]
    )
    loop = AgentLoop(llm, model="m", tools=[_echo_tool(ran)], max_steps=5, policy=policy)
    out = await loop.run("sys", "task")
    assert ran == [{"files": []}] and out.policy_blocks == []


def test_decision_helper() -> None:
    assert Decision(PolicyAction.ALLOW, "ok").allowed
    assert not Decision(PolicyAction.DENY, "no").allowed
