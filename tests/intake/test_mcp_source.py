"""Requirements sources via an onboarded MCP server (Confluence, Jira, generic)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake.factory import (
    SUPPORTED_SOURCE_KINDS,
    IntakeNotConfiguredError,
    build_service_for,
)
from orchestrator.intake.mcp_source import MCPConfluenceAdapter, MCPSourceAdapter, MCPSourceConfig
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _FakeAtlassian:
    def __init__(self, pages: dict[str, Any], children: dict[str, Any]) -> None:
        self._pages, self._children = pages, children

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(server="confluence", name="confluence_get_page", read_only=True),
            MCPTool(server="confluence", name="confluence_get_page_children", read_only=True),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "confluence_get_page":
            return MCPToolResult(text=json.dumps(self._pages.get(arguments["page_id"], {})))
        if name == "confluence_get_page_children":
            return MCPToolResult(text=json.dumps(self._children.get(arguments["parent_id"], [])))
        return MCPToolResult(text="{}")


def _adapter(pages: dict[str, Any], children: dict[str, Any]) -> MCPConfluenceAdapter:
    cfg = MCPServerConfig(
        name="confluence",
        url="http://x",
        allow=("confluence_get_page", "confluence_get_page_children"),
    )
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeAtlassian(pages, children))
    return MCPConfluenceAdapter(registry, MCPSourceConfig())


async def test_fetch_document_parses_a_page() -> None:
    adapter = _adapter({"123": {"title": "Spec", "body": "As a user I want CSV export."}}, {})
    doc = await adapter.fetch_document("123")
    assert doc.id == "123" and doc.title == "Spec" and "CSV export" in doc.body


async def test_fetch_tree_walks_children_breadth_first() -> None:
    adapter = _adapter(
        {"root": {"title": "Root", "body": "r"}, "c1": {"title": "Child", "body": "c"}},
        {"root": [{"id": "c1", "title": "Child"}]},
    )
    result = await adapter.fetch_tree("root")
    assert {d.id for d in result.documents} == {"root", "c1"}
    assert result.truncated is False


async def test_fetch_tree_respects_max_docs() -> None:
    adapter = _adapter({"root": {"body": "r"}, "c1": {"body": "c"}}, {"root": [{"id": "c1"}]})
    result = await adapter.fetch_tree("root", max_docs=1)
    assert len(result.documents) == 1 and result.truncated is True


# ---- Jira over MCP (preset) -------------------------------------------------


class _FakeJira:
    """Minimal mcp-atlassian Jira: jira_get_issue + jira_search."""

    def __init__(self, issues: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> None:
        self._issues, self._children = issues, children
        self.searched: list[str] = []

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(server="jira", name="jira_get_issue", read_only=True),
            MCPTool(server="jira", name="jira_search", read_only=True),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "jira_get_issue":
            return MCPToolResult(text=json.dumps(self._issues.get(arguments["issue_key"], {})))
        if name == "jira_search":
            jql = str(arguments["jql"])
            self.searched.append(jql)
            return MCPToolResult(text=json.dumps({"issues": self._children.get(jql, [])}))
        return MCPToolResult(text="{}")


def _jira_adapter(issues: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> MCPSourceAdapter:
    cfg = MCPServerConfig(name="jira", url="http://x", allow=("jira_get_issue", "jira_search"))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeJira(issues, children))
    return MCPSourceAdapter(registry, MCPSourceConfig.for_jira())


async def test_mcp_jira_fetches_issue_with_adf_description() -> None:
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "boom"}]}]}
    adapter = _jira_adapter(
        {"ENG-1": {"key": "ENG-1", "fields": {"summary": "Login 500", "description": adf}}}, {}
    )
    doc = await adapter.fetch_document("ENG-1")
    assert doc.id == "ENG-1" and doc.title == "Login 500" and "boom" in doc.body


async def test_mcp_jira_walks_children_via_parent_jql() -> None:
    adapter = _jira_adapter(
        {
            "ENG-1": {"key": "ENG-1", "fields": {"summary": "Epic"}},
            "ENG-2": {"key": "ENG-2", "fields": {"summary": "Sub"}},
        },
        {"parent = ENG-1": [{"key": "ENG-2", "fields": {"summary": "Sub"}}]},
    )
    result = await adapter.fetch_tree("ENG-1")
    assert {d.id for d in result.documents} == {"ENG-1", "ENG-2"}


# ---- generic escape hatch + raw-text fallback -------------------------------


class _FakeAny:
    def __init__(self, text: str) -> None:
        self._text = text

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server="acme", name="get_doc", read_only=True)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(text=self._text)


async def test_generic_server_falls_back_to_raw_text() -> None:
    cfg = MCPServerConfig(name="acme", url="http://x", allow=("get_doc",))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeAny("plain requirements text"))
    config = MCPSourceConfig(
        source_kind="mcp", server="acme", doc_tool="get_doc", doc_arg="id", children_tool=""
    )
    adapter = MCPSourceAdapter(registry, config)
    doc = await adapter.fetch_document("D1")
    assert doc.id == "D1" and doc.body == "plain requirements text"
    assert await adapter.list_children("D1") == []  # no children_tool → no walk


# ---- factory dispatch -------------------------------------------------------


def test_mcp_kinds_are_supported() -> None:
    for kind in ("mcp-confluence", "mcp-jira", "mcp"):
        assert kind in SUPPORTED_SOURCE_KINDS


def test_generic_mcp_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_SOURCE_SERVER", raising=False)
    monkeypatch.delenv("MCP_SOURCE_DOC_TOOL", raising=False)
    with pytest.raises(IntakeNotConfiguredError, match="MCP source"):
        build_service_for("mcp://D1", dry_run=True)


def test_mcp_confluence_is_a_supported_source_kind() -> None:
    assert "mcp-confluence" in SUPPORTED_SOURCE_KINDS


def test_unconfigured_mcp_confluence_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(tmp_path / "absent.json"))  # no servers
    with pytest.raises(IntakeNotConfiguredError, match="MCP Confluence source"):
        build_service_for("mcp-confluence://123", dry_run=True)


def test_configured_mcp_confluence_builds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfgfile = tmp_path / "mcp.json"
    cfgfile.write_text(
        '{"mcpServers": {"confluence": {"url": "http://x", "allow": ["confluence_get_page"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(cfgfile))
    service = build_service_for("mcp-confluence://123", dry_run=True)  # builds without connecting
    assert service is not None


async def test_mcp_jira_carries_issue_type_status_and_priority() -> None:
    """The gap this closes: SourceDocument has no field for issue type, so the REST adapter
    prepends it to the body. The MCP path did not, and the same epic ingested over MCP
    arrived untyped — the extractor could not tell a Bug from a Story or done from open."""
    adapter = _jira_adapter(
        {
            "ENG-9": {
                "key": "ENG-9",
                "fields": {
                    "summary": "Login 500",
                    "description": "stack trace",
                    "issuetype": {"name": "Bug"},
                    "status": {"name": "Open"},
                    "priority": {"name": "High"},
                },
            }
        },
        {},
    )
    doc = await adapter.fetch_document("ENG-9")
    assert doc.body.startswith("Bug · status: Open · priority: High")
    assert "stack trace" in doc.body
    assert doc.space == "ENG", "project key should come from the issue key, as in REST"
    # The same parity requirement, one layer down: an issue ingested over MCP must select the
    # same workflow profile as the identical issue over REST, which means the *field* agrees
    # and not just the prose header.
    assert doc.issue_type == "Bug"


async def test_mcp_jira_matches_the_rest_adapter_byte_for_byte() -> None:
    """Parity is the actual requirement — one issue must read the same whichever transport
    fetched it. Asserting equality (rather than each field) is what stops the two drifting."""
    from orchestrator.intake.jira import JiraConfig
    from orchestrator.intake.jira_source import JiraSourceAdapter

    fields = {
        "summary": "Login 500",
        "description": "stack trace",
        "issuetype": {"name": "Bug"},
        "status": {"name": "Open"},
        "priority": {"name": "High"},
        "labels": ["auth"],
    }
    mcp_doc = await _jira_adapter({"ENG-9": {"key": "ENG-9", "fields": fields}}, {}).fetch_document("ENG-9")
    rest = JiraSourceAdapter(JiraConfig(base_url="https://x.atlassian.net", email="e", api_token="t"))
    rest_doc = rest._issue_to_document({"key": "ENG-9", "fields": fields})

    assert mcp_doc.body == rest_doc.body
    assert mcp_doc.title == rest_doc.title
    assert mcp_doc.labels == rest_doc.labels
    assert mcp_doc.space == rest_doc.space


async def test_mcp_jira_omits_the_header_when_the_issue_has_no_type() -> None:
    """A bare issue must not gain a stray blank header line."""
    adapter = _jira_adapter({"ENG-1": {"key": "ENG-1", "fields": {"summary": "S", "description": "d"}}}, {})
    doc = await adapter.fetch_document("ENG-1")
    assert doc.body == "d"
    assert doc.space == ""


async def test_confluence_pages_are_untouched_by_the_jira_header() -> None:
    """`_parse_document` is shared across source kinds; a wiki page has no issue metadata
    and must not acquire a header or a project key."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document("123", {"title": "Design", "body": "the body"}, "")
    assert doc.body == "the body"
    assert doc.space == ""


