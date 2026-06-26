"""Tool gateway: invokes registered tool handlers against published ToolContracts.

This is the slim-version gateway: HTTP transport, transport-agnostic handler
interface, no MCP wire protocol yet. An MCP adapter (stdio JSON-RPC via the
`mcp` package) can be added later without changing handler code — handlers
only see ``InvocationContext`` and a typed inputs/outputs pair.
"""

from orchestrator.gateway.handlers import (
    HandlerRegistry,
    ToolHandler,
    get_default_registry,
    register_tool,
)
from orchestrator.gateway.invocation import (
    InvocationContext,
    InvocationOutcome,
    InvocationResult,
)

__all__ = [
    "HandlerRegistry",
    "InvocationContext",
    "InvocationOutcome",
    "InvocationResult",
    "ToolHandler",
    "get_default_registry",
    "register_tool",
]
