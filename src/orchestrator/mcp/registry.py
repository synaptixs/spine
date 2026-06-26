"""Onboard configured MCP servers: discover their tools and route calls.

The registry is the orchestrator-facing surface for Phase 1: it loads the
``mcpServers`` config, discovers each server's tools (namespaced ``server:tool``,
filtered to the allow-list), and routes a call to the right server. A server
that's unreachable is skipped (logged) so one bad server doesn't blank the rest.

Read-mostly by design: only allow-listed tools are exposed or callable, so a
server can't surface a mutating tool the operator didn't opt into. (Wiring these
into the gateway ``ToolContract`` registry + budget/audit is Phase-1 Slice 2.)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.mcp.client import MCPClient, SessionMCPClient
from orchestrator.mcp.config import MCPServerConfig, load_mcp_configs
from orchestrator.mcp.models import MCPTool, MCPToolResult

logger = logging.getLogger("orchestrator.mcp")

ClientFactory = Callable[[MCPServerConfig], MCPClient]


def _default_factory(config: MCPServerConfig) -> MCPClient:
    return SessionMCPClient(config)


class MCPRegistry:
    """Aggregates the tools of every configured MCP server."""

    def __init__(
        self, configs: list[MCPServerConfig], *, client_factory: ClientFactory = _default_factory
    ) -> None:
        self._configs = {c.name: c for c in configs if c.enabled}
        self._factory = client_factory

    @classmethod
    def from_config(
        cls, path: str | Path | None = None, *, client_factory: ClientFactory = _default_factory
    ) -> MCPRegistry:
        return cls(load_mcp_configs(path), client_factory=client_factory)

    def server_names(self) -> list[str]:
        return list(self._configs)

    async def list_tools(self) -> list[MCPTool]:
        """Every allow-listed tool across all reachable servers (namespaced)."""
        out: list[MCPTool] = []
        for name, cfg in self._configs.items():
            if cfg.allow is None:
                logger.warning("mcp.no_allowlist", extra={"server": name})
            try:
                tools = await self._factory(cfg).list_tools()
            except Exception as exc:  # noqa: BLE001 — a down server must not blank the rest
                logger.warning("mcp.list_failed", extra={"server": name, "error": str(exc)[:200]})
                continue
            out.extend(t for t in tools if cfg.allows(t.name))
        return out

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Invoke ``server:tool`` — refuses unknown servers and non-allow-listed tools."""
        server, _, tool = qualified_name.partition(":")
        cfg = self._configs.get(server)
        if cfg is None:
            known = ", ".join(self._configs) or "none configured"
            raise KeyError(f"unknown MCP server {server!r} (known: {known})")
        if not tool or not cfg.allows(tool):
            raise PermissionError(f"tool {qualified_name!r} is not allow-listed on server {server!r}")
        return await self._factory(cfg).call_tool(tool, arguments)


__all__ = ["ClientFactory", "MCPRegistry"]