# Captured verbatim from mcp-atlassian 3.4.4 (`jira_get_issue` on a real Jira Cloud issue).
# NOT hand-written: the previous fixtures used Jira REST's nested `fields` shape, which this
# server does not produce — so the parser and its tests agreed with each other and both were
# wrong about reality. Empty fields (description, labels) are absent, not null.
_REAL_MCP_ATLASSIAN_ISSUE = {
    "id": "36672",
    "key": "CB-676",
    "summary": "CLONE - CLONE - Write/update script to store this data in S3",
    "browse_url": "https://fibonacci-solutions.atlassian.net/browse/CB-676",
    "status": {"name": "Business Requirements", "category": "To Do", "color": "blue-gray"},
    "issue_type": {"name": "Sub-task"},
    "priority": {"name": "Medium"},
}


def test_parses_the_flattened_mcp_atlassian_shape() -> None:
    """mcp-atlassian returns issue attributes at the TOP LEVEL with no `fields` envelope,
    and spells the type `issue_type` where Jira REST spells it `issuetype`."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document("CB-676", _REAL_MCP_ATLASSIAN_ISSUE, "")
    assert doc.body.startswith("Sub-task · status: Business Requirements · priority: Medium")
    # `issue_type`, the mcp-atlassian spelling — the field reads it as the header does.
    assert doc.issue_type == "Sub-task"


def test_prefers_the_issue_key_over_the_opaque_numeric_id() -> None:
    """`id` is 36672; `key` is CB-676. The key is what the browse URL, JQL and the project
    prefix are all built from — keying on `id` left documents nobody could match up."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document("CB-676", _REAL_MCP_ATLASSIAN_ISSUE, "")
    assert doc.id == "CB-676"
    assert doc.space == "CB"


