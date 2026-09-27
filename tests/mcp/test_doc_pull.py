"""``orchestrator mcp ingest-docs``: the pull, its tool guard, and the cache it writes (SSPN-80).

The fake server replays mcp-atlassian's shapes — ``confluence_get_page`` with
``include_metadata`` (``{"metadata": {..., "content": {"value": markdown}}}``),
``confluence_get_page_children`` (``{"results": [...]}``), ``jira_search`` (``{"total", "issues"}``)
and ``jira_get_issue`` (flattened issue with ``comments``) — and its annotations: read tools
report ``read_only=True``, write tools ``None``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.doc_pull import ToolGuardError, ingest_docs, vet_tools
from orchestrator.mcp.models import MCPServerStatus, MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.pkg.external_docs import failure_path, read_source, source_cache_dir
from orchestrator.pkg.repos import DocSource

_READ = {
    "confluence_get_page": True,
    "confluence_get_page_children": True,
    "jira_search": True,
    "jira_get_issue": True,
    "jira_create_issue": None,
    "jira_transition_issue": None,
}


class _FakeAtlassian:
    def __init__(
        self,
        *,
        pages: dict[str, dict[str, Any]] | None = None,
        children: dict[str, list[str]] | None = None,
        issues: dict[str, dict[str, Any]] | None = None,
        read_only: dict[str, bool | None] | None = None,
        total: int | None = None,
        fail: str = "",
        soft_fail: str = "",
    ) -> None:
        self.pages = pages or {}
        self.children = children or {}
        self.issues = issues or {}
        self.read_only = read_only or dict(_READ)
        self.total = total
        self.fail = fail
        self.soft_fail = soft_fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server="atlassian", name=n, read_only=ro) for n, ro in self.read_only.items()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append((name, arguments))
        if name == self.fail:
            return MCPToolResult(text="Error: 403 Forbidden", is_error=True)
        if name == self.soft_fail:
            # mcp-atlassian's own shape for a failed call: an ordinary result whose JSON body is
            # `{"error": ...}` — `is_error` stays False (servers/confluence.py, servers/jira.py).
            msg = "Failed to retrieve page by ID: Authentication failed for Confluence API (403)."
            return MCPToolResult(text=json.dumps({"error": msg}), is_error=False)
        if name == "confluence_get_page":
            page = self.pages[arguments["page_id"]]
            meta = {
                "id": arguments["page_id"],
                "title": page["title"],
                "type": "page",
                "url": page.get("url", ""),
            }
            meta["content"] = {"value": page["body"], "format": "markdown"}
            return MCPToolResult(text=json.dumps({"metadata": meta}))
        if name == "confluence_get_page_children":
            kids = self.children.get(arguments["parent_id"], [])
            window = kids[arguments["start"] : arguments["start"] + arguments["limit"]]
            results = [{"id": k, "title": self.pages[k]["title"], "type": "page"} for k in window]
            return MCPToolResult(text=json.dumps({"parent_id": arguments["parent_id"], "results": results}))
        if name == "jira_search":
            keys = sorted(self.issues)
            start = arguments.get("start_at", 0)
            window = keys[start : start + arguments["limit"]]
            payload: dict[str, Any] = {
                "start_at": start,
                "max_results": arguments["limit"],
                "issues": [{"key": k, "summary": self.issues[k]["summary"]} for k in window],
            }
            payload["total"] = len(keys) if self.total is None else self.total
            return MCPToolResult(text=json.dumps(payload))
        if name == "jira_get_issue":
            issue = self.issues[arguments["issue_key"]]
            return MCPToolResult(text=json.dumps({"key": arguments["issue_key"], **issue}))
        raise AssertionError(f"unexpected tool {name}")


def _registry(fake: _FakeAtlassian, allow: tuple[str, ...] | None = None) -> MCPRegistry:
    cfg = MCPServerConfig(name="atlassian", url="http://x", allow=allow or tuple(_READ))
    return MCPRegistry([cfg], client_factory=lambda _c: fake)


_WIKI = DocSource("wiki", "atlassian", "confluence", roots=("1",), max_depth=3, max_docs=100)
_JIRA = DocSource("tickets", "atlassian", "jira", jql="project = APP", max_issues=100)


def _tree() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    pages = {
        "1": {"title": "Root", "body": "# Root\n\nUse `Client`.", "url": "https://wiki/1"},
        "2": {"title": "Child A", "body": "A"},
        "3": {"title": "Child B", "body": "B"},
        "4": {"title": "Grandchild", "body": "G"},
    }
    return pages, {"1": ["2", "3"], "2": ["4"]}


async def _pull(
    fake: _FakeAtlassian, source: DocSource, tmp_path: Path, **kw: Any
) -> tuple[dict[str, Any], Path]:
    (row,) = await ingest_docs(
        _registry(fake), tmp_path / "repo", "app", [source], cache_base=tmp_path / "c", **kw
    )
    return row, source_cache_dir(tmp_path / "repo", "app", source.name, base=tmp_path / "c")


# ---- the tool guard -------------------------------------------------------------------------


async def test_a_tool_that_is_not_declared_read_only_is_refused_by_name_and_nothing_is_called(
    tmp_path: Path,
) -> None:
    pages, children = _tree()
    fake = _FakeAtlassian(pages=pages, children=children, read_only={**_READ, "confluence_get_page": None})
    row, dest = await _pull(fake, _WIKI, tmp_path)
    assert row["status"] == "refused"
    assert "atlassian:confluence_get_page (read_only=None" in row["error"]
    assert fake.calls == []
    assert read_source(dest, _WIKI).status == "failed"  # recorded, no folder written


async def test_a_tool_missing_from_the_allow_list_is_refused(tmp_path: Path) -> None:
    fake = _FakeAtlassian()
    registry = _registry(fake, allow=("jira_search", "jira_create_issue"))
    (row,) = await ingest_docs(registry, tmp_path, "app", [_JIRA], cache_base=tmp_path / "c")
    assert row["status"] == "refused" and "atlassian:jira_get_issue (not allow-listed" in row["error"]
    assert fake.calls == []


def test_vet_tools_names_every_refusal_and_never_clears_a_write_tool() -> None:
    tools = tuple(
        MCPTool(server="atlassian", name=n, read_only=None) for n in ("jira_search", "jira_get_issue")
    )
    with pytest.raises(ToolGuardError) as err:
        vet_tools(_JIRA, MCPServerStatus(name="atlassian", tools=tools))
    assert "jira_search" in str(err.value) and "jira_get_issue" in str(err.value)
    with pytest.raises(ToolGuardError, match="not configured"):
        vet_tools(_JIRA, None)


# ---- Confluence -----------------------------------------------------------------------------


async def test_confluence_walks_breadth_first_and_always_asks_for_markdown(tmp_path: Path) -> None:
    pages, children = _tree()
    fake = _FakeAtlassian(pages=pages, children=children)
    row, dest = await _pull(fake, _WIKI, tmp_path)
    assert row["status"] == "ok" and row["pulled"] == 4 and row["bound"] == "4 of 4"
    assert [c[1]["page_id"] for c in fake.calls if c[0] == "confluence_get_page"] == ["1", "2", "3", "4"]
    assert all(c[1]["convert_to_markdown"] is True for c in fake.calls)
    assert not any(c[0].startswith("jira_create") for c in fake.calls)
    cached = read_source(dest, _WIKI)
    assert [(p.id, p.title) for p in cached.pages][0] == ("1", "Root")
    assert cached.pages[0].text == "# Root\n\nUse `Client`." and cached.pages[0].url == "https://wiki/1"
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["server"] == "atlassian" and manifest["server_version"] is None
    assert manifest["counts"]["pages"] == 4 and manifest["status"] == "ok"
    assert {c["tool"] for c in manifest["tools_called"]} == {
        "confluence_get_page",
        "confluence_get_page_children",
    }


async def test_confluence_honours_max_depth(tmp_path: Path) -> None:
    pages, children = _tree()
    fake = _FakeAtlassian(pages=pages, children=children)
    row, _dest = await _pull(
        fake, DocSource("wiki", "atlassian", "confluence", roots=("1",), max_depth=1), tmp_path
    )
    assert row["pulled"] == 3 and row["truncated"] is False  # depth, not the cap, bounded it
    assert not any(
        c[0] == "confluence_get_page_children" and c[1]["parent_id"] in ("2", "3") for c in fake.calls
    )


async def test_confluence_reports_the_cap_with_what_it_had_seen(tmp_path: Path) -> None:
    pages, children = _tree()
    fake = _FakeAtlassian(pages=pages, children=children)
    source = DocSource("wiki", "atlassian", "confluence", roots=("1",), max_docs=2)
    row, _dest = await _pull(fake, source, tmp_path)
    assert row["pulled"] == 2 and row["truncated"] is True
    assert row["bound"] == "2 of at least 4 (cap reached)"


async def test_children_are_paged_fifty_at_a_time(tmp_path: Path) -> None:
    pages = {"1": {"title": "Root", "body": "r"}}
    pages.update({f"c{i:03}": {"title": f"C{i}", "body": "x"} for i in range(60)})
    fake = _FakeAtlassian(pages=pages, children={"1": sorted(k for k in pages if k != "1")})
    row, _dest = await _pull(
        fake, DocSource("wiki", "atlassian", "confluence", roots=("1",), max_depth=1), tmp_path
    )
    assert row["pulled"] == 61
    starts = [c[1]["start"] for c in fake.calls if c[0] == "confluence_get_page_children"]
    assert starts == [0, 50]


async def test_an_html_body_is_flattened_before_caching(tmp_path: Path) -> None:
    fake = _FakeAtlassian(
        pages={"1": {"title": "R", "body": "<h1>Setup</h1><p>Make a <code>Client</code>.</p>"}}
    )
    _row, dest = await _pull(fake, _WIKI, tmp_path)
    (page,) = read_source(dest, _WIKI).pages
    assert page.text.startswith("# Setup") and "`Client`" in page.text


async def test_a_server_error_keeps_the_last_good_pull(tmp_path: Path) -> None:
    pages, children = _tree()
    _row, dest = await _pull(_FakeAtlassian(pages=pages, children=children), _WIKI, tmp_path)
    good = (dest / "pages.jsonl").read_bytes()
    row, _dest = await _pull(
        _FakeAtlassian(pages=pages, children=children, fail="confluence_get_page_children"), _WIKI, tmp_path
    )
    assert row["status"] == "failed" and "403 Forbidden" in row["error"]
    assert (dest / "pages.jsonl").read_bytes() == good
    assert failure_path(dest).is_file()
    assert read_source(dest, _WIKI).status == "failed"


async def test_an_error_body_with_is_error_false_is_a_failure_not_an_empty_page(tmp_path: Path) -> None:
    """An expired token comes back as `{"error": ...}` with `is_error` False. Read as a page it
    "succeeds" with nothing in it and replaces the last good pull — it must fail instead."""
    pages, children = _tree()
    _row, dest = await _pull(_FakeAtlassian(pages=pages, children=children), _WIKI, tmp_path)
    good = (dest / "pages.jsonl").read_bytes()
    row, _dest = await _pull(
        _FakeAtlassian(pages=pages, children=children, soft_fail="confluence_get_page"), _WIKI, tmp_path
    )
    assert row["status"] == "failed" and "Authentication failed" in row["error"]
    assert (dest / "pages.jsonl").read_bytes() == good
    assert read_source(dest, _WIKI).status == "failed"


# ---- Jira ----------------------------------------------------------------------------------


def _issues(n: int) -> dict[str, dict[str, Any]]:
    return {
        f"APP-{i:03}": {
            "summary": f"Issue {i}",
            "description": f"Touches `Client` #{i}",
            "comments": [{"id": "1", "body": "a comment"}],
            "browse_url": f"https://jira/APP-{i:03}",
        }
        for i in range(n)
    }


async def test_jira_pages_the_search_and_never_updates_view_history(tmp_path: Path) -> None:
    fake = _FakeAtlassian(issues=_issues(120))
    row, dest = await _pull(
        fake, DocSource("t", "atlassian", "jira", jql="project = APP", max_issues=110), tmp_path
    )
    assert row["status"] == "ok" and row["pulled"] == 110 and row["bound"] == "110 of 120 (cap reached)"
    searches = [c[1] for c in fake.calls if c[0] == "jira_search"]
    assert [(s["start_at"], s["limit"]) for s in searches] == [(0, 50), (50, 50), (100, 10)]
    gets = [c[1] for c in fake.calls if c[0] == "jira_get_issue"]
    assert len(gets) == 110
    assert all(g["update_history"] is False and g["comment_limit"] == 10 for g in gets)
    page = read_source(dest, DocSource("t", "atlassian", "jira", jql="x")).pages[0]
    assert page.kind == "jira" and page.id == "APP-000" and page.url == "https://jira/APP-000"
    assert page.text == "APP-000: Issue 0\n\nTouches `Client` #0\n\na comment"


async def test_jira_under_the_cap_reports_every_issue(tmp_path: Path) -> None:
    fake = _FakeAtlassian(issues=_issues(3))
    row, _dest = await _pull(fake, _JIRA, tmp_path)
    assert row["bound"] == "3 of 3" and row["truncated"] is False
    assert len([c for c in fake.calls if c[0] == "jira_search"]) == 1


# ---- dry run and the CLI --------------------------------------------------------------------


async def test_dry_run_vets_the_tools_and_calls_nothing(tmp_path: Path) -> None:
    fake = _FakeAtlassian(issues=_issues(3))
    row, dest = await _pull(fake, _JIRA, tmp_path, dry_run=True)
    assert row["status"] == "planned" and fake.calls == []
    assert [t["tool"] for t in row["tools"]] == ["jira_search", "jira_get_issue"]
    assert row["tools"][1]["args"]["update_history"] is False
    assert not dest.exists()


def _cli_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".spine").mkdir(parents=True)
    (repo / ".spine" / "repos.yaml").write_text(
        "repos:\n  app: ..\ndocs:\n  app:\n"
        "    - {name: wiki, server: atlassian, confluence: {roots: ['1']}}\n"
        "    - {name: tickets, server: atlassian, jira: {jql: 'project = APP'}}\n",
        encoding="utf-8",
    )
    return repo


def _run_cli(monkeypatch: pytest.MonkeyPatch, fake: _FakeAtlassian, args: list[str]) -> Any:
    import orchestrator.cli.registry as cli_registry
    from orchestrator.cli import app

    monkeypatch.setattr(cli_registry, "_mcp_build_registry", lambda _configs: _registry(fake))
    monkeypatch.setattr(cli_registry, "_mcp_load_configs", lambda _path=None: [])
    return CliRunner().invoke(app, ["mcp", "ingest-docs", *args])


def test_cli_pulls_the_named_source_into_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_DOCS_CACHE_DIR", str(tmp_path / "cache"))
    pages, children = _tree()
    repo = _cli_repo(tmp_path)
    result = _run_cli(
        monkeypatch, _FakeAtlassian(pages=pages, children=children), ["--repo", str(repo), "--source", "wiki"]
    )
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["repo"] == "app" and [s["source"] for s in out["sources"]] == ["wiki"]
    assert out["sources"][0]["collapse"] == {"sections": 4, "identical_to_repo_sections": 0}
    assert Path(out["sources"][0]["cache"]).is_relative_to(tmp_path / "cache")


def test_cli_exits_2_on_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_DOCS_CACHE_DIR", str(tmp_path / "cache"))
    fake = _FakeAtlassian(issues=_issues(1), read_only={**_READ, "jira_get_issue": None})
    result = _run_cli(monkeypatch, fake, ["--repo", str(_cli_repo(tmp_path)), "--source", "tickets"])
    assert result.exit_code == 2
    assert "jira_get_issue" in result.output and fake.calls == []


def test_cli_rejects_an_unknown_source_and_an_undeclared_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _cli_repo(tmp_path)
    bad = _run_cli(monkeypatch, _FakeAtlassian(), ["--repo", str(repo), "--source", "nope"])
    assert bad.exit_code == 2 and "nope" in bad.output
    other = tmp_path / "other"
    other.mkdir()
    undeclared = _run_cli(
        monkeypatch, _FakeAtlassian(), ["--repo", str(other), "--repos", str(repo / ".spine" / "repos.yaml")]
    )
    assert undeclared.exit_code == 2 and "not declared" in undeclared.output
