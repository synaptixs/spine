"""Block B.1: Jira source adapter (the read side).

The counterpart to ``JiraAdapter`` (which *writes* specs into Jira): this reads
**existing** issues as requirements documents, so intake can be driven off a
real backlog — an epic, a project, a bug, a saved JQL — instead of only a wiki.
An issue's summary + description becomes a ``SourceDocument`` the intent
extractor reads; the ``jira://`` source kind flows through the same
``ingest`` / ``sdlc run`` paths as every other adapter.

Roots (``jira://<root>``):
  - ``jira://PROJ-123``    → the issue, then breadth-first its children
    (``parent = PROJ-123`` — subtasks and epic children).
  - ``jira://PROJ``        → the whole project (``project = PROJ ORDER BY created``).
  - ``jira://jql/<query>`` → an arbitrary JQL result set.

Reuses ``JiraConfig`` (the ``JIRA_`` env creds) and HTTP Basic auth against the
Cloud REST API v3. Descriptions come back as Atlassian Document Format (a JSON
document); ``_adf_to_text`` flattens it to readable prose — the reverse of the
write side's ``_text_to_adf``.
"""

from __future__ import annotations

import re
import tempfile
from collections import deque
from collections.abc import Collection
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from orchestrator.intake.jira import IssueTrackerError, JiraConfig
from orchestrator.intake.source import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOCS,
    FetchTreeResult,
    SourceDocument,
    SourceRef,
)

#: Issue key (``PROJ-123``) vs bare project key (``PROJ``) — decides how a root
#: is resolved: walk one issue's children, or run a project-wide search.
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*$")

#: The issue fields we pull — enough for the extractor without over-fetching.
#:
#: **The description is often not where the ticket is.** A real tracker entry reads
#: "send list of columns or enable filter on all columns" and the decision that settles it
#: — which columns, for which users — is three comments down or in the epic it blocks. A
#: spec derived from the description alone restates a summary; one that has read the thread
#: can carry the constraint. `comment` and `issuelinks` are the two fields that hold it;
#: `attachment` names every file and, on a single-issue fetch, carries the text of the ones the
#: doc readers can open (see :meth:`JiraSourceAdapter._attachment_texts`).
_FIELDS = "summary,description,issuetype,status,priority,labels,parent,comment,issuelinks,attachment"

#: Comments carried into the body, most recent first. Bounded because a long-running ticket
#: can hold hundreds and they would crowd out the description itself — the one part that is
#: certainly on topic. Invariant 7: the elision is stated, never silent.
_MAX_COMMENTS = 10

#: Per-comment characters. A pasted stack trace or a quoted email chain is one comment and
#: can be longer than every other comment combined.
_MAX_COMMENT_CHARS = 1200

#: Attachment *content* is fetched only for files the doc readers claim (``pkg.doc_source``:
#: markdown, text, PDF, HTML, docx, xlsx…), and only when an issue is fetched on its own —
#: the plan and feature path — never on a JQL scan of a hundred issues. An image is named,
#: not read: reading one needs OCR, and the repository keeps model-using extraction on its
#: own opt-in seam (``orchestrator media extract``) precisely so intake stays deterministic.
_MAX_ATTACHMENTS = 5
#: Jira reports ``size``, so an oversize file costs no request; the same cap is re-checked on
#: the bytes that arrive, because ``size`` is the tracker's claim and not a promise.
_MAX_ATTACHMENT_BYTES = 1_000_000  # `doc_source._MAX_DOC_BYTES` for text readers; a larger cap over-promised
#: Per-attachment characters. A 40-page PDF spec is one attachment and would otherwise be
#: the whole document; the cut is stated inline, as a comment's is.
_MAX_ATTACHMENT_CHARS = 8_000
#: All attachments together. Five at the per-file cut are 40,000 characters, which with ten
#: comments and a description passed ``intents._MAX_PROMPT_CHARS`` and cut the *tail* of the
#: document — the criteria, silently. This bound cuts the attachments instead, says so on the
#: attachment, and leaves the ticket's own words whole.
_MAX_ATTACHMENTS_TOTAL_CHARS = 20_000

