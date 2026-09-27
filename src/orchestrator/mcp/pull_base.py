"""What every docs pull shares — the call guard, its errors and the shape of a pull's result.

Split out of :mod:`orchestrator.mcp.doc_pull` so the RAG half (:mod:`~orchestrator.mcp.discovery`,
:mod:`~orchestrator.mcp.rag_pull`) can use them without importing ``doc_pull``, which dispatches
to that half: the two importing each other was an import cycle (CodeQL ``py/cyclic-import``).
``doc_pull`` re-exports every name here, so its callers are unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from orchestrator.mcp.models import MCPToolResult
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.pkg.external_docs import ExternalPage


class ToolGuardError(PermissionError):
    """A tool the pull needs is not allow-listed, or does not declare itself read-only."""


class PullError(RuntimeError):
    """A server answered with an error, or with something that is not a page."""


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


@dataclass
class _Caller:
    """Every call a pull makes goes through here: only vetted tools, each call recorded."""

    registry: MCPRegistry
    server: str
    vetted: frozenset[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[Any, str]:
        data, result = await self.call_result(tool, args)
        return data, result.text

    async def call_result(self, tool: str, args: dict[str, Any]) -> tuple[Any, MCPToolResult]:
        """``(parsed JSON text or None, the whole result)`` — for a caller that needs the text
        blocks apart or the structured content (a RAG answer). The same guard and the same
        failure detection as :meth:`call`, including an ``{"error": ...}`` structured body."""
        if tool not in self.vetted:
            raise ToolGuardError(f"refusing {self.server}:{tool} — not cleared by the docs-pull tool guard")
        self.calls.append({"tool": tool, "args": dict(args)})
        result = await self.registry.call(f"{self.server}:{tool}", args)
        if result.is_error:
            raise PullError(
                f"{self.server}:{tool} failed ({json.dumps(args, sort_keys=True)}): {result.text[:300]}"
            )
        data = _loads(result.text) if result.text.strip() else None
        reason = _error_body(data)
        if reason is None and data is None:
            reason = _error_body(result.structured)
        if reason is not None:
            raise PullError(
                f"{self.server}:{tool} failed ({json.dumps(args, sort_keys=True)}): {reason[:300]}"
            )
        return data, result


# Keys that make a JSON body an answer rather than a complaint — a page, a child listing, a search,
# an issue. A body carrying ``error`` and none of these is a failure.
_ANSWER_KEYS = frozenset({"metadata", "content", "results", "issues", "key", "id", "fields"})


def _error_body(data: Any) -> str | None:
    """The failure a tool reported *inside* an ordinary result, or ``None``.

    mcp-atlassian answers a failed call — an expired token, a missing page — with a normal result
    whose body is ``{"error": "..."}`` and ``is_error`` left False (its ``servers/confluence.py`` and
    ``servers/jira.py``). Read as data, that is a page with no title and no text: the pull
    "succeeds" and swaps an empty cache in over the last good one, which D21 exists to prevent.
    """
    if isinstance(data, dict) and "error" in data and not (_ANSWER_KEYS & data.keys()):
        return str(data["error"])
    return None


@dataclass
class Pulled:
    """One source's pull: its pages and the bound it hit."""

    pages: list[ExternalPage]
    cap: int
    total: int | None = None  # what the server said exists, when it said
    discovered: int = 0  # pages seen (fetched + still queued) when a walk stopped
    truncated: bool = False
    queried: int | None = None  # a query-driven rag pull: queries asked …
    candidates: int | None = None  # … of how many modules and classes could have been asked
    queries: dict[str, list[str]] | None = None  # query → chunk ids, for queries.json

    @property
    def bound(self) -> str:
        n = len(self.pages)
        if self.queried is not None:
            asked = f"queried {self.queried} of {self.candidates} symbols"
            if self.candidates is not None and self.queried < self.candidates:
                asked += " (cap reached)"
            return f"{asked} → {n} chunk(s)"
        if self.total is not None:
            return f"{n} of {self.total}" + (" (cap reached)" if n < self.total else "")
        if self.truncated:
            return f"{n} of at least {self.discovered} (cap reached)"
        return f"{n} of {n}"


__all__ = ["PullError", "Pulled", "ToolGuardError"]