def test_uses_the_browse_url_the_server_supplies() -> None:
    """The server hands back the browse link, so `url` is available over MCP after all."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document("CB-676", _REAL_MCP_ATLASSIAN_ISSUE, "")
    assert doc.url == "https://fibonacci-solutions.atlassian.net/browse/CB-676"


def test_title_survives_when_the_description_field_is_absent() -> None:
    """This issue has no description, and mcp-atlassian omits empty fields rather than
    returning null — the body must still be the metadata header, never raw JSON."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document("CB-676", _REAL_MCP_ATLASSIAN_ISSUE, "{raw fallback}")
    assert doc.title == "CLONE - CLONE - Write/update script to store this data in S3"
    assert "{raw fallback}" not in doc.body
    assert doc.body.count("\n") == 0, "header only, no stray blank lines"


def test_nested_rest_shape_still_parses() -> None:
    """The REST-shaped payload must keep working — both transports share this parser."""
    from orchestrator.intake.mcp_source import _parse_document

    doc = _parse_document(
        "ENG-1",
        {"key": "ENG-1", "fields": {"summary": "S", "description": "d", "issuetype": {"name": "Bug"}}},
        "",
    )
    assert doc.id == "ENG-1"
    assert doc.body.startswith("Bug")
    assert "d" in doc.body


