"""Onboard external MCP servers for the orchestrator to use (MCP client side).

Phase 1: discover and invoke the tools of configured MCP servers through one
registry, allow-listed and namespaced. This is the *client* side — the
orchestrator consuming MCP servers (DBs, Atlassian, …); distinct from exposing
the orchestrator itself as an MCP server.
"""

from orchestrator.mcp.client import MCPClient, MCPError, SessionMCPClient
from orchestrator.mcp.config import (
    MCPConfigError,
    MCPServerConfig,
    load_mcp_configs,
    remove_mcp_server,
    resolve_config_path,
    upsert_mcp_server,
)
from orchestrator.mcp.contract import contract_id_for, mcp_tool_to_contract
from orchestrator.mcp.handler import MCPRegisteredTool, MCPToolHandler, build_mcp_tools
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry

__all__ = [
    "MCPClient",
    "MCPConfigError",
    "MCPError",
    "MCPRegisteredTool",
    "MCPRegistry",
    "MCPServerConfig",
    "MCPTool",
    "MCPToolHandler",
    "MCPToolResult",
    "SessionMCPClient",
    "build_mcp_tools",
    "contract_id_for",
    "load_mcp_configs",
    "remove_mcp_server",
    "resolve_config_path",
    "upsert_mcp_server",
    "mcp_tool_to_contract",
]
