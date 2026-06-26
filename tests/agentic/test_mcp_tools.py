"""Governed MCP tools in the loop (Phase 5c) — via a fake registry."""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.agentic.mcp_tools import build_mcp_loop_tools


@dataclass
class _Tool:
    server: str
    name: str
    description: str = ""
    input_schema: dict[str, object] | None = None
    read_only: bool | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.server}:{self.name}"


@dataclass
class _Result:
    text: str
    is_error: bool = False


@dataclass
class _Cfg:
    name: str
    write_enabled: bool = False


class _FakeRegistry:
    def __init__(self, tools: list[_Tool]) -> None:
        self._tools = tools
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self) -> list[_Tool]:
        return self._tools

    async def call(self, qualified: str, args: dict[str, object]) -> _Result:
        self.calls.append((qualified, args))
        return _Result(text=f"ran {qualified}")


async def test_read_only_tool_is_callable_and_routes() -> None:
    reg = _FakeRegistry([_Tool("db", "query", "run a read query", {"type": "object"}, read_only=True)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db", write_enabled=False)])
    assert len(tools) == 1
    tool = tools[0]
    assert tool.spec.name == "mcp__db__query"  # ':' sanitized
    out = await tool.run({"sql": "select 1"})
    assert out == "ran db:query"
    assert reg.calls == [("db:query", {"sql": "select 1"})]


async def test_mutating_tool_is_write_gated() -> None:
    reg = _FakeRegistry([_Tool("db", "insert", read_only=False)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db", write_enabled=False)])
    out = await tools[0].run({"row": {}})
    assert "mutating tool" in out and "write_enabled" in out
    assert reg.calls == []  # never reached the server


async def test_mutating_tool_allowed_when_write_enabled() -> None:
    reg = _FakeRegistry([_Tool("db", "insert", read_only=False)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db", write_enabled=True)])
    out = await tools[0].run({"row": {}})
    assert out == "ran db:insert"


async def test_unknown_readonly_is_treated_as_mutating() -> None:
    # read_only=None (server didn't declare a hint) → conservative: gated.
    reg = _FakeRegistry([_Tool("db", "maybe", read_only=None)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db", write_enabled=False)])
    out = await tools[0].run({})
    assert out.startswith("error:")


async def test_registry_error_is_an_observation() -> None:
    class _Boom(_FakeRegistry):
        async def call(self, qualified: str, args: dict[str, object]) -> _Result:
            raise PermissionError("not allow-listed")

    reg = _Boom([_Tool("db", "query", read_only=True)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db")])
    out = await tools[0].run({})
    assert out.startswith("error:") and "allow-listed" in out


async def test_tool_result_error_is_prefixed() -> None:
    class _ErrReg(_FakeRegistry):
        async def call(self, qualified: str, args: dict[str, object]) -> _Result:
            return _Result(text="boom", is_error=True)

    reg = _ErrReg([_Tool("db", "query", read_only=True)])
    tools = await build_mcp_loop_tools(reg, [_Cfg("db")])
    out = await tools[0].run({})
    assert out.startswith("error: boom")