# ---- parity with comments, links and attachments (Track E, E3) ---------------------------------


def _adf_para(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


# What mcp-atlassian returns for the same ticket: it reads Cloud through API v2 and converts wiki
# markup to Markdown (source @ `0a5d242`, `jira/issues.py`), so formatted prose reads differently.
_MCP_DESCRIPTION = "## Steps\n\nThe CSV export loses the currency column."
_MCP_COMMENT_BODIES = ["Seen on EUR.", "Keep **ISO** codes."]


_RICH_REST_FIELDS: dict[str, Any] = {
    "summary": "Orders export drops the currency column",
    # What Jira Cloud's v3 API returns: ADF, never a plain string.
    "description": {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Steps"}]},
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "The CSV export loses the currency column."}],
            },
        ],
    },
    "issuetype": {"name": "Bug"},
    "status": {"name": "To Do"},
    "priority": {"name": "High"},
    "labels": ["export"],
    "parent": {"key": "FIN-1", "fields": {"summary": "Finance exports"}},
    "issuelinks": [
        {
            "type": {"inward": "is blocked by", "outward": "blocks"},
            "outwardIssue": {"key": "FIN-9", "fields": {"summary": "Quarter close"}},
        }
    ],
    "comment": {
        "total": 2,
        "comments": [
            {
                "author": {"displayName": "Ana"},
                "created": "2026-09-01T10:00:00.000+0000",
                "body": _adf_para("Seen on EUR."),
            },
            {
                "author": {"displayName": "Raj"},
                "created": "2026-09-02T10:00:00.000+0000",
                "body": _adf_para("Keep ISO codes."),
            },
        ],
    },
    "attachment": [
        {"filename": "mapping.md", "size": 60, "content": "https://x.atlassian.net/att/1"},
        {"filename": "rules.md", "size": 40_000, "content": "https://x.atlassian.net/att/2"},
        {"filename": "screen.png", "size": 10, "content": "https://x.atlassian.net/att/3"},
    ],
}
_FILES = {
    "mapping.md": b"# Mapping\n\n- currency: column 7\n",
    "rules.md": ("- keep currency\n" * 2_500).encode(),
}


