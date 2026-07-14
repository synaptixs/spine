"""MCP server configuration — the ``mcpServers`` file shape, plus an allow-list.

Adopts the de-facto ``mcpServers`` JSON shape that Claude Desktop / Claude Code
/ Codex already use, so a developer can point the orchestrator at the config
they already have. Transport is inferred: ``command`` → stdio, ``url`` → HTTP.
Our one addition is ``allow`` — a per-server allow-list of tool names; only
allow-listed tools are exposed/callable (``null``/absent = all tools, which the
registry warns about). Auth/secrets ride in ``env`` (stdio) or ``headers``
(http); never inline a raw secret you don't want in the file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_ENV = "ORCHESTRATOR_MCP_CONFIG"
DEFAULT_CONFIG_FILE = "mcp.json"


class MCPConfigError(ValueError):
    """The MCP config file is missing required shape."""


@dataclass(frozen=True)
class MCPServerConfig:
    """One onboarded MCP server. ``command`` ⇒ stdio; ``url`` ⇒ HTTP."""

    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    allow: tuple[str, ...] | None = None
    enabled: bool = True
    # Governance: mutating tools (not flagged read-only by the server) are
    # refused unless the operator opts the server in. Read tools are unaffected.
    write_enabled: bool = False

    @property
    def transport(self) -> str:
        if self.command:
            return "stdio"
        if self.url:
            return "http"
        raise MCPConfigError(f"server {self.name!r} has neither 'command' (stdio) nor 'url' (http)")

    def allows(self, tool_name: str) -> bool:
        """True when ``tool_name`` is exposed (allow-list, or all when unset)."""
        return self.allow is None or tool_name in self.allow


def load_mcp_configs(path: str | Path | None = None) -> list[MCPServerConfig]:
    """Load ``mcpServers`` from a JSON file. Empty list when the file is absent.

    Path precedence: explicit ``path`` > ``$ORCHESTRATOR_MCP_CONFIG`` > ``mcp.json``.
    """
    p = Path(path or os.getenv(DEFAULT_CONFIG_ENV) or DEFAULT_CONFIG_FILE)
    if not p.is_file():
        return []
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"{p}: invalid JSON ({exc})") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise MCPConfigError(f"{p}: expected an object with a top-level 'mcpServers' map")

    configs: list[MCPServerConfig] = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            raise MCPConfigError(f"{p}: server {name!r} must be an object")
        allow = raw.get("allow")
        configs.append(
            MCPServerConfig(
                name=str(name),
                command=raw.get("command"),
                args=tuple(str(a) for a in (raw.get("args") or [])),
                env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
                url=raw.get("url"),
                headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
                allow=tuple(str(a) for a in allow) if isinstance(allow, list) else None,
                enabled=bool(raw.get("enabled", True)),
                write_enabled=bool(raw.get("write_enabled", False)),
            )
        )
    return configs


def resolve_config_path(path: str | None = None) -> Path:
    """The mcp.json path to read/write. Precedence: explicit ``path`` >
    ``$ORCHESTRATOR_MCP_CONFIG`` > ``mcp.json`` in the cwd. ``~`` is expanded."""
    return Path(path or os.getenv(DEFAULT_CONFIG_ENV) or DEFAULT_CONFIG_FILE).expanduser()


def _read_config_doc(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"{p}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise MCPConfigError(f"{p}: expected a JSON object")
    return data


def upsert_mcp_server(path: str | None, name: str, spec: dict[str, Any]) -> Path:
    """Add or replace the ``mcpServers[name]`` entry in the config file, creating
    the file (and parents) if needed. Preserves other keys. Returns the path."""
    if not name.strip():
        raise MCPConfigError("server name is required")
    p = resolve_config_path(path)
    doc = _read_config_doc(p)
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[name] = {k: v for k, v in spec.items() if v is not None}
    doc["mcpServers"] = servers
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p


def remove_mcp_server(path: str | None, name: str) -> bool:
    """Remove ``mcpServers[name]``. Returns True if it existed."""
    p = resolve_config_path(path)
    doc = _read_config_doc(p)
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    doc["mcpServers"] = servers
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return True


__all__ = [
    "DEFAULT_CONFIG_ENV",
    "MCPConfigError",
    "MCPServerConfig",
    "load_mcp_configs",
    "remove_mcp_server",
    "resolve_config_path",
    "upsert_mcp_server",
]
