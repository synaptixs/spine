"""MCP is the preferred transport wherever a capable server is onboarded.

A governed MCP server beats direct REST credentials: secrets stay with the operator's
server, calls are allow-listed and audited, and the server tracks upstream API changes a
hand-rolled client does not. Spine's own Jira REST reader broke exactly that way when
Atlassian removed `GET /rest/api/3/search`.

Resolution must be **config-only** — building a source must never launch a server to ask
what it exposes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.intake.factory import mcp_server_for


def _config(tmp_path: Path, servers: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(p))


def test_no_config_means_no_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With nothing onboarded, REST stays the path — this must not break existing users."""
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(tmp_path / "absent.json"))
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") is None


def test_picks_the_server_that_allow_lists_the_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(
        tmp_path,
        {
            "db": {"command": "x", "allow": ["query"]},
            "atlassian": {"command": "y", "allow": ["jira_get_issue", "confluence_get_page"]},
        },
        monkeypatch,
    )
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") == "atlassian"
    assert mcp_server_for("confluence_get_page", "MCP_CONFLUENCE_SERVER") == "atlassian"
    assert mcp_server_for("query", "MCP_SOURCE_SERVER") == "db"


def test_env_var_overrides_the_allow_list_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit choice wins — the operator may run two Atlassian servers."""
    _config(
        tmp_path,
        {
            "a": {"command": "x", "allow": ["jira_get_issue"]},
            "b": {"command": "y", "allow": ["jira_get_issue"]},
        },
        monkeypatch,
    )
    monkeypatch.setenv("MCP_JIRA_SERVER", "b")
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") == "b"


def test_env_var_naming_an_absent_server_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale env var must not silently disable MCP — fall through to the scan."""
    _config(tmp_path, {"atlassian": {"command": "x", "allow": ["jira_get_issue"]}}, monkeypatch)
    monkeypatch.setenv("MCP_JIRA_SERVER", "typo")
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") == "atlassian"


def test_a_lone_unrestricted_server_is_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No allow-list means it may serve anything; with one server there is nothing to guess."""
    _config(tmp_path, {"atlassian": {"command": "x"}}, monkeypatch)
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") == "atlassian"


def test_several_unrestricted_servers_decline_to_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Picking one arbitrarily would route a ticket through whichever server sorted first."""
    _config(tmp_path, {"a": {"command": "x"}, "b": {"command": "y"}}, monkeypatch)
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") is None


def test_disabled_servers_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(
        tmp_path,
        {"atlassian": {"command": "x", "allow": ["jira_get_issue"], "enabled": False}},
        monkeypatch,
    )
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") is None


def test_a_malformed_config_falls_back_rather_than_exploding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken mcp.json must not take REST sources down with it."""
    p = tmp_path / "mcp.json"
    p.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(p))
    assert mcp_server_for("jira_get_issue", "MCP_JIRA_SERVER") is None
