"""Requirements sources backed by an onboarded MCP server (C10).

Instead of direct REST creds, read a source through an MCP server the operator
already runs (e.g. ``mcp-atlassian``, which exposes *both* Confluence and Jira
tools). One generic ``MCPSourceAdapter`` maps the ``SourceAdapter`` seam's two
operations — fetch-document / list-children — onto whatever tools a given server
exposes; ``MCPSourceConfig`` names those tools. Presets cover the common cases:

* ``mcp-confluence`` — pages via ``confluence_get_page`` / ``_get_page_children``.
* ``mcp-jira`` — issues via ``jira_get_issue``; children via ``jira_search`` with
  a ``parent = <key>`` query.
* ``mcp`` — the generic escape hatch: point at *any* onboarded server by setting
  ``MCP_SOURCE_*`` (server + tool/arg names). Server *onboarding* is already
  handled by ``MCPRegistry.from_config()`` (the ``mcpServers`` config); this only
  adds the tool-mapping layer.

Result parsing is deliberately **lenient and unified**: it reads Confluence page
shapes, Jira ``fields.summary`` / ADF ``description``, and falls back to the raw
tool text when a server returns something else — so presets are exact and the
generic path degrades honestly rather than failing. Richer per-server field
mapping (JSON-path extraction) is intentionally deferred until a real
non-Atlassian server needs it.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from orchestrator.intake.jira_source import (
    _MAX_ATTACHMENT_BYTES,
    _MAX_COMMENTS,
    _AttachmentTooLargeError,
    _bound_attachments,
    _description_text,
    _UnreadableError,
    issue_meta_header,
    issue_type_of,
    project_key_of,
    read_attachments_in_full,
    render_issue_bodies,
)
from orchestrator.intake.source import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOCS,
    FetchTreeResult,
    SourceDocument,
    SourceRef,
)
from orchestrator.mcp.client import MCPError
from orchestrator.mcp.registry import MCPRegistry

if TYPE_CHECKING:
    from orchestrator.intake.confluence_links import LinkedPages


@dataclass(frozen=True)
class MCPSourceConfig:
    """Which onboarded MCP server + tools back a source. Defaults = Confluence
    (so a bare ``MCPSourceConfig()`` stays backward-compatible)."""

    source_kind: str = "mcp-confluence"
    server: str = "confluence"
    doc_tool: str = "confluence_get_page"
    doc_arg: str = "page_id"
    children_tool: str = "confluence_get_page_children"
    children_arg: str = "parent_id"
    #: How to build the children-tool argument from a parent id. Confluence passes
    #: the id straight through (``{id}``); Jira searches for ``parent = {id}``.
    children_query: str = "{id}"
    #: Jira only: the tool that returns an issue's attachment *bytes* (mcp-atlassian's
    #: ``jira_download_attachments``, base64). Empty — or a server without it — leaves every
    #: attachment named with why, as the REST path does for a download it cannot make.
    attachments_tool: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.server and self.doc_tool)

    @classmethod
    def for_confluence(cls) -> MCPSourceConfig:
        return cls(
            source_kind="mcp-confluence",
            server=os.getenv("MCP_CONFLUENCE_SERVER", "confluence"),
            doc_tool=os.getenv("MCP_CONFLUENCE_PAGE_TOOL", "confluence_get_page"),
            doc_arg=os.getenv("MCP_CONFLUENCE_PAGE_ARG", "page_id"),
            children_tool=os.getenv("MCP_CONFLUENCE_CHILDREN_TOOL", "confluence_get_page_children"),
            children_arg=os.getenv("MCP_CONFLUENCE_CHILDREN_ARG", "parent_id"),
            children_query="{id}",
        )

    @classmethod
    def for_jira(cls) -> MCPSourceConfig:
        return cls(
            source_kind="mcp-jira",
            server=os.getenv("MCP_JIRA_SERVER", "jira"),
            doc_tool=os.getenv("MCP_JIRA_ISSUE_TOOL", "jira_get_issue"),
            doc_arg=os.getenv("MCP_JIRA_ISSUE_ARG", "issue_key"),
            children_tool=os.getenv("MCP_JIRA_SEARCH_TOOL", "jira_search"),
            children_arg=os.getenv("MCP_JIRA_SEARCH_ARG", "jql"),
            children_query=os.getenv("MCP_JIRA_CHILDREN_QUERY", "parent = {id}"),
            attachments_tool=os.getenv("MCP_JIRA_ATTACHMENTS_TOOL", "jira_download_attachments"),
        )

    @classmethod
    def from_env(cls) -> MCPSourceConfig:
        """Generic escape hatch — any onboarded server, configured via ``MCP_SOURCE_*``."""
        return cls(
            source_kind="mcp",
            server=os.getenv("MCP_SOURCE_SERVER", ""),
            doc_tool=os.getenv("MCP_SOURCE_DOC_TOOL", ""),
            doc_arg=os.getenv("MCP_SOURCE_DOC_ARG", "id"),
            children_tool=os.getenv("MCP_SOURCE_CHILDREN_TOOL", ""),
            children_arg=os.getenv("MCP_SOURCE_CHILDREN_ARG", "id"),
            children_query=os.getenv("MCP_SOURCE_CHILDREN_QUERY", "{id}"),
        )


#: What a Jira issue is asked for over MCP — the same fields the REST adapter reads (mcp-atlassian
#: returns attachments and issue links only when they are named here; comments come by default).
_MCP_JIRA_FIELDS = "summary,description,issuetype,status,priority,labels,parent,comment,issuelinks,attachment"
#: Said at the end of the ticket when the server would not take that request and answered with
#: its defaults: the same `jira://KEY` then reads much thinner than over REST, and §8 must know.
# How a server says it does not take an argument — the only failure that means "ask again without
# the fields". Anything else (a 429, a timeout, a permission error) is a real failure and surfaces
# as one: retrying bare would read the ticket with the server's defaults and claim completeness.
_UNKNOWN_ARGUMENT = re.compile(
    r"(unknown|unexpected|unrecognized|invalid|extra)\b.{0,60}\b(argument|parameter|field|keyword|input)"
    r"|\b(fields|comment_limit)\b.{0,40}\b(not|unknown|unexpected|invalid)",
    re.IGNORECASE | re.DOTALL,
)
MCP_JIRA_FIELDS_REFUSED = (
    "_Read through an MCP server that did not accept a request for comments, issue links and "
    "attachments: the description only._"
)


def _json_objects(text: str) -> list[Any]:
    """Every JSON value in ``text`` — a multi-part tool result arrives as its parts, concatenated."""
    decoder, found, i = json.JSONDecoder(), [], 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        try:
            value, i = decoder.raw_decode(text, i)
        except ValueError:
            break
        found.append(value)
    return found


def _rest_fields(data: dict[str, Any]) -> dict[str, Any]:
    """An issue as mcp-atlassian returns it, in the REST field shape the shared renderer reads.

    mcp-atlassian flattens the issue and renames three parts (source @ ``0a5d242``, v0.23.0+53):
    ``comments`` (oldest first, the newest N kept, ``author.display_name``, bodies as text),
    ``issuelinks`` (``inward_issue`` / ``outward_issue``) and ``attachments`` (``filename``,
    ``size``, ``url`` — no ``id``). A server that already answers in the REST shape — ``fields``
    nested, REST names — passes through untouched.
    """
    page = data.get("page")
    inner: dict[str, Any] = page if isinstance(page, dict) else data
    nested = inner.get("fields")
    fields: dict[str, Any] = dict(nested) if isinstance(nested, dict) else dict(inner)

    comments = inner.get("comments")
    if isinstance(comments, list) and "comment" not in fields:
        rendered = []
        for c in comments:
            if not isinstance(c, dict):
                continue
            author = c.get("author") or {}
            name = author.get("display_name") or author.get("name") if isinstance(author, dict) else None
            rendered.append(
                {
                    "author": {"displayName": name or "unknown"},
                    "created": c.get("created"),
                    "body": c.get("body"),
                }
            )
        # Asked for one more than is shown, so an extra one means more exist — mcp-atlassian gives
        # no total, and "of 11" would be a count it never had.
        fields["comment"] = {"comments": rendered, "more_exist": len(rendered) > _MAX_COMMENTS}

    links = inner.get("issuelinks")
    if isinstance(links, list) and links and isinstance(links[0], dict) and "type" in links[0]:
        fields["issuelinks"] = [
            {
                "type": {k: (link.get("type") or {}).get(k) for k in ("inward", "outward")},
                "inwardIssue": link.get("inward_issue") or link.get("inwardIssue"),
                "outwardIssue": link.get("outward_issue") or link.get("outwardIssue"),
            }
            for link in links
            if isinstance(link, dict)
        ]

    attachments = inner.get("attachments")
    if isinstance(attachments, list) and "attachment" not in fields:
        # No `id` from mcp-atlassian, and the attachment key falls back to the filename — so give
        # each its own, or two revisions of one filename would collapse into one entry, the other
        # neither read nor named.
        kept = [a for a in attachments if isinstance(a, dict) and a.get("filename")]
        fields["attachment"] = [
            {
                "id": f"mcp:{i}",
                "filename": a.get("filename"),
                "size": a.get("size"),
                "content": a.get("url") or f"mcp:{a.get('filename')}",
            }
            for i, a in enumerate(kept)
        ]
    return fields


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_document(doc_id: str, data: Any, raw_text: str) -> SourceDocument:
    """Normalise an MCP tool result into a SourceDocument, leniently.

    Handles Confluence page shapes (``title`` + ``body``/``content``/``text``),
    Jira issue shapes (``fields.summary`` + ADF ``fields.description``), a common
    ``{"page": {...}}`` envelope, and — when the server returns something else —
    falls back to the raw tool text as the body.
    """
    if not isinstance(data, dict):
        return SourceDocument(id=doc_id, title=doc_id, body=raw_text.strip())

    inner = data.get("page")
    doc: dict[str, Any] = inner if isinstance(inner, dict) else data
    fields_raw = doc.get("fields")
    # Jira REST nests issue attributes under `fields`; mcp-atlassian returns them FLATTENED at
    # the top level and omits empty ones entirely. Fall back to the document itself as the
    # field source so both shapes read the same. Harmless for Confluence: a page has no
    # issue keys, so every issue-specific lookup below simply misses.
    fields: dict[str, Any] = fields_raw if isinstance(fields_raw, dict) else doc

    title = str(doc.get("title") or fields.get("summary") or doc.get("summary") or doc.get("key") or doc_id)

    body_raw = doc.get("body") or doc.get("content") or doc.get("text")
    if isinstance(body_raw, str):
        body = body_raw
    elif body_raw is not None:  # Confluence storage/ADF-ish object
        body = _description_text(body_raw)
    else:  # Jira: description lives under fields, often as ADF
        body = _description_text(fields.get("description"))
    body = body.strip()

    # Jira issues carry type/status/priority; the REST adapter prepends them to the body so the
    # extractor can tell a Bug from a Story and done from open. Do the same here, from the same
    # helper, or the identical epic ingested over MCP arrives untyped while the REST path types
    # it. Guarded on `fields` so Confluence pages (which have no such shape) are untouched.
    header = issue_meta_header(fields) if fields else ""

    # The raw-text fallback is for a payload we did not recognise AT ALL — not for one we
    # parsed cleanly that happens to have no description. mcp-atlassian omits empty fields
    # rather than nulling them, so a description-less issue took this branch and shipped the
    # whole JSON blob as the body; the extractor then read JSON instead of prose and coped
    # only because an LLM is forgiving. Recognising a title or a header means we understood
    # the document, and an empty body is then the truth.
    recognised = bool(header) or title != doc_id
    if not body and not recognised:
        body = raw_text.strip()

    if header:
        body = f"{header}\n\n{body}" if body else header

    labels = tuple(str(x) for x in (fields.get("labels") or doc.get("labels") or []))
    # Prefer `key` over `id`. Jira's `id` is an opaque number (36672) while `key` is the
    # human identifier (CB-676) that every other surface — the browse URL, JQL, the project
    # prefix — is built from. Preferring `id` left documents keyed by a number nobody
    # recognises, and made the project key underivable.
    resolved_id = str(doc.get("key") or doc.get("id") or doc_id)
    # `space` is the project key for Jira, matching the REST adapter.
    space = project_key_of(resolved_id) if header else ""
    # mcp-atlassian hands back the browse link, so the `url` the REST adapter builds from its
    # base URL is available here after all — no need to know the host.
    url = str(doc.get("browse_url") or doc.get("url") or "")
    return SourceDocument(
        id=resolved_id,
        title=title,
        body=body,
        labels=labels,
        space=space,
        url=url,
        # Same helper as the header above, and the same `fields` guard: an issue ingested over
        # MCP must select the same workflow profile as the identical issue over REST.
        issue_type=issue_type_of(fields) if fields else "",
    )


def _parse_children(data: Any) -> list[SourceRef]:
    """Children refs from a list/children/results/issues payload (id or key)."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("results") or data.get("children") or data.get("issues") or []
    else:
        items = []
    refs: list[SourceRef] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("id") or it.get("key")
        if not cid:
            continue
        it_fields_raw = it.get("fields")
        it_fields: dict[str, Any] = it_fields_raw if isinstance(it_fields_raw, dict) else {}
        title = str(it.get("title") or it_fields.get("summary") or "")
        refs.append(SourceRef(id=str(cid), title=title))
    return refs


