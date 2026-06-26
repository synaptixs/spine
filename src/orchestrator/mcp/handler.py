"""Bridge onboarded MCP tools into the gateway as governed ``ToolHandler``s.

``MCPToolHandler`` satisfies the gateway's ``ToolHandler`` protocol
(``contract_id``/``contract_version`` + async ``__call__``), so an MCP tool runs
through the gateway's rate-limit + audit + approval path like any other tool.
Governance applied here: a **write** tool (one the server didn't flag read-only)
is refused unless its server is ``write_enabled``, and every call emits a
structured audit log line.

``build_mcp_tools`` discovers the registry's tools and returns one
``(ToolContract, MCPToolHandler)`` pair each — ready to publish + register.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.contract import contract_id_for, mcp_tool_to_contract
from orchestrator.mcp.models import MCPTool
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.registry.tool_contract import ToolContract

logger = logging.getLogger("orchestrator.mcp.invoke")


class MCPToolHandler:
    """A gateway ``ToolHandler`` that invokes one MCP tool, write-gated."""

    def __init__(
        self,
        *,
        registry: MCPRegistry,
        tool: MCPTool,
        contract_version: str,
        write_enabled: bool,
    ) -> None:
        self.contract_id = contract_id_for(tool)
        self.contract_version = contract_version
        self.read_only = tool.read_only is True
        self.write_enabled = write_enabled
        self._registry = registry
        self._qualified = tool.qualified_name

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        if not self.read_only and not self.write_enabled:
            logger.warning(
                "mcp.invoke.write_blocked",
                extra={"tool": self._qualified, "actor": ctx.actor, "trace_id": ctx.trace_id},
            )
            raise PermissionError(
                f"MCP tool {self._qualified!r} is a write tool; its server is not write_enabled."
            )
        # The contract requires it, but it's the caller's key — the MCP server
        # neither needs nor understands it, so don't forward it.
        args = {k: v for k, v in inputs.items() if k != "idempotency_key"}
        result = await self._registry.call(self._qualified, args)
        logger.info(
            "mcp.invoke",
            extra={
                "tool": self._qualified,
                "actor": ctx.actor,
                "trace_id": ctx.trace_id,
                "is_error": result.is_error,
            },
        )
        # MCP tool calls carry no LLM/$ cost; report 0 for the gateway's field.
        return {"text": result.text, "is_error": result.is_error, "__cost_usd__": 0.0}


@dataclass(frozen=True)
class MCPRegisteredTool:
    """A discovered MCP tool, ready to publish + register on the gateway."""

    contract: ToolContract
    handler: MCPToolHandler


async def build_mcp_tools(
    registry: MCPRegistry, *, configs: list[MCPServerConfig], contract_version: str | None = None
) -> list[MCPRegisteredTool]:
    """Derive a ``(contract, handler)`` for every discovered, allow-listed tool.

    ``write_enabled`` is read per server from ``configs`` so the handler can
    gate mutating tools.
    """
    from orchestrator.mcp.contract import CONTRACT_VERSION

    version = contract_version or CONTRACT_VERSION
    write_by_server = {c.name: c.write_enabled for c in configs}
    built: list[MCPRegisteredTool] = []
    for tool in await registry.list_tools():
        contract = mcp_tool_to_contract(tool, version=version)
        handler = MCPToolHandler(
            registry=registry,
            tool=tool,
            contract_version=version,
            write_enabled=write_by_server.get(tool.server, False),
        )
        built.append(MCPRegisteredTool(contract=contract, handler=handler))
    return built


__all__ = ["MCPRegisteredTool", "MCPToolHandler", "build_mcp_tools"]
