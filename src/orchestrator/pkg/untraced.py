"""The calls the graph could not bind, looked up per symbol — shared by the MCP tools and the CLI.

Moved here from ``plugin.server`` (SSPN-303): ``openspec options`` shows the same untraced calls
beside an option's caller floor as ``blast_radius`` shows beside a symbol's, and the CLI cannot
import the server (it needs the ``mcp`` extra). One lookup, so the two cannot disagree about what
"not tracked" or "not available" means. ``plugin.server`` still calls it ``_Untraced`` in annotations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.pkg import FactStore, Node
    from orchestrator.pkg.unbound import UnboundIndex

__all__ = ["MAX_UNTRACED_SITES", "UntracedLookup"]

#: Sites returned per symbol in the JSON. The count beside them is exact.
MAX_UNTRACED_SITES = 25


class UntracedLookup:
    """The calls the graph could not bind to a receiver type (B62), looked up per symbol.

    **A hint, never a caller.** These are attribute calls whose name matches the symbol's but whose
    receiver the graph could not type; the same name is also a ``str.find`` or a ``dict.get``
    somewhere else, so each site carries its receiver text and the answer carries how many
    functions share the name. It is reported beside ``callers`` and never added to ``caller_count``.

    A symbol where the question does not apply (a class, a module, an external name) gets nothing.
    A language whose front-end does not record refusals, or a list that could not be loaded, gets
    ``None`` and a note — never ``0``, because zero means "tracked, and none were found"."""

    def __init__(
        self, store: FactStore, indexes: dict[str, UnboundIndex] | None, unavailable: str | None
    ) -> None:
        """``indexes`` maps a repository key to its index; ``""`` is the one repository of a
        single-repo call (its node ids carry no scope). ``None`` means the list could not be loaded."""
        self._store = store
        self._indexes = indexes
        self._why = unavailable
        self._declared: dict[tuple[str, str], int] | None = None

    def _declared_count(self, repo: str, name: str) -> int:
        """How many functions named ``name`` this node's own repository declares."""
        if self._declared is None:
            from orchestrator.pkg.facts import NodeKind
            from orchestrator.pkg.scoping import unscope_id

            counts: dict[tuple[str, str], int] = {}
            for n in self._store.nodes:
                if n.grounded and n.kind is NodeKind.FUNCTION:
                    key = (unscope_id(n.id)[0], n.name)
                    counts[key] = counts.get(key, 0) + 1
            self._declared = counts
        return self._declared.get((repo, name), 0)

    def for_node(self, node: Node) -> dict[str, Any]:
        from orchestrator.pkg.facts import NodeKind
        from orchestrator.pkg.unbound import TRACKED_LANGUAGES

        if node.kind is not NodeKind.FUNCTION or not node.grounded:
            return {}
        if node.language not in TRACKED_LANGUAGES:
            return {
                "unresolved_calls": None,
                "unresolved_calls_note": f"not tracked for {node.language or 'this language'}",
            }
        if self._indexes is None:
            return {"unresolved_calls": None, "unresolved_calls_note": f"not available ({self._why})"}
        from orchestrator.pkg.scoping import unscope_id

        repo = unscope_id(node.id)[0]  # "" for a single repository
        index = self._indexes.get(repo)
        entry = index.entry(node.name) if index is not None else None
        sites = entry.sites[:MAX_UNTRACED_SITES] if entry is not None else ()
        return {
            "unresolved_calls": {
                "count": entry.count if entry is not None else 0,
                "shown": len(sites),
                "declared": self._declared_count(repo, node.name),
                "sites": [
                    {"caller": c.caller, "at": f"{c.rel}:{c.line}", "receiver": c.receiver} for c in sites
                ],
            }
        }
