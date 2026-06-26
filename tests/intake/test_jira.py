"""Block B.5 unit tests: Jira adapter create/link, dry-run, ADF, errors."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import httpx
import pytest

from orchestrator.intake.jira import (
    IssueLink,
    IssueRequest,
    IssueTrackerError,
    JiraAdapter,
    JiraConfig,
    _text_to_adf,
)


def _config(*, dry_run: bool) -> JiraConfig:
    return JiraConfig(
        base_url="https://acme.atlassian.net",
        email="bot@acme.io",
        api_token="tok",
        project_key="ENG",
        dry_run=dry_run,
    )


# ---- ADF ------------------------------------------------------------------


def test_text_to_adf_one_paragraph_per_line() -> None:
    adf = _text_to_adf("line one\n\nline two")
    assert adf["type"] == "doc"
    assert len(adf["content"]) == 2  # blank line skipped
    assert adf["content"][0]["content"][0]["text"] == "line one"


def test_text_to_adf_empty_is_valid_doc() -> None:
    adf = _text_to_adf("")
    assert adf["content"] == [{"type": "paragraph", "content": []}]


# ---- dry-run --------------------------------------------------------------


async def test_dry_run_create_returns_synthetic_keys_no_api() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(201, json={"key": "ENG-1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=True), http_client=http)
    try:
        a = await adapter.create_issue(IssueRequest(summary="First"))
        b = await adapter.create_issue(IssueRequest(summary="Second"))
        await adapter.link_issues(IssueLink(inward_key="DRY-1", outward_key="DRY-2"))
    finally:
        await http.aclose()

    assert a.key == "DRY-1" and a.dry_run is True
    assert b.key == "DRY-2"
    assert calls["n"] == 0  # nothing hit the API


# ---- live create + link ---------------------------------------------------


async def test_live_create_posts_fields_and_returns_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issue"):
            captured["issue"] = jsonlib.loads(request.content)
            return httpx.Response(201, json={"key": "ENG-42", "id": "10042"})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        created = await adapter.create_issue(
            IssueRequest(
                summary="Add CSV export",
                description="Summary line.\nAcceptance: downloads <5s.",
                issue_type="Story",
                labels=("sdlc", "backlog"),
                parent_key="ENG-1",
            )
        )
    finally:
        await http.aclose()

    assert created.key == "ENG-42"
    assert created.id == "10042"
    assert created.url == "https://acme.atlassian.net/browse/ENG-42"
    fields = captured["issue"]["fields"]
    assert fields["project"] == {"key": "ENG"}
    assert fields["summary"] == "Add CSV export"
    assert fields["issuetype"] == {"name": "Story"}
    assert fields["labels"] == ["sdlc", "backlog"]
    assert fields["parent"] == {"key": "ENG-1"}
    # description is ADF, two paragraphs
    assert fields["description"]["type"] == "doc"
    assert len(fields["description"]["content"]) == 2


async def test_live_link_posts_issue_link() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["link"] = jsonlib.loads(request.content)
        return httpx.Response(201)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        await adapter.link_issues(IssueLink(inward_key="ENG-2", outward_key="ENG-1", link_type="Blocks"))
    finally:
        await http.aclose()
    assert captured["link"]["type"] == {"name": "Blocks"}
    assert captured["link"]["inwardIssue"] == {"key": "ENG-2"}


# ---- comment (issue update) ----------------------------------------------


async def test_dry_run_comment_makes_no_api_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(201, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=True), http_client=http)
    try:
        await adapter.comment_issue("ENG-1", "PR opened: https://github.com/x/y/pull/3")
    finally:
        await http.aclose()
    assert calls["n"] == 0


async def test_live_comment_posts_adf_to_comment_endpoint() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = jsonlib.loads(request.content)
        return httpx.Response(201, json={"id": "1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        await adapter.comment_issue("ENG-42", "PR opened: https://github.com/x/y/pull/3")
    finally:
        await http.aclose()
    assert captured["path"].endswith("/issue/ENG-42/comment")
    assert captured["body"]["body"]["type"] == "doc"
    assert captured["body"]["body"]["content"][0]["content"][0]["text"].startswith("PR opened")


# ---- transitions ----------------------------------------------------------


async def test_dry_run_transition_makes_no_api_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"transitions": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=True), http_client=http)
    try:
        result = await adapter.transition_issue("ENG-1", "Done")
    finally:
        await http.aclose()
    assert result is None
    assert calls["n"] == 0


async def test_live_transition_resolves_id_by_target_status_name() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            # Transition ids are workflow-specific; resolve by destination name.
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "11", "to": {"name": "In Progress"}},
                        {"id": "31", "to": {"name": "Done"}},
                    ]
                },
            )
        captured["path"] = request.url.path
        captured["body"] = jsonlib.loads(request.content)
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        moved = await adapter.transition_issue("ENG-7", "done")  # case-insensitive
    finally:
        await http.aclose()
    assert moved == "Done"
    assert captured["path"].endswith("/issue/ENG-7/transitions")
    assert captured["body"] == {"transition": {"id": "31"}}


async def test_transition_to_unavailable_status_raises_with_options() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"transitions": [{"id": "11", "to": {"name": "In Progress"}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        with pytest.raises(IssueTrackerError, match="In Progress"):  # lists what IS available
            await adapter.transition_issue("ENG-7", "Done")
    finally:
        await http.aclose()


# ---- errors ---------------------------------------------------------------


async def test_unconfigured_live_create_raises() -> None:
    # _env_file=None so a developer's local .env can't make this "configured".
    adapter = JiraAdapter(JiraConfig(dry_run=False, _env_file=None))  # type: ignore[call-arg]  # missing creds
    with pytest.raises(IssueTrackerError, match="not configured"):
        await adapter.create_issue(IssueRequest(summary="x"))


async def test_api_error_surfaces() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": {"summary": "required"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        with pytest.raises(IssueTrackerError, match="HTTP 400"):
            await adapter.create_issue(IssueRequest(summary="x"))
    finally:
        await http.aclose()
