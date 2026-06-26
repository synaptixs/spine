"""Read-only in-loop tools (Phase 5a): query the PKG, read a file.

Thin adapters over existing seams — no new capability, just made callable
mid-task. All are read-only and sandboxed to the worktree root; write/test and
governed MCP tools arrive in 5b/5c.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator.agentic.loop import Tool
from orchestrator.core.llm import ToolSpec

_MAX_READ_BYTES = 60_000
_MAX_LISTED_FILES = 400
_STR = {"type": "string"}


def build_readonly_tools(root: Path | str) -> list[Tool]:
    """PKG-query + file-read tools scoped to ``root`` (extracts the PKG once)."""
    from orchestrator.pkg import FactStore, RepoCodeExtractor
    from orchestrator.pkg.retrieval import GroundedRetriever

    root_path = Path(root).resolve()
    store = FactStore(RepoCodeExtractor().extract(root_path))
    retriever = GroundedRetriever(store)

    async def _relevant_symbols(args: dict[str, object]) -> str:
        nodes = retriever.relevant_symbols(str(args.get("query", "")), limit=8)
        return _format_nodes(nodes) or "no relevant symbols found"

    async def _api_surface(args: dict[str, object]) -> str:
        nodes = retriever.api_surface(str(args.get("query", "")), limit=8)
        return _format_nodes(nodes) or "no api surface found"

    async def _callers_of(args: dict[str, object]) -> str:
        name = str(args.get("symbol", "")).strip()
        targets = [n for n in store.nodes if n.name == name and n.grounded]
        if not targets:
            return f"no grounded symbol named {name!r}"
        lines: list[str] = []
        for target in targets:
            for site in store.callers_of(target.id):
                lines.append(f"{site.caller.name} ({site.at}) calls {name}")
        return "\n".join(lines) or f"{name} has no recorded callers"

    async def _blast_radius(args: dict[str, object]) -> str:
        name = str(args.get("symbol", "")).strip()
        targets = [n for n in store.find(name) if n.grounded]
        if not targets:
            return f"no grounded symbol named {name!r}"
        blocks: list[str] = []
        for target in targets:
            impacted = store.impact_of(target.id)
            head = f"changing {target.name} ({target.provenance}) affects {len(impacted)} symbol(s)"
            if not impacted:
                blocks.append(head + " — nothing recorded calls it")
                continue
            body = "\n".join(f"  {n.name} ({n.provenance}) [depth {d}]" for n, d in impacted[:30])
            blocks.append(head + ":\n" + body)
        return "\n".join(blocks)

    async def _read_file(args: dict[str, object]) -> str:
        rel = str(args.get("path", "")).strip()
        target = (root_path / rel).resolve()
        if not _within(root_path, target):
            return f"error: refusing to read outside the worktree: {rel!r}"
        if not target.is_file():
            return f"error: no such file: {rel!r}"
        data = target.read_text(encoding="utf-8", errors="replace")
        return data[:_MAX_READ_BYTES] + ("\n…(truncated)" if len(data) > _MAX_READ_BYTES else "")

    async def _list_files(args: dict[str, object]) -> str:
        """The repo's files (relative paths) — how the agent discovers what to read."""
        subdir = str(args.get("subdir", "")).strip()
        base = (root_path / subdir).resolve() if subdir else root_path
        if not _within(root_path, base) or not base.is_dir():
            return f"error: not a directory under the repo: {subdir!r}"
        files = _walk_files(base, root_path)
        if not files:
            return "no files found"
        shown = files[:_MAX_LISTED_FILES]
        out = "\n".join(shown)
        if len(files) > _MAX_LISTED_FILES:
            out += f"\n…({len(files) - _MAX_LISTED_FILES} more — pass subdir to narrow)"
        return out

    return [
        Tool(
            ToolSpec(
                "list_files",
                "List the repository's files (relative paths) so you can decide what to read. "
                "Optional 'subdir' narrows the listing.",
                {"type": "object", "properties": {"subdir": _STR}},
            ),
            _list_files,
        ),
        Tool(
            ToolSpec(
                "pkg_relevant_symbols",
                "Find existing code symbols (types/functions) relevant to a query, with file:line.",
                {"type": "object", "properties": {"query": _STR}, "required": ["query"]},
            ),
            _relevant_symbols,
        ),
        Tool(
            ToolSpec(
                "pkg_api_surface",
                "List the repo's relevant public API surface for a query, with file:line.",
                {"type": "object", "properties": {"query": _STR}, "required": ["query"]},
            ),
            _api_surface,
        ),
        Tool(
            ToolSpec(
                "pkg_callers_of",
                "List the callers of a symbol by name, with file:line provenance.",
                {"type": "object", "properties": {"symbol": _STR}, "required": ["symbol"]},
            ),
            _callers_of,
        ),
        Tool(
            ToolSpec(
                "pkg_blast_radius",
                "What breaks if you change a symbol — its transitive callers (impact set) "
                "with file:line and hop distance. Use before modifying shared code.",
                {"type": "object", "properties": {"symbol": _STR}, "required": ["symbol"]},
            ),
            _blast_radius,
        ),
        Tool(
            ToolSpec(
                "read_file",
                "Read a file's contents by path relative to the worktree root.",
                {"type": "object", "properties": {"path": _STR}, "required": ["path"]},
            ),
            _read_file,
        ),
    ]


def _walk_files(base: Path, root: Path) -> list[str]:
    """Repo-relative file paths under ``base``, skipping vendored/hidden dirs."""
    from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            out.append((Path(dirpath) / name).resolve().relative_to(root).as_posix())
    return sorted(out)


def _format_nodes(nodes: list) -> str:  # type: ignore[type-arg]
    return "\n".join(f"{n.kind.value} {n.name} ({n.provenance})" for n in nodes if n.provenance)


def _within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["build_readonly_tools"]
