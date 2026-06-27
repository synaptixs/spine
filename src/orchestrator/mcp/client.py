"""MCP client transport — discover + invoke tools on one server.

``MCPClient`` is the seam (so tests inject a fake); ``SessionMCPClient`` is the
real implementation over the official ``mcp`` SDK (the optional ``mcp`` extra,
lazy-imported). It opens a fresh transport+session per operation — simple and
stateless; a persistent session is an optimization for later if call volume
warrants it.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any, Protocol

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult


class MCPError(RuntimeError):
    """An MCP server could not be reached or a tool call failed."""


class MCPClient(Protocol):
    """Discover and invoke the tools of a single MCP server."""

    async def list_tools(self) -> list[MCPTool]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult: ...


class SessionMCPClient:
    """Real ``MCPClient`` over the ``mcp`` SDK (needs the ``mcp`` extra)."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config

    async def list_tools(self) -> list[MCPTool]:
        async with self._session() as session:
            result = await session.list_tools()
            return [self._to_tool(t) for t in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        async with self._session() as session:
            result = await session.call_tool(name, arguments)
            text = "".join(getattr(c, "text", "") or "" for c in (result.content or []))
            return MCPToolResult(text=text, is_error=bool(getattr(result, "isError", False)))

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        try:
            from mcp import ClientSession  # lazy: only when actually used
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise MCPError(
                "MCP support needs the 'mcp' extra — install with: pip install 'synaptixs-spine[mcp]'"
            ) from exc

        cfg = self._config
        try:
            if cfg.transport == "stdio":
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client

                params = StdioServerParameters(
                    command=cfg.command or "",
                    args=list(cfg.args),
                    env={**os.environ, **cfg.env},
                )
                async with (
                    stdio_client(params) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
            else:
                from mcp.client.streamable_http import streamablehttp_client

                async with (
                    streamablehttp_client(cfg.url or "", headers=cfg.headers or None) as (read, write, _),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any transport failure as MCPError
            raise MCPError(f"MCP server {cfg.name!r} ({cfg.transport}) failed: {exc}") from exc

    def _to_tool(self, raw: Any) -> MCPTool:
        annotations = getattr(raw, "annotations", None)
        read_only = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
        schema = getattr(raw, "inputSchema", None)
        return MCPTool(
            server=self._config.name,
            name=raw.name,
            description=getattr(raw, "description", "") or "",
            input_schema=dict(schema) if isinstance(schema, dict) else {},
            read_only=read_only,
        )


__all__ = ["MCPClient", "MCPError", "SessionMCPClient"]