class MCPSourceAdapter:
    """A requirements source read through an onboarded MCP server (any kind)."""

    def __init__(self, registry: MCPRegistry, config: MCPSourceConfig) -> None:
        self._registry = registry
        self._config = config
        self.source_kind = config.source_kind

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        cfg = self._config
        if cfg.source_kind == "mcp-jira":
            return await self._fetch_jira_issue(doc_id)
        result = await self._registry.call(f"{cfg.server}:{cfg.doc_tool}", {cfg.doc_arg: doc_id})
        return _parse_document(doc_id, _loads(result.text), result.text)

    async def _fetch_jira_issue(self, doc_id: str) -> SourceDocument:
        """A Jira issue read the way the REST adapter reads it — same fields, same rendering, same
        attachment reader — so one ticket reads the same over either transport (Track E, E3)."""
        cfg = self._config
        tool = f"{cfg.server}:{cfg.doc_tool}"
        notes: list[str] = []
        args = {cfg.doc_arg: doc_id, "fields": _MCP_JIRA_FIELDS, "comment_limit": _MAX_COMMENTS + 1}
        try:
            result = await self._registry.call(tool, args)
            failure = result.text if result.is_error else ""
        except MCPError as exc:
            failure = str(exc) or "error"
        refused = bool(failure) and bool(_UNKNOWN_ARGUMENT.search(failure))
        if failure and not refused:
            raise MCPError(f"{tool} failed for {doc_id}: {failure[:300]}")
        if refused:
            # A server that does not take mcp-atlassian's parameters still answers the bare call.
            result = await self._registry.call(tool, {cfg.doc_arg: doc_id})
            notes.append(MCP_JIRA_FIELDS_REFUSED)
        data = _loads(result.text)
        doc = _parse_document(doc_id, data, result.text)
        if not isinstance(data, dict):
            return doc  # a payload not recognised as an issue: the raw text, as before
        fields = _rest_fields(data)
        if refused:
            # The bare call answers with the server's defaults — comments cut at *its* limit, with
            # no total, links and attachments absent. Rendering those would claim a completeness
            # that was never asked for, so the ticket is what the note says: the description.
            for part in ("comment", "issuelinks", "attachment"):
                fields.pop(part, None)
        files, unavailable = (
            await self._jira_attachment_bytes(doc_id) if fields.get("attachment") else ({}, "")
        )

        names = [Path(str(a.get("filename") or "")).name for a in fields.get("attachment") or []]

        async def fetch(a: dict[str, Any]) -> bytes:
            if unavailable:
                raise _UnreadableError(unavailable)
            name = Path(str(a.get("filename") or "")).name
            if names.count(name) > 1:
                # mcp-atlassian gives attachments no id and downloads them by name, so two
                # revisions of `spec.md` cannot be told apart. Reading either could be the stale one.
                raise _UnreadableError(
                    "another attachment has the same name — the MCP server cannot tell them apart"
                )
            content = files.get(name)
            if content is None:
                raise _UnreadableError("the MCP server returned no content for it")
            if len(content) > _MAX_ATTACHMENT_BYTES:
                raise _AttachmentTooLargeError(str(a.get("filename")))
            return content

        full, not_read = await read_attachments_in_full(fields, fetch)
        read, unread = _bound_attachments(fields, full, not_read)
        body, full_body = render_issue_bodies(
            fields,
            attachment_texts=read,
            attachment_skips=unread,
            full_texts=full,
            full_skips=not_read,
            notes=tuple(notes),
        )
        return replace(doc, body=body, full_body=full_body)

    async def _jira_attachment_bytes(self, doc_id: str) -> tuple[dict[str, bytes], str]:
        """``filename → bytes`` from the server's attachment tool, or why there are none.

        mcp-atlassian's ``jira_download_attachments`` answers with a summary and one base64 JSON
        object per non-image file; images come as blobs, which carry no text and are named as
        images anyway. The text is extracted here, by the reader the REST path uses.
        """
        cfg = self._config
        if not cfg.attachments_tool:
            return {}, "the MCP server offers no attachment download"
        try:
            result = await self._registry.call(f"{cfg.server}:{cfg.attachments_tool}", {cfg.doc_arg: doc_id})
        except (MCPError, PermissionError, KeyError) as exc:
            return {}, f"the MCP server offers no attachment download ({type(exc).__name__})"
        if result.is_error:
            return {}, "the MCP server could not download it"
        files: dict[str, bytes] = {}
        for obj in _json_objects(result.text):
            if isinstance(obj, dict) and obj.get("encoding") == "base64" and obj.get("filename"):
                try:
                    files[Path(str(obj["filename"])).name] = base64.b64decode(
                        str(obj.get("content") or ""), validate=True
                    )
                except ValueError:
                    continue
        return files, ""

    async def linked_pages(self, doc_id: str) -> LinkedPages:
        """The Confluence pages a Jira issue links to, read over MCP — `--follow-links`.

        Remote links come raw (``include=remote_links``); the description comes flattened, which
        keeps smart-card URLs and pasted ones but drops the target of a text link, so a page linked
        only through link text is found over REST and not here. Comments carry theirs as text.
        """
        from orchestrator.intake.confluence import ConfluenceConfig
        from orchestrator.intake.confluence_links import LinkedPages, find_linked_pages

        cfg = self._config
        if cfg.source_kind != "mcp-jira":
            return LinkedPages()
        tool = f"{cfg.server}:{cfg.doc_tool}"
        try:
            result = await self._registry.call(
                tool, {cfg.doc_arg: doc_id, "fields": "description,comment", "include": "remote_links"}
            )
            if result.is_error:
                raise MCPError(result.text)
        except MCPError:
            result = await self._registry.call(tool, {cfg.doc_arg: doc_id})
        data = _loads(result.text)
        if not isinstance(data, dict):
            return LinkedPages()
        texts: list[tuple[str, Any]] = [("description", data.get("description"))]
        texts += [("comment", c.get("body")) for c in data.get("comments") or [] if isinstance(c, dict)]
        hosts = {urlparse(str(data.get("browse_url") or data.get("url") or "")).netloc}
        hosts.add(urlparse(ConfluenceConfig().base_url).netloc)
        remote = data.get("remote_links")
        return find_linked_pages(
            remote_links=remote if isinstance(remote, list) else [], texts=texts, site_hosts=hosts
        )

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        cfg = self._config
        if not cfg.children_tool:
            return []
        arg_value = cfg.children_query.format(id=doc_id)
        result = await self._registry.call(f"{cfg.server}:{cfg.children_tool}", {cfg.children_arg: arg_value})
        return _parse_children(_loads(result.text))

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult:
        documents: list[SourceDocument] = []
        truncated = False
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(root_id, 0)]
        while queue:
            doc_id, depth = queue.pop(0)
            if doc_id in seen:
                continue
            seen.add(doc_id)
            if len(documents) >= max_docs:
                truncated = True
                break
            documents.append(await self.fetch_document(doc_id))
            if depth < max_depth:
                for ref in await self.list_children(doc_id):
                    if ref.id not in seen:
                        queue.append((ref.id, depth + 1))
        return FetchTreeResult(documents=documents, truncated=truncated)


class MCPConfluenceAdapter(MCPSourceAdapter):
    """Back-compat alias — Confluence-over-MCP. Prefer ``MCPSourceAdapter`` + a preset."""

    def __init__(self, registry: MCPRegistry, config: MCPSourceConfig | None = None) -> None:
        super().__init__(registry, config or MCPSourceConfig.for_confluence())


__all__ = ["MCPConfluenceAdapter", "MCPSourceAdapter", "MCPSourceConfig"]
