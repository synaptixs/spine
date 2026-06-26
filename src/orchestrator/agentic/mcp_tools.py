"""Governed MCP tools in the loop (Phase 5c).

Bridges onboarded, allow-listed MCP server tools into loop ``Tool``s so the
agent can call them mid-task — the consumer the Phase-4 onboarding was missing.
Governance is preserved: the registry enforces the allow-list, and a mutating
tool (``read_only`` is not true) is refused unless its server is
``write_enabled`` — the same rule ``MCPToolHandler`` applies on the gateway.
Every failure (allow-list, write-gate, transport) comes back as an observation.
"""

from __future__ import annotations

import re
from typing import Any

from orchestrator.agentic.loop import Tool
from orchestrator.core.llm import ToolSpec


def _safe_name(qualified: str) -> str:
    """``server:tool`` → a valid function name (``mcp__server__tool``)."""
    return "mcp__" + re.sub(r"[^a-zA-Z0-9_-]", "_", qualified.replace(":", "__"))


async def build_mcp_loop_tools(registry: Any, configs: list[Any]) -> list[Tool]:
    """Wrap each allow-listed MCP tool as a governed, write-gated loop tool.

    Duck-typed over ``orchestrator.mcp`` (an optional extra): ``registry`` needs
    ``list_tools()`` + ``call(qualified, args)``; each config needs ``name`` +
    ``write_enabled``.
    """
    write_by_server = {c.name: c.write_enabled for c in configs}
    discovered = await registry.list_tools()
    tools: list[Tool] = []
    for mt in discovered:
        tools.append(_loop_tool(registry, mt, write_enabled=bool(write_by_server.get(mt.server, False))))
    return tools


def _loop_tool(registry: Any, mt: Any, *, write_enabled: bool) -> Tool:
    qualified = mt.qualified_name
    read_only = mt.read_only is True
    gated = not read_only and not write_enabled

    async def _run(args: dict[str, object]) -> str:
        if gated:
            return (
                f"error: {qualified!r} is a mutating tool and its server is not "
                "write_enabled — it cannot be called"
            )
        try:
            result = await registry.call(qualified, dict(args))
        except (KeyError, PermissionError) as exc:
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 — transport errors are observations
            return f"error: {type(exc).__name__}: {exc}"
        prefix = "error: " if result.is_error else ""
        return prefix + (result.text or "")

    description = (mt.description or f"MCP tool {qualified}").strip()
    if gated:
        description += " (read-only access; mutating calls are disabled for this server)"
    schema = (
        mt.input_schema
        if isinstance(mt.input_schema, dict) and mt.input_schema
        else {
            "type": "object",
            "properties": {},
        }
    )
    return Tool(ToolSpec(_safe_name(qualified), description[:1024], schema), _run)


__all__ = ["build_mcp_loop_tools"]
