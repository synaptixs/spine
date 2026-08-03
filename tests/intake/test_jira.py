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
    # Description is ADF. Two lines with no blank between them are one paragraph
    # joined by a hardBreak — the author's line break survives without markdown's
    # reflow merging them into a run-on.
    assert fields["description"]["type"] == "doc"
    assert len(fields["description"]["content"]) == 1
    assert [n["type"] for n in fields["description"]["content"][0]["content"]] == [
        "text",
        "hardBreak",
        "text",
    ]


# ---- issue type resolution ------------------------------------------------

_PROJECT_TYPES = {"issueTypes": [{"id": "10302", "name": "Story"}, {"id": "10299", "name": "Epic"}]}


def _typed_transport(
    captured: dict[str, Any], *, createmeta: httpx.Response | None = None
) -> httpx.MockTransport:
    """Serves the project's create-meta, then captures the issue POST."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "createmeta" in request.url.path:
            return createmeta if createmeta is not None else httpx.Response(200, json=_PROJECT_TYPES)
        captured["issue"] = jsonlib.loads(request.content)
        return httpx.Response(201, json={"key": "ENG-9", "id": "9"})

    return httpx.MockTransport(handler)


async def _create(config: JiraConfig, request: IssueRequest, transport: httpx.MockTransport) -> None:
    http = httpx.AsyncClient(transport=transport, base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(config, http_client=http)
    try:
        await adapter.create_issue(request)
    finally:
        await http.aclose()


@pytest.mark.parametrize(
    ("requested", "configured", "expected_id"),
    [
        ("", "Story", "10302"),  # nothing named → the project's configured default
        ("Epic", "Story", "10299"),  # the caller names one → the caller wins
    ],
)
async def test_issue_type_is_sent_as_a_project_scoped_id(
    requested: str, configured: str, expected_id: str
) -> None:
    """Names are resolved site-wide by Jira, so a project-scoped type must go by id.

    A team-managed site can hold several unrelated types called "Epic" and no global
    one; posting the name then fails with "Specify a valid issue type" even though the
    project has it.
    """
    captured: dict[str, Any] = {}
    config = _config(dry_run=False)
    config.issue_type = configured
    await _create(config, IssueRequest(summary="s", issue_type=requested), _typed_transport(captured))
    assert captured["issue"]["fields"]["issuetype"] == {"id": expected_id}


async def test_unknown_issue_type_names_what_the_project_offers() -> None:
    captured: dict[str, Any] = {}
    config = _config(dry_run=False)
    with pytest.raises(IssueTrackerError, match="does not exist in project 'ENG'.*available: epic, story"):
        await _create(config, IssueRequest(summary="s", issue_type="Nope"), _typed_transport(captured))
    assert "issue" not in captured  # refused before writing anything


async def test_unreadable_create_meta_falls_back_to_the_type_name() -> None:
    """Not being able to *verify* the type is no reason to refuse to try it."""
    captured: dict[str, Any] = {}
    config = _config(dry_run=False)
    transport = _typed_transport(captured, createmeta=httpx.Response(403, json={}))
    await _create(config, IssueRequest(summary="s", issue_type="Story"), transport)
    assert captured["issue"]["fields"]["issuetype"] == {"name": "Story"}


async def test_create_meta_is_fetched_once_per_adapter() -> None:
    calls = {"meta": 0}
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "createmeta" in request.url.path:
            calls["meta"] += 1
            return httpx.Response(200, json=_PROJECT_TYPES)
        captured["issue"] = jsonlib.loads(request.content)
        return httpx.Response(201, json={"key": "ENG-9", "id": "9"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=False), http_client=http)
    try:
        for _ in range(3):
            await adapter.create_issue(IssueRequest(summary="s", issue_type="Story"))
    finally:
        await http.aclose()
    assert calls["meta"] == 1


async def test_dry_run_never_fetches_create_meta() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        calls["n"] += 1
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = JiraAdapter(_config(dry_run=True), http_client=http)
    try:
        await adapter.create_issue(IssueRequest(summary="s", issue_type="Anything"))
    finally:
        await http.aclose()
    assert calls["n"] == 0


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
