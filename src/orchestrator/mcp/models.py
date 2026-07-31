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


@dataclass(frozen=True)
class MCPServerStatus:
    """What happened when we asked one server for its tools.

    Exists because "no tools" had exactly one presentation regardless of cause: a missing
    dependency, an unpulled image, a typo in ``command`` and a genuinely dead server all
    produced an empty list and a log line nobody sees. ``kind`` separates the two cases that
    call for different action — ``config`` is permanent and fixable by the operator, while
    ``unreachable`` may resolve on its own — and ``remedy`` carries the fix when we know it.
    """

    name: str
    kind: str = "ok"  # ok | config | unreachable
    tools: tuple[MCPTool, ...] = ()
    error: str = ""
    remedy: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


__all__ = ["MCPServerStatus", "MCPTool", "MCPToolResult"]
