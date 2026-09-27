"""Pull a repository's external docs over MCP into the docs cache — ``orchestrator mcp ingest-docs``.

The network half of SSPN-80. Everything after the network — the cache format, the conversion to
``DocPage``, the binding — lives in :mod:`orchestrator.pkg.external_docs`, which never imports
this package; this module only asks servers for pages and hands them over.

**The tool guard (D22, D36).** A pull calls a tool only when it is allow-listed on the server
*and* the server declares it read-only (``readOnlyHint: true`` → ``MCPTool.read_only is True``).
Discovery runs first; any tool the pull needs that fails either test is refused by name, and
nothing is called. The rule is local to the pull on purpose: an operator's ``mcp.json``
allow-list routinely includes write tools (``jira_create_issue`` for intake), and a docs pull
that could reach one because a tool name was mistyped is a pull that can change a tracker.
Tightening :meth:`MCPRegistry.call` for every caller is a separate decision (ledger B43).

**What each source asks for.**

- *Confluence* (D16, D32): breadth-first from each root page — ``confluence_get_page`` per page
  and ``confluence_get_page_children`` (paged, 50 at a time) per parent — to ``max_depth``,
  at most ``max_docs`` pages. ``convert_to_markdown: true`` is always sent, so a page arrives
  as markdown and splits by heading like a local file; a server that answers in HTML anyway is
  flattened by the local HTML reader.
- *Jira* (D33, D34): ``jira_search`` paged 50 at a time to ``max_issues``, then
  ``jira_get_issue`` per key with ``comment_limit: 10`` and **always** ``update_history:
  false`` — mcp-atlassian defaults it to true, which would stamp every pulled issue into the
  operator's "recently viewed".

Every bound is reported honestly (invariant 7): ``N of M`` when the server said how many there
were, ``N of at least M (cap reached)`` when a walk stopped with pages still queued.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.mcp.models import MCPServerStatus, MCPTool
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.pkg.external_docs import (
    ExternalPage,
    collapse_stats,
    page_text,
    record_failure,
    source_cache_dir,
    utc_stamp,
    write_pull,
)
from orchestrator.pkg.repos import DocSource

#: The tools each kind of source calls — and so the tools the guard must clear before a pull.
TOOLS: dict[str, tuple[str, ...]] = {
    "confluence": ("confluence_get_page", "confluence_get_page_children"),
    "jira": ("jira_search", "jira_get_issue"),
}
#: The most mcp-atlassian returns per page of children or search results.
_PAGE_SIZE = 50
#: Comments read per Jira issue (D33).
_COMMENT_LIMIT = 10


class ToolGuardError(PermissionError):
    """A tool the pull needs is not allow-listed, or does not declare itself read-only."""


class PullError(RuntimeError):
    """A server answered with an error, or with something that is not a page."""


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _text_of(value: Any) -> str:
    """Prose from a string, or from an ADF-ish object's ``text`` leaves, in order."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return str(value["text"])
        return _text_of(value.get("content") or value.get("value") or "")
    if isinstance(value, list):
        return "\n".join(t for t in (_text_of(v) for v in value) if t)
    return ""


def vet_tools(source: DocSource, status: MCPServerStatus | None) -> dict[str, MCPTool]:
    """The tools ``source`` needs, each cleared by the guard — or :class:`ToolGuardError`
    naming every one that is not, before anything is called."""
    if status is None:
        raise ToolGuardError(f"source {source.name!r}: MCP server {source.server!r} is not configured")
    if not status.ok:
        raise PullError(f"MCP server {source.server!r} is unavailable: {status.error or status.kind}")
    offered = {t.name: t for t in status.tools}
    refused: list[str] = []
    for name in TOOLS[source.kind]:
        tool = offered.get(name)
        if tool is None:
            refused.append(f"{source.server}:{name} (not allow-listed or not offered)")
        elif tool.read_only is not True:
            refused.append(f"{source.server}:{name} (read_only={tool.read_only!r}, not declared read-only)")
    if refused:
        raise ToolGuardError(
            f"source {source.name!r}: refusing to pull — the docs pull calls only allow-listed tools "
            f"that declare readOnlyHint=true: {'; '.join(refused)}"
        )
    return {name: offered[name] for name in TOOLS[source.kind]}


@dataclass
class _Caller:
    """Every call a pull makes goes through here: only vetted tools, each call recorded."""

    registry: MCPRegistry
    server: str
    vetted: frozenset[str]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[Any, str]:
        if tool not in self.vetted:
            raise ToolGuardError(f"refusing {self.server}:{tool} — not cleared by the docs-pull tool guard")
        self.calls.append({"tool": tool, "args": dict(args)})
        result = await self.registry.call(f"{self.server}:{tool}", args)
        if result.is_error:
            raise PullError(
                f"{self.server}:{tool} failed ({json.dumps(args, sort_keys=True)}): {result.text[:300]}"
            )
        return _loads(result.text) if result.text.strip() else None, result.text