_MULTI_BLANK_RE = re.compile(r"\n{3,}")


class _AttachmentTooLargeError(Exception):
    """The streamed body passed ``_MAX_ATTACHMENT_BYTES``; abandoned, and said so."""


class _OffHostError(Exception):
    """An attachment URL on a host other than the tracker's; credentials stay home."""


def _adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format node to readable text.

    Walks the ADF tree, emitting text leaves and a newline after each block
    (paragraph/heading/list item), so structure survives without HTML. Unknown
    node types fall through to concatenating their children — lossy-safe.
    """
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type")
    if ntype == "text":
        return str(node.get("text", ""))
    if ntype == "hardBreak":
        return "\n"
    inner = "".join(_adf_to_text(c) for c in (node.get("content") or []))
    if ntype == "listItem":
        return f"- {inner.strip()}\n"
    if ntype in ("paragraph", "heading", "codeBlock"):
        return f"{inner}\n"
    return inner


def _description_text(description: Any) -> str:
    """A Jira description is ADF (v3), occasionally plain text; normalise both."""
    if isinstance(description, str):
        return description.strip()
    if isinstance(description, dict):
        return _adf_to_text(description).strip()
    return ""


def issue_type_of(fields: dict[str, Any]) -> str:
    """``"Bug"`` from a Jira issue's fields, or ``""``.

    Accepts **both spellings of the type key**, because the two transports disagree: Jira REST
    returns ``issuetype``, while ``mcp-atlassian`` returns ``issue_type``. Reading only the
    REST spelling is how the meta header silently produced nothing at all over MCP — the tests
    mocked REST's shape, so they agreed with the code and both were wrong about the real
    server.

    One function so the *header* and the ``SourceDocument.issue_type`` *field* cannot disagree
    about what type an issue is. They feed different consumers — the header is prose for the
    intent extractor, the field is data for the deterministic pipeline — and a ticket whose
    two answers differed would be a defect nobody could see from either one alone.
    """
    type_obj = fields.get("issuetype") or fields.get("issue_type") or {}
    return str(type_obj.get("name") or "") if isinstance(type_obj, dict) else str(type_obj)


def issue_meta_header(fields: dict[str, Any]) -> str:
    """``"Bug · status: Open · priority: High"`` from a Jira issue's fields, or ``""``.

    Shared by the REST adapter and the MCP one so an issue reads the same whichever
    transport fetched it. This is how issue type, status and priority reach the *extractor* —
    prepended to the body, because a Bug genuinely reads differently from a Story and the
    model only sees prose.

    The type also travels as ``SourceDocument.issue_type``, which is what selects a workflow
    profile. This header cannot serve that: parsing a profile decision back out of a body
    string would make the pipeline depend on prose formatting.
    """
    itype = issue_type_of(fields)
    status = str((fields.get("status") or {}).get("name") or "")
    priority = str((fields.get("priority") or {}).get("name") or "")
    parts = (itype, f"status: {status}" if status else "", f"priority: {priority}" if priority else "")
    return " · ".join(p for p in parts if p)


def project_key_of(issue_key: str) -> str:
    """``"PROJ"`` from ``"PROJ-123"``, else ``""`` — the project an issue belongs to."""
    return issue_key.split("-")[0] if "-" in issue_key else ""


def _collapse(text: str) -> str:
    return _MULTI_BLANK_RE.sub("\n\n", text).strip()


def _comments_text(fields: dict[str, Any]) -> str:
    """Recent comments as prose, newest first, with what was left out stated.

    **Newest first** because a decision supersedes the discussion that produced it, and when
    the bound bites it is the early back-and-forth that is least worth keeping.

    Each comment is attributed. An unattributed thread reads as one voice, and "we agreed to
    do X" from the reporter and from an engineer are different kinds of claim.
    """
    block = fields.get("comment") or {}
    raw = block.get("comments") if isinstance(block, dict) else None
    if not isinstance(raw, list) or not raw:
        return ""

    ordered = list(reversed(raw))
    shown, lines = ordered[:_MAX_COMMENTS], []
    for entry in shown:
        if not isinstance(entry, dict):
            continue
        author = str((entry.get("author") or {}).get("displayName") or "unknown")
        when = str(entry.get("created") or "")[:10]
        text = _description_text(entry.get("body"))
        if len(text) > _MAX_COMMENT_CHARS:
            # Say it inline: a comment that stops mid-sentence with no marker reads as a
            # comment that ended there, which can invert its meaning.
            text = text[:_MAX_COMMENT_CHARS].rstrip() + f" …[truncated, {len(text)} chars]"
        if text:
            lines.append(f"- {author}{f' ({when})' if when else ''}: {text}")
    if not lines:
        return ""

    # `total` counts what Jira says exists, not what this page returned — a bound stated
    # against a partial denominator is a second, quieter version of the same lie.
    total = int(block.get("total") or len(raw)) if isinstance(block, dict) else len(raw)
    head = f"Comments ({len(lines)} of {total}, most recent first):"
    if total > len(lines):
        head = f"Comments ({len(lines)} most recent of {total}):"
    return "\n".join([head, *lines])


def _links_text(fields: dict[str, Any]) -> str:
    """Issue links and the parent/epic, as ``relation KEY — summary`` lines.

    The subtree walk already follows ``parent``; this is the *sideways* half — blocks,
    relates-to, duplicates — which a breadth-first walk over children never reaches, and
    which is where a constraint imposed by another team usually lives.
    """
    lines: list[str] = []
    parent = fields.get("parent")
    if isinstance(parent, dict) and parent.get("key"):
        summary = str((parent.get("fields") or {}).get("summary") or "")
        lines.append(f"- parent {parent['key']}{f' — {summary}' if summary else ''}")

    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        for side, verb_key in (("outwardIssue", "outward"), ("inwardIssue", "inward")):
            other = link.get(side)
            if not isinstance(other, dict) or not other.get("key"):
                continue
            verb = str(link_type.get(verb_key) or "relates to")
            summary = str((other.get("fields") or {}).get("summary") or "")
            lines.append(f"- {verb} {other['key']}{f' — {summary}' if summary else ''}")

    return "\n".join(["Linked issues:", *lines]) if lines else ""


def _attachment_key(a: dict[str, Any]) -> str:
    """Jira's attachment ``id``, or the filename when the record has none.

    Two revisions of ``spec.md`` on one ticket are two attachments — the everyday case, since a
    revised spec is re-uploaded, not edited — and keying on the filename read one and reported
    it as both.
    """
    return str(a.get("id") or Path(str(a.get("filename") or "")).name)


def _attachment_names(
    fields: dict[str, Any], *, exclude: Collection[str] = (), reasons: dict[str, str] | None = None
) -> str:
    """Attachment *filenames* for whatever was not read — never their contents, and each with why.

    Naming them costs nothing and tells a reader there is material Spine has not read; the
    reason tells them whether it ever could be: an image, a file over the cap, a download that
    failed, a text file that would not decode, the bound reached. Silence would be the worst
    option — it reads as "there was nothing attached". ``exclude`` holds the keys
    :meth:`JiraSourceAdapter._attachment_texts` did read.
    """
    reasons = reasons or {}
    names: list[str] = []
    for a in fields.get("attachment") or []:
        if not isinstance(a, dict) or not a.get("filename"):
            continue
        key = _attachment_key(a)
        if key in exclude:
            continue
        shown = Path(str(a["filename"])).name
        why = reasons.get(key)
        names.append(f"{shown} ({why})" if why else shown)
    if not names:
        return ""
    return "Attachments (names only — contents not read): " + ", ".join(names)


def _attachments_read_text(texts: dict[str, tuple[str, str]]) -> str:
    """The attachments whose text was extracted, each under its own filename."""
    if not texts:
        return ""
    used = sum(len(text) for _, text in texts.values())
    lines = [f"Attachments read ({len(texts)}, {used:,} of {_MAX_ATTACHMENTS_TOTAL_CHARS:,} chars):"]
    for name, text in texts.values():
        lines.append(f"--- {name} ---\n{text}")
    return "\n".join(lines)


class JiraSourceAdapter:
    """SourceAdapter over Jira Cloud v3 (read-only)."""

    source_kind = "jira"

    def __init__(self, config: JiraConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None

    def _issue_to_document(
        self,
        issue: dict[str, Any],
        *,
        attachment_texts: dict[str, tuple[str, str]] | None = None,
        attachment_skips: dict[str, str] | None = None,
    ) -> SourceDocument:
        key = str(issue.get("key", ""))
        fields = issue.get("fields") or {}
        summary = str(fields.get("summary") or "")
        labels = tuple(str(x) for x in (fields.get("labels") or []))
        texts = attachment_texts or {}
        # A short metadata header gives the extractor context — a Bug reads
        # differently from a Story, and status tells done from open.
        header = issue_meta_header(fields)
        # Order is deliberate and is the ticket's own order of authority: what it *is*, what
        # it says, what it is attached to, what was argued about it, what was attached and
        # read, and finally what exists but was not read. The description stays directly
        # under the header so a bounded comment thread can never displace the one section
        # that is certainly on topic.
        body = _collapse(
            "\n\n".join(
                p
                for p in (
                    header,
                    _description_text(fields.get("description")),
                    _links_text(fields),
                    _comments_text(fields),
                    _attachments_read_text(texts),
                    _attachment_names(fields, exclude=texts.keys(), reasons=attachment_skips),
                )
                if p
            )
        )
        url = f"{self._config.base_url.rstrip('/')}/browse/{key}" if key else ""
        project = project_key_of(key)
        return SourceDocument(
            id=key,
            title=summary,
            body=body,
            url=url,
            space=project,
            labels=labels,
            issue_type=issue_type_of(fields),
        )

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        data = await self._get(f"/issue/{doc_id}", params={"fields": _FIELDS})
        read, unread = await self._attachment_texts(data.get("fields") or {})
        return self._issue_to_document(data, attachment_texts=read, attachment_skips=unread)

    async def _attachment_texts(
        self, fields: dict[str, Any]
    ) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
        """``key → (filename, text)`` for the attachments that could be read, and ``key → reason``
        for every one that was not.

        Bounded four ways, each reason stated — at most ``_MAX_ATTACHMENTS``, none over
        ``_MAX_ATTACHMENT_BYTES`` (checked against Jira's ``size`` before any request and against
        the bytes as they stream in), each text cut at ``_MAX_ATTACHMENT_CHARS`` with the cut
        marked, and all of them together under ``_MAX_ATTACHMENTS_TOTAL_CHARS``, the one that
        crosses it cut to what is left and the rest named with why. Any failure — HTTP, a
        reader that yields nothing, an unreadable file, a record
        with a malformed ``size`` — leaves that attachment named with why. Never raises.

        The readers are ``pkg.doc_source``'s, run over a temporary directory: the same PDF,
        docx and markdown paths ``understand`` uses, with the same optional-extra behaviour.
        """
        from orchestrator.pkg.doc_source import is_doc_file, read_doc_pages
        from orchestrator.pkg.media import MEDIA_SUFFIXES

        read: dict[str, tuple[str, str]] = {}
        unread: dict[str, str] = {}
        used = 0
        for a in fields.get("attachment") or []:
            if not isinstance(a, dict):
                continue
            key = _attachment_key(a)
            name = Path(str(a.get("filename") or "")).name
            url = str(a.get("content") or "")
            if not name or not url:
                continue
            if Path(name).suffix.lower() in MEDIA_SUFFIXES:
                # `is_doc_file` claims images too, because `pkg.media` registers a reader for
                # them — one that reads a *committed* transcript artifact, which a downloaded
                # attachment can never have. Requesting the bytes would be guaranteed waste.
                unread[key] = "image, not read"
                continue
            if not is_doc_file(Path(name)):
                unread[key] = "no reader for this type"
                continue
            if len(read) >= _MAX_ATTACHMENTS:
                unread[key] = f"bound of {_MAX_ATTACHMENTS} reached"
                continue
            if used >= _MAX_ATTACHMENTS_TOTAL_CHARS:
                unread[key] = f"attachment budget of {_MAX_ATTACHMENTS_TOTAL_CHARS:,} chars reached"
                continue
            try:
                if int(a.get("size") or 0) > _MAX_ATTACHMENT_BYTES:
                    unread[key] = f"over the {_MAX_ATTACHMENT_BYTES // 1_000_000} MB cap"
                    continue
                data = await self._get_bytes(url)
                with tempfile.TemporaryDirectory() as tmp:
                    (Path(tmp) / name).write_bytes(data)
                    pages = read_doc_pages(tmp, sections=False)
            except _AttachmentTooLargeError:
                unread[key] = f"over the {_MAX_ATTACHMENT_BYTES // 1_000_000} MB cap"
                continue
            except _OffHostError:
                unread[key] = "not on the tracker's host"
                continue
            except (httpx.HTTPError, IssueTrackerError, OSError, ValueError, TypeError):
                unread[key] = "download failed"
                continue
            text = "\n\n".join(p.text for p in pages).strip()
            if not text:
                unread[key] = "no text could be read"
                continue
            remaining = _MAX_ATTACHMENTS_TOTAL_CHARS - used
            if len(text) > min(_MAX_ATTACHMENT_CHARS, remaining):
                why = (
                    f" — attachment budget of {_MAX_ATTACHMENTS_TOTAL_CHARS:,} chars reached"
                    if remaining < min(len(text), _MAX_ATTACHMENT_CHARS)
                    else ""
                )
                marker = f" …[truncated, {len(text)} chars{why}]"
                # The marker is part of what is carried, so it comes out of the same budget:
                # counting only the content let the header print more chars than it allows.
                keep = min(_MAX_ATTACHMENT_CHARS, remaining) - len(marker)
                if keep <= 0:
                    # Not even room for the sentence saying it was cut. Naming it costs
                    # nothing and keeps the header's arithmetic true.
                    unread[key] = f"attachment budget of {_MAX_ATTACHMENTS_TOTAL_CHARS:,} chars reached"
                    continue
                text = text[:keep].rstrip() + marker
            used += len(text)
            read[key] = (name, text)
        return read, unread

    async def _get_bytes(self, url: str) -> bytes:
        """An authenticated binary GET of an attachment's ``content`` link, streamed under the cap.

        Only the tracker's own host: the URL comes from Jira's JSON, and the Basic credentials
        must not follow it anywhere else. Jira answers with a redirect to a signed media URL, so
        redirects are followed (httpx drops ``Authorization`` on a cross-host hop). The body is
        read in chunks and abandoned the moment it passes ``_MAX_ATTACHMENT_BYTES`` — ``size``
        is the tracker's claim, and a check after a full download is a check too late.
        """
        if not self._read_ready():
            raise IssueTrackerError(
                "Jira not configured for reading (need JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)."
            )
        if urlparse(url).netloc.lower() != urlparse(self._config.base_url).netloc.lower():
            raise _OffHostError(url)
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            async with client.stream(
                "GET", url, auth=(self._config.email, self._config.api_token), follow_redirects=True
            ) as resp:
                if resp.status_code != httpx.codes.OK:
                    raise IssueTrackerError(f"GET {url} failed: HTTP {resp.status_code}")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _MAX_ATTACHMENT_BYTES:
                        raise _AttachmentTooLargeError(url)
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        return bytes(buf)

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        """Subtasks + epic children, via ``parent = <KEY>`` (both in modern Jira)."""
        data = await self._search(f"parent = {doc_id}", max_results=DEFAULT_MAX_DOCS)
        refs: list[SourceRef] = []
        for issue in data.get("issues") or []:
            key = str(issue.get("key", ""))
            if key:
                title = str((issue.get("fields") or {}).get("summary") or "")
                refs.append(SourceRef(id=key, title=title, kind="issue"))
        return refs

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult:
        """Resolve a ``jira://`` root: an issue subtree, a project, or a JQL set."""
        root = root_id.strip()
        if root.startswith("jql/"):
            return await self._search_tree(root[len("jql/") :], max_docs=max_docs)
        if _ISSUE_KEY_RE.match(root):
            return await self._issue_tree(root, max_depth=max_depth, max_docs=max_docs)
        if _PROJECT_KEY_RE.match(root):
            return await self._search_tree(f"project = {root} ORDER BY created", max_docs=max_docs)
        # Not a key shape — treat it as raw JQL rather than failing hard.
        return await self._search_tree(root, max_docs=max_docs)

    async def _issue_tree(self, root_key: str, *, max_depth: int, max_docs: int) -> FetchTreeResult:
        """Breadth-first walk from an issue, following ``parent`` children."""
        result = FetchTreeResult()
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(root_key, 0)])
        while queue:
            key, depth = queue.popleft()
            if key in seen:
                continue
            seen.add(key)
            if len(result.documents) >= max_docs:
                result.truncated = True
                break
            try:
                doc = await self.fetch_document(key)
            except IssueTrackerError:
                if depth == 0:
                    raise  # the root must be readable; a missing child is skipped
                continue
            result.documents.append(doc)
            if depth < max_depth:
                for child in await self.list_children(key):
                    if child.id not in seen:
                        queue.append((child.id, depth + 1))
        return result

    async def _search_tree(self, jql: str, *, max_docs: int) -> FetchTreeResult:
        """Run a JQL search; each matching issue is one flat document."""
        result = FetchTreeResult()
        data = await self._search(jql, max_results=max_docs)
        issues = data.get("issues") or []
        for issue in issues[:max_docs]:
            result.documents.append(self._issue_to_document(issue))
        # `/search/jql` returns no `total` — the old endpoint's count is gone. Truncation is
        # now "the server says there is more" (isLast false / a nextPageToken) or "we clipped
        # the page ourselves". Reading a missing `total` as 0 would have silently claimed
        # every result set was complete.
        more_upstream = data.get("isLast") is False or bool(data.get("nextPageToken"))
        result.truncated = more_upstream or len(issues) > len(result.documents)
        return result

    async def _search(self, jql: str, *, max_results: int) -> dict[str, Any]:
        """JQL search via ``/search/jql``.

        Atlassian **removed** ``GET /rest/api/3/search`` (CHANGE-2046); it now answers 410
        Gone on Jira Cloud, which broke every read through this adapter — including the
        ``parent = <key>`` traversal that walks an epic to its stories. The replacement drops
        ``total`` in favour of ``isLast``/``nextPageToken``, so callers cannot ask "how many
        are there?" any more; :meth:`_search_tree` decides truncation from ``isLast`` instead.

        Prefer an MCP server for Jira where one is available — it tracks these API changes so
        this client does not have to. See ``factory.mcp_server_for``.
        """
        return await self._get(
            "/search/jql",
            params={"jql": jql, "maxResults": str(min(max_results, 100)), "fields": _FIELDS},
        )

    def _read_ready(self) -> bool:
        """Reading needs creds but not a target ``project_key`` (that's write-only)."""
        c = self._config
        return bool(c.base_url and c.email and c.api_token)

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._read_ready():
            raise IssueTrackerError(
                "Jira not configured for reading (need JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)."
            )
        url = f"{self._config.api_base}{path}"
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.get(
                url,
                params=params,
                auth=(self._config.email, self._config.api_token),
                headers={"Accept": "application/json"},
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code != httpx.codes.OK:
            raise IssueTrackerError(f"GET {path} failed: HTTP {resp.status_code} {resp.text[:256]}")
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
