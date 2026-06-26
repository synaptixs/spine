"""MCP server config loading (the mcpServers file shape + allow-list)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.mcp.config import MCPConfigError, MCPServerConfig, load_mcp_configs


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_loads_stdio_and_http_servers(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "mcpServers": {
                "fs": {"command": "npx", "args": ["-y", "server-fs", "/data"], "allow": ["read_file"]},
                "pg": {"url": "http://localhost:8080/mcp", "headers": {"Authorization": "Bearer x"}},
            }
        },
    )
    configs = {c.name: c for c in load_mcp_configs(p)}
    assert configs["fs"].transport == "stdio"
    assert configs["fs"].args == ("-y", "server-fs", "/data")
    assert configs["pg"].transport == "http"
    assert configs["pg"].headers == {"Authorization": "Bearer x"}


def test_allowlist_gates_tool_names() -> None:
    cfg = MCPServerConfig(name="fs", command="x", allow=("read_file", "list_dir"))
    assert cfg.allows("read_file") is True
    assert cfg.allows("delete_file") is False
    # No allow-list = everything is exposed (the registry warns).
    assert MCPServerConfig(name="open", command="x").allows("anything") is True


def test_missing_file_is_empty_not_error(tmp_path: Path) -> None:
    assert load_mcp_configs(tmp_path / "nope.json") == []


def test_bad_shape_raises(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    p.write_text('{"servers": {}}', encoding="utf-8")  # missing mcpServers
    with pytest.raises(MCPConfigError, match="mcpServers"):
        load_mcp_configs(p)


def test_server_without_command_or_url_raises_on_transport() -> None:
    with pytest.raises(MCPConfigError, match="neither"):
        _ = MCPServerConfig(name="bad").transport
