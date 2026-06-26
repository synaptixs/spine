"""The orchestrator exposed as an MCP server (plugin surface for Claude/Codex).

Distinct from ``orchestrator.mcp`` (the client that *consumes* MCP servers):
this is the server other hosts consume. See ``server.build_server``.
"""

from orchestrator.plugin.server import build_server

__all__ = ["build_server"]
