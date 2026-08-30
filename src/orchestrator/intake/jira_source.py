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
from collections import deque
from typing import Any

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
_FIELDS = "summary,description,issuetype,status,priority,labels"

_MULTI_BLANK_RE = re.compile(r"\n{3,}")


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


class JiraSourceAdapter:
    """SourceAdapter over Jira Cloud v3 (read-only)."""

    source_kind = "jira"

    def __init__(self, config: JiraConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None

    def _issue_to_document(self, issue: dict[str, Any]) -> SourceDocument:
        key = str(issue.get("key", ""))
        fields = issue.get("fields") or {}
        summary = str(fields.get("summary") or "")
        labels = tuple(str(x) for x in (fields.get("labels") or []))
        # A short metadata header gives the extractor context — a Bug reads
        # differently from a Story, and status tells done from open.
        header = issue_meta_header(fields)
        body = _collapse("\n\n".join(p for p in (header, _description_text(fields.get("description"))) if p))
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
        return self._issue_to_document(data)

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