@dataclass
class Pulled:
    """One source's pull: its pages and the bound it hit."""

    pages: list[ExternalPage]
    cap: int
    total: int | None = None  # what the server said exists, when it said
    discovered: int = 0  # pages seen (fetched + still queued) when a walk stopped
    truncated: bool = False

    @property
    def bound(self) -> str:
        n = len(self.pages)
        if self.total is not None:
            return f"{n} of {self.total}" + (" (cap reached)" if n < self.total else "")
        if self.truncated:
            return f"{n} of at least {self.discovered} (cap reached)"
        return f"{n} of {n}"


def _confluence_page(page_id: str, data: Any, raw: str) -> ExternalPage:
    doc: dict[str, Any] = data if isinstance(data, dict) else {}
    nested = doc.get("metadata")
    meta: dict[str, Any] = nested if isinstance(nested, dict) else doc
    body: Any = None
    for holder in (meta, doc):
        for key in ("content", "body", "text"):
            candidate = holder.get(key)
            if isinstance(candidate, dict):
                candidate = candidate.get("value")
            if isinstance(candidate, str) and candidate.strip():
                body = candidate
                break
        if body is not None:
            break
    if body is None:
        body = raw if not isinstance(data, dict) else ""
    return ExternalPage(
        id=str(meta.get("id") or page_id),
        title=str(meta.get("title") or page_id),
        text=page_text(body),
        url=str(meta.get("url") or meta.get("link") or ""),
        kind="confluence",
    )


def _child_ids(data: Any) -> list[str]:
    items = (
        data if isinstance(data, list) else (data or {}).get("results") or (data or {}).get("children") or []
    )
    return [str(it["id"]) for it in items if isinstance(it, dict) and it.get("id")]


async def _pull_confluence(caller: _Caller, source: DocSource) -> Pulled:
    pages: list[ExternalPage] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(root, 0) for root in source.roots]
    queued = set(source.roots)
    truncated = False
    while queue:
        page_id, depth = queue.pop(0)
        if page_id in seen:
            continue
        if len(pages) >= source.max_docs:
            truncated = True
            break
        seen.add(page_id)
        data, raw = await caller.call(
            "confluence_get_page", {"page_id": page_id, "convert_to_markdown": True, "include_metadata": True}
        )
        pages.append(_confluence_page(page_id, data, raw))
        if depth >= source.max_depth:
            continue
        start = 0
        while True:
            data, _raw = await caller.call(
                "confluence_get_page_children",
                {
                    "parent_id": page_id,
                    "limit": _PAGE_SIZE,
                    "start": start,
                    "include_content": False,
                    "convert_to_markdown": True,
                },
            )
            ids = _child_ids(data)
            for child in ids:
                if child not in queued:
                    queued.add(child)
                    queue.append((child, depth + 1))
            if len(ids) < _PAGE_SIZE:
                break
            start += _PAGE_SIZE
    return Pulled(pages=pages, cap=source.max_docs, discovered=len(queued), truncated=truncated)


def _issue_text(key: str, data: Any, raw: str) -> tuple[str, str, str]:
    """``(title, text, url)`` — summary, description and up to ten comments."""
    if not isinstance(data, dict):
        return key, raw.strip(), ""
    nested = data.get("fields")
    fields: dict[str, Any] = nested if isinstance(nested, dict) else data
    summary = str(fields.get("summary") or data.get("summary") or key)
    parts = [f"{key}: {summary}"]
    description = _text_of(fields.get("description")).strip()
    if description:
        parts.append(description)
    comments = data.get("comments")
    if comments is None and isinstance(fields.get("comment"), dict):
        comments = fields["comment"].get("comments")
    for comment in (comments or [])[:_COMMENT_LIMIT]:
        body = _text_of(comment.get("body") if isinstance(comment, dict) else comment).strip()
        if body:
            parts.append(body)
    url = str(data.get("browse_url") or data.get("url") or "")
    return summary, "\n\n".join(parts), url