def _flattened(fields: dict[str, Any]) -> dict[str, Any]:
    """The same issue as mcp-atlassian v0.23.0+53 (`0a5d242`) returns it: flattened, three parts renamed."""
    return {
        "key": "FIN-42",
        "summary": fields["summary"],
        "description": _MCP_DESCRIPTION,
        "issue_type": fields["issuetype"],
        "status": fields["status"],
        "priority": fields["priority"],
        "labels": fields["labels"],
        "parent": fields["parent"],
        "issuelinks": [
            {
                "id": "1",
                "type": {"id": "10", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "outward_issue": {"id": "9", "key": "FIN-9", "fields": {"summary": "Quarter close"}},
            }
        ],
        "comments": [
            {
                "id": str(i),
                "body": _MCP_COMMENT_BODIES[i],
                "author": {"display_name": c["author"]["displayName"], "name": "x"},
                "created": c["created"],
            }
            for i, c in enumerate(fields["comment"]["comments"])
        ],
        "attachments": [
            {"filename": a["filename"], "size": a["size"], "url": a["content"]} for a in fields["attachment"]
        ],
    }


class _RichJira:
    """mcp-atlassian with the attachment toolset: `jira_get_issue` + `jira_download_attachments`."""

    def __init__(self, *, accepts_fields: bool = True) -> None:
        self.accepts_fields = accepts_fields
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(server="jira", name=n, read_only=True)
            for n in ("jira_get_issue", "jira_download_attachments")
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        import base64

        self.calls.append((name, arguments))
        if name == "jira_get_issue":
            if "fields" in arguments and not self.accepts_fields:
                return MCPToolResult(text="unknown argument: fields", is_error=True)
            payload = _flattened(_RICH_REST_FIELDS)
            if "fields" not in arguments:  # a server's defaults: no attachments, no links
                payload = {k: v for k, v in payload.items() if k not in {"attachments", "issuelinks"}}
            return MCPToolResult(text=json.dumps(payload))
        if name == "jira_download_attachments":
            parts = [json.dumps({"success": True, "issue_key": arguments["issue_key"], "total": 3}, indent=2)]
            parts += [
                json.dumps(
                    {
                        "filename": n,
                        "mime_type": "text/markdown",
                        "encoding": "base64",
                        "content": base64.b64encode(b).decode(),
                    },
                    indent=2,
                )
                for n, b in _FILES.items()
            ]
            return MCPToolResult(text="".join(parts))  # parts arrive concatenated
        return MCPToolResult(text="{}")


def _rich_adapter(
    fake: _RichJira, *, allow: tuple[str, ...] = ("jira_get_issue", "jira_download_attachments")
) -> MCPSourceAdapter:
    cfg = MCPServerConfig(name="jira", url="http://x", allow=allow)
    return MCPSourceAdapter(MCPRegistry([cfg], client_factory=lambda _c: fake), MCPSourceConfig.for_jira())


async def _rest_document() -> Any:
    import httpx

    from orchestrator.intake.jira import JiraConfig
    from orchestrator.intake.jira_source import JiraSourceAdapter

    by_url = {a["content"]: _FILES.get(a["filename"]) for a in _RICH_REST_FIELDS["attachment"]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issue/FIN-42"):
            return httpx.Response(200, json={"key": "FIN-42", "fields": _RICH_REST_FIELDS})
        content = by_url.get(str(request.url))
        return httpx.Response(200, content=content) if content else httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x.atlassian.net")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url="https://x.atlassian.net", email="e", api_token="t"), http_client=http
    )
    async with http:
        return await adapter.fetch_document("FIN-42")


async def test_an_issue_with_comments_links_and_attachments_carries_every_part_over_mcp_and_rest() -> None:
    """Track E, E3 — and review finding 3, which corrected what "the same" can mean. Over MCP the
    ticket carries every part REST does: the header, the description, the linked issues, each
    comment, every attachment read and every one named. Prose is *not* byte-identical: REST
    flattens v3 ADF to text, mcp-atlassian returns Markdown converted from wiki markup, and making
    two upstream converters agree on every formatting rule is not a promise the code can keep.
    Attachments are identical — both transports extract them with the same reader."""
    fake = _RichJira()
    mcp_doc = await _rich_adapter(fake).fetch_document("FIN-42")
    rest_doc = await _rest_document()

    for doc in (mcp_doc, rest_doc):
        body = doc.body
        assert body.startswith("Bug · status: To Do · priority: High")
        assert "Steps" in body and "The CSV export loses the currency column." in body
        assert "- parent FIN-1 — Finance exports" in body and "- blocks FIN-9 — Quarter close" in body
        assert "Comments (2 of 2, most recent first):" in body
        assert "- Raj (2026-09-02):" in body and "- Ana (2026-09-01): Seen on EUR." in body
        assert "screen.png (image, not read)" in body
        assert "Attachments read in full (2):" in doc.full_body
    assert "## Steps" in mcp_doc.body and "## Steps" not in rest_doc.body  # the declared difference

    def attachments(text: str) -> str:
        return text[text.index("Attachments read") :]

    assert attachments(mcp_doc.body) == attachments(rest_doc.body)
    assert attachments(mcp_doc.full_body) == attachments(rest_doc.full_body)
    assert [name for name, _ in fake.calls] == ["jira_get_issue", "jira_download_attachments"]
    assert fake.calls[0][1]["fields"].split(",")[-3:] == ["comment", "issuelinks", "attachment"]


async def test_a_transient_error_is_a_failure_not_a_refusal() -> None:
    """Review finding 4: a 429 read as "refused fields" and was retried bare — the server's
    defaults then rendered as "Comments (10 of 10…)", completeness nobody had."""
    from orchestrator.mcp.client import MCPError

    class _Busy(_RichJira):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
            self.calls.append((name, arguments))
            return MCPToolResult(text="HTTP 429 Too Many Requests", is_error=True)

    fake = _Busy()
    with pytest.raises(MCPError, match="429"):
        await _rich_adapter(fake).fetch_document("FIN-42")
    assert len(fake.calls) == 1  # no bare retry


async def test_two_attachments_with_one_name_are_both_named_not_one_read() -> None:
    """Review finding 3: mcp-atlassian gives no attachment id and downloads by name, so two
    revisions of `spec.md` collapsed into one — the other neither read nor named."""
    payload = _flattened(_RICH_REST_FIELDS)
    payload["attachments"] = [
        {"filename": "spec.md", "size": 10, "url": "https://x/1"},
        {"filename": "spec.md", "size": 12, "url": "https://x/2"},
    ]

    class _Twice(_RichJira):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
            if name == "jira_get_issue":
                self.calls.append((name, arguments))
                return MCPToolResult(text=json.dumps(payload))
            return await super().call_tool(name, arguments)

    doc = await _rich_adapter(_Twice()).fetch_document("FIN-42")
    same = "another attachment has the same name — the MCP server cannot tell them apart"
    assert doc.body.count(f"spec.md ({same})") == 2


async def test_a_server_without_the_download_tool_names_each_attachment_with_why() -> None:
    doc = await _rich_adapter(_RichJira(), allow=("jira_get_issue",)).fetch_document("FIN-42")
    assert "mapping.md (the MCP server offers no attachment download (PermissionError))" in doc.body
    assert "Comments (2 of 2" in doc.body  # everything else still read


async def test_a_server_that_refuses_the_fields_request_reads_the_description_and_says_so() -> None:
    from orchestrator.intake.mcp_source import MCP_JIRA_FIELDS_REFUSED

    fake = _RichJira(accepts_fields=False)
    doc = await _rich_adapter(fake).fetch_document("FIN-42")
    assert doc.body.endswith(MCP_JIRA_FIELDS_REFUSED)
    assert [args.get("fields") for _, args in fake.calls] == [
        "summary,description,issuetype,status,priority,labels,parent,comment,issuelinks,attachment",
        None,
    ]


async def test_more_comments_than_are_shown_are_said_to_exist_not_counted() -> None:
    """mcp-atlassian returns the newest N and no total; asking for one more than is shown is how
    the adapter knows more exist without inventing a count."""
    from orchestrator.intake.jira_source import _MAX_COMMENTS

    payload = _flattened(_RICH_REST_FIELDS)
    payload["comments"] = [
        {"id": str(i), "body": f"comment {i}", "author": {"display_name": "A"}, "created": "2026-09-01"}
        for i in range(_MAX_COMMENTS + 1)
    ]
    payload.pop("attachments")

    class _Many(_RichJira):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
            self.calls.append((name, arguments))
            return MCPToolResult(text=json.dumps(payload))

    fake = _Many()
    doc = await _rich_adapter(fake).fetch_document("FIN-42")
    assert fake.calls[0][1]["comment_limit"] == _MAX_COMMENTS + 1
    assert f"Comments ({_MAX_COMMENTS} most recent — more exist):" in doc.body
    assert "comment 0" not in doc.body  # the oldest is the one left out


async def test_links_to_follow_are_found_over_mcp_too() -> None:
    """`--follow-links` over MCP: remote links arrive raw (`include=remote_links`)."""
    remote = [
        {
            "globalId": "appId=a&pageId=77",
            "application": {"type": "com.atlassian.confluence"},
            "object": {"url": "https://x.atlassian.net/wiki/pages/viewpage.action?pageId=77"},
        }
    ]

    class _WithLinks(_RichJira):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
            self.calls.append((name, arguments))
            return MCPToolResult(
                text=json.dumps(
                    {
                        "key": "FIN-42",
                        "browse_url": "https://x.atlassian.net/browse/FIN-42",
                        "description": "see the page",
                        "remote_links": remote,
                    }
                )
            )

    fake = _WithLinks()
    linked = await _rich_adapter(fake).linked_pages("FIN-42")
    assert [p.page_id for p in linked.pages] == ["77"]
    assert fake.calls[0][1]["include"] == "remote_links"
