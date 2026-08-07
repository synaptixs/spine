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
            # Read the flag, don't `getattr(..., False)` it. v1 spelled this `isError`; a
            # defaulted lookup silently reported every tool error as a success when the name
            # changed, which is worse than the AttributeError it was written to avoid.
            return MCPToolResult(text=text, is_error=bool(result.is_error))

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
                # v2 takes a configured http client rather than a `headers` kwarg, and
                # yields two streams rather than three. `create_mcp_http_client` is the
                # SDK's own factory, so MCP's recommended timeouts still apply.
                from mcp.client.streamable_http import (  # type: ignore[attr-defined]
                    create_mcp_http_client,  # re-exported here, but absent from __all__
                    streamable_http_client,
                )

                http_client = create_mcp_http_client(headers=cfg.headers or None)
                async with (
                    streamable_http_client(cfg.url or "", http_client=http_client) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()
                    yield session
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any transport failure as MCPError
            raise MCPError(f"MCP server {cfg.name!r} ({cfg.transport}) failed: {exc}") from exc

    def _to_tool(self, raw: Any) -> MCPTool:
        # v2 renamed these to snake_case along with everything else. They were read with a
        # defaulted `getattr`, so the rename did not raise — `input_schema` silently became
        # None and every tool lost its argument types, which took the `mcp contracts` labels
        # with it and stopped `MCPRegistry.call` coercing a structured argument the server
        # declares as a string. A default that hides a rename is the same trap as
        # `getattr(result, "isError", False)`; both are gone.
        annotations = getattr(raw, "annotations", None)
        read_only = getattr(annotations, "read_only_hint", None) if annotations is not None else None
        schema = getattr(raw, "input_schema", None)
        return MCPTool(
            server=self._config.name,
            name=raw.name,
            description=getattr(raw, "description", "") or "",
            input_schema=dict(schema) if isinstance(schema, dict) else {},
            read_only=read_only,
        )


__all__ = ["MCPClient", "MCPError", "SessionMCPClient"]