async def _pull_jira(caller: _Caller, source: DocSource) -> Pulled:
    keys: list[str] = []
    total: int | None = None
    token = ""
    while len(keys) < source.max_issues:
        want = min(_PAGE_SIZE, source.max_issues - len(keys))
        args: dict[str, Any] = {"jql": source.jql, "limit": want, "fields": "summary"}
        if token:
            args["page_token"] = token
        else:
            args["start_at"] = len(keys)
        data, _raw = await caller.call("jira_search", args)
        data = data if isinstance(data, dict) else {"issues": data if isinstance(data, list) else []}
        reported = data.get("total")
        if isinstance(reported, int) and not isinstance(reported, bool) and reported >= 0:
            total = reported
        batch = [str(i["key"]) for i in data.get("issues") or [] if isinstance(i, dict) and i.get("key")]
        fresh = [k for k in batch if k not in keys]
        keys.extend(fresh[: source.max_issues - len(keys)])
        token = str(data.get("next_page_token") or data.get("nextPageToken") or "")
        if not fresh or len(batch) < want or (total is not None and len(keys) >= total):
            break
    pages: list[ExternalPage] = []
    for key in keys:
        data, raw = await caller.call(
            "jira_get_issue",
            {
                "issue_key": key,
                "comment_limit": _COMMENT_LIMIT,
                "update_history": False,
                "fields": "summary,description,comment",
            },
        )
        title, text, url = _issue_text(key, data, raw)
        pages.append(ExternalPage(id=key, title=title, text=text, url=url, kind="jira"))
    truncated = len(keys) >= source.max_issues and (total is None or total > len(keys))
    return Pulled(pages=pages, cap=source.max_issues, total=total, discovered=len(keys), truncated=truncated)


def plan_of(source: DocSource) -> list[dict[str, Any]]:
    """What a pull of ``source`` would call — ``--dry-run``'s answer, no tool touched."""
    if source.kind == "confluence":
        return [
            {"tool": "confluence_get_page", "args": {"page_id": "<each page>", "convert_to_markdown": True}},
            {
                "tool": "confluence_get_page_children",
                "args": {
                    "parent_id": "<each page above max_depth>",
                    "limit": _PAGE_SIZE,
                    "convert_to_markdown": True,
                },
            },
        ]
    return [
        {"tool": "jira_search", "args": {"jql": source.jql, "limit": _PAGE_SIZE}},
        {
            "tool": "jira_get_issue",
            "args": {"issue_key": "<each result>", "comment_limit": _COMMENT_LIMIT, "update_history": False},
        },
    ]


async def ingest_docs(
    registry: MCPRegistry,
    repo_root: Path,
    repo_key: str,
    sources: Sequence[DocSource],
    *,
    dry_run: bool = False,
    cache_base: Path | None = None,
) -> list[dict[str, Any]]:
    """Pull every source in ``sources`` for ``repo_key`` — one summary per source.

    A source that fails — refused by the guard, a server error, an unreadable answer — keeps
    its last good pull and gets a ``failure.json`` beside it; the others still pull."""
    statuses = {s.name: s for s in await registry.probe()}
    out: list[dict[str, Any]] = []
    for source in sources:
        dest = source_cache_dir(repo_root, repo_key, source.name, base=cache_base)
        row: dict[str, Any] = {
            "source": source.name,
            "server": source.server,
            "kind": source.kind,
            "cap": source.cap,
            "cache": str(dest),
        }
        try:
            vetted = vet_tools(source, statuses.get(source.server))
        except (ToolGuardError, PullError) as exc:
            row.update(
                {"status": "refused" if isinstance(exc, ToolGuardError) else "failed", "error": str(exc)}
            )
            if not dry_run:
                record_failure(dest, str(exc))
            out.append(row)
            continue
        if dry_run:
            row.update({"status": "planned", "tools": plan_of(source)})
            out.append(row)
            continue
        caller = _Caller(registry, source.server, frozenset(vetted))
        try:
            pulled = await (
                _pull_confluence(caller, source)
                if source.kind == "confluence"
                else _pull_jira(caller, source)
            )
            manifest = {
                "source": source.name,
                "kind": source.kind,
                "server": source.server,
                # The SDK client does not surface the server's `serverInfo` through the registry.
                "server_version": None,
                "pulled_at": utc_stamp(),
                "tools_called": caller.calls,
                "counts": {
                    "pages": len(pulled.pages),
                    "cap": pulled.cap,
                    "total": pulled.total,
                    "discovered": pulled.discovered,
                    "truncated": pulled.truncated,
                    "bound": pulled.bound,
                },
            }
            written = write_pull(dest, pulled.pages, manifest)
        except (ToolGuardError, PullError, OSError, ValueError, KeyError, RuntimeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            record_failure(dest, reason)
            row.update({"status": "failed", "error": reason, "tools_called": len(caller.calls)})
            out.append(row)
            continue
        row.update(
            {
                "status": "ok",
                "pulled": len(pulled.pages),
                "bound": pulled.bound,
                "truncated": pulled.truncated,
                "pulled_at": written["pulled_at"],
                "tools_called": _tally(caller.calls),
                "collapse": collapse_stats(repo_root, pulled.pages, source.server),
            }
        )
        out.append(row)
    return out


def _tally(calls: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for call in calls:
        counts[call["tool"]] = counts.get(call["tool"], 0) + 1
    return dict(sorted(counts.items()))


__all__ = ["TOOLS", "PullError", "Pulled", "ToolGuardError", "ingest_docs", "plan_of", "vet_tools"]
