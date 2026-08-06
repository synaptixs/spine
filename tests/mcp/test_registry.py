"""MCPRegistry: discovery (namespaced, allow-listed) + call routing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _FakeClient:
    def __init__(
        self,
        server: str,
        tools: list[tuple[str, bool | None]],
        *,
        down: bool = False,
        schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._server = server
        self._tools = tools
        self._down = down
        self._schemas = schemas or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_count = 0  # so a test can prove the schema lookup was skipped

    async def list_tools(self) -> list[MCPTool]:
        self.list_count += 1
        if self._down:
            raise RuntimeError("server down")
        return [
            MCPTool(server=self._server, name=n, read_only=ro, input_schema=self._schemas.get(n, {}))
            for n, ro in self._tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append((name, arguments))
        return MCPToolResult(text=f"{self._server}:{name} ok")


def _registry(clients: dict[str, _FakeClient], configs: list[MCPServerConfig]) -> MCPRegistry:
    return MCPRegistry(configs, client_factory=lambda cfg: clients[cfg.name])


async def test_list_is_namespaced_and_allowlisted() -> None:
    clients = {
        "fs": _FakeClient("fs", [("read_file", True), ("delete_file", False)]),
        "pg": _FakeClient("pg", [("query", True)]),
    }
    configs = [
        MCPServerConfig(name="fs", command="x", allow=("read_file",)),  # delete_file filtered out
        MCPServerConfig(name="pg", url="http://x"),  # no allow-list → all
    ]
    tools = await _registry(clients, configs).list_tools()
    names = {t.qualified_name for t in tools}
    assert names == {"fs:read_file", "pg:query"}
    assert next(t for t in tools if t.name == "read_file").read_only is True


async def test_call_routes_to_the_right_server() -> None:
    clients = {"fs": _FakeClient("fs", [("read_file", True)]), "pg": _FakeClient("pg", [("query", True)])}
    configs = [
        MCPServerConfig(name="fs", command="x", allow=("read_file",)),
        MCPServerConfig(name="pg", url="http://x"),
    ]
    result = await _registry(clients, configs).call("pg:query", {"sql": "select 1"})
    assert result.text == "pg:query ok"
    assert clients["pg"].calls == [("query", {"sql": "select 1"})]
    assert clients["fs"].calls == []


async def test_unknown_server_raises_keyerror() -> None:
    reg = _registry({"fs": _FakeClient("fs", [])}, [MCPServerConfig(name="fs", command="x")])
    with pytest.raises(KeyError, match="unknown MCP server"):
        await reg.call("nope:tool", {})


async def test_non_allowlisted_tool_is_refused() -> None:
    clients = {"fs": _FakeClient("fs", [("read_file", True), ("delete_file", False)])}
    configs = [MCPServerConfig(name="fs", command="x", allow=("read_file",))]
    with pytest.raises(PermissionError, match="not allow-listed"):
        await _registry(clients, configs).call("fs:delete_file", {})


async def test_down_server_is_skipped_not_fatal() -> None:
    clients = {"up": _FakeClient("up", [("ok", True)]), "down": _FakeClient("down", [], down=True)}
    configs = [MCPServerConfig(name="up", command="x"), MCPServerConfig(name="down", command="y")]
    tools = await _registry(clients, configs).list_tools()
    assert {t.qualified_name for t in tools} == {"up:ok"}  # 'down' skipped, 'up' still listed


def test_disabled_server_is_excluded() -> None:
    reg = MCPRegistry([MCPServerConfig(name="off", command="x", enabled=False)])
    assert reg.server_names() == []


# --- A server that declares a structured argument as a JSON string (SSPN-14) ---------
#
# mcp-atlassian types `jira_create_issue.additional_fields` as `string`, not `object`.
# Every caller therefore had to json.dumps it first, which meant a governed create
# encoded twice: once for the field, once for the transport. The registry now honours
# the declared type, so callers pass the natural object.

_ATLASSIAN = {
    "jira_create_issue": {
        "properties": {
            "project_key": {"type": "string"},
            "additional_fields": {"type": "string"},
            "components": {"type": ["string", "null"]},
            "labels": {"type": "array"},
            "payload": {"type": "object"},
        }
    }
}


def _atlassian_registry() -> tuple[MCPRegistry, _FakeClient]:
    client = _FakeClient("atlassian", [("jira_create_issue", False)], schemas=_ATLASSIAN)
    configs = [MCPServerConfig(name="atlassian", command="x")]
    return _registry({"atlassian": client}, configs), client


async def test_object_argument_declared_as_string_is_encoded_once() -> None:
    registry, client = _atlassian_registry()
    await registry.call(
        "atlassian:jira_create_issue",
        {"project_key": "SSPN", "additional_fields": {"priority": {"name": "High"}}},
    )
    _, sent = client.calls[0]
    assert sent["additional_fields"] == '{"priority": {"name": "High"}}'
    # Encoded exactly once: it round-trips back to the object the caller passed.
    assert json.loads(sent["additional_fields"]) == {"priority": {"name": "High"}}
    assert sent["project_key"] == "SSPN", "a scalar argument is untouched"


async def test_a_caller_that_already_encoded_is_left_alone() -> None:
    """Both spellings work, so this is not a breaking change for existing callers."""
    registry, client = _atlassian_registry()
    await registry.call(
        "atlassian:jira_create_issue",
        {"project_key": "SSPN", "additional_fields": '{"priority": {"name": "High"}}'},
    )
    _, sent = client.calls[0]
    assert sent["additional_fields"] == '{"priority": {"name": "High"}}'


async def test_only_string_declared_arguments_are_encoded() -> None:
    """A declared array/object is passed through — the server asked for structure."""
    registry, client = _atlassian_registry()
    await registry.call(
        "atlassian:jira_create_issue",
        {"labels": ["a", "b"], "payload": {"k": "v"}, "components": {"nullable": True}},
    )
    _, sent = client.calls[0]
    assert sent["labels"] == ["a", "b"]
    assert sent["payload"] == {"k": "v"}
    # `string|null` is still a string type, so it is encoded.
    assert sent["components"] == '{"nullable": true}'


async def test_an_undeclared_argument_is_never_guessed_at() -> None:
    registry, client = _atlassian_registry()
    await registry.call("atlassian:jira_create_issue", {"mystery": {"k": "v"}})
    assert client.calls[0][1]["mystery"] == {"k": "v"}


async def test_all_scalar_call_skips_the_schema_round_trip() -> None:
    """Discovery costs a session; nothing structured means nothing to encode."""
    registry, client = _atlassian_registry()
    await registry.call("atlassian:jira_create_issue", {"project_key": "SSPN"})
    assert client.list_count == 0


async def test_a_failed_schema_lookup_still_makes_the_call() -> None:
    """The coercion is a convenience over the raw call, never a new way to fail."""
    client = _FakeClient("atlassian", [("jira_create_issue", False)], down=True)
    registry = _registry({"atlassian": client}, [MCPServerConfig(name="atlassian", command="x")])
    result = await registry.call("atlassian:jira_create_issue", {"additional_fields": {"k": "v"}})
    assert not result.is_error
    assert client.calls[0][1]["additional_fields"] == {"k": "v"}
