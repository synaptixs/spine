"""Value types for onboarded MCP tools (transport-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPTool:
    """A tool discovered on an onboarded MCP server.

    ``read_only`` mirrors the server's ``annotations.readOnlyHint`` when it
    declares one (``None`` = unknown) — surfaced so callers/governance can tell
    read tools from mutating ones.
    """

    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    read_only: bool | None = None

    @property
    def qualified_name(self) -> str:
        """``server:tool`` — the namespaced id callers use to invoke it."""
        return f"{self.server}:{self.name}"


@dataclass(frozen=True)
class MCPToolResult:
    """The flattened result of one MCP tool call (text content concatenated)."""

    text: str
    is_error: bool = False


__all__ = ["MCPTool", "MCPToolResult"]
