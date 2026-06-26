"""Block A.3 unit tests: GitHubClient diff fetch + review submit.

A single httpx MockTransport routes every endpoint the client touches —
the installation-token mint (via the shared GitHubAppAuth), the PR detail
(head SHA), the paginated files list, and the review POST. No network.
"""

from __future__ import annotations

import json as jsonlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from orchestrator.codereview.auth import GitHubAppAuth
from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.codereview.github_client import (
    GitHubClient,
    GitHubClientError,
    ReviewComment,
    ReviewSubmission,
    ReviewVerdict,
)


def _private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _future() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def _file(name: str, *, status: str = "modified", patch: str = "@@ -1 +1 @@\n-old\n+new") -> dict[str, Any]:
    return {"filename": name, "status": status, "additions": 1, "deletions": 1, "patch": patch}


class _GitHubMock:
    """Routes the GitHub endpoints the client hits. Tracks the review POST body."""

    def __init__(self, *, files_pages: list[list[dict[str, Any]]] | None = None) -> None:
        self.files_pages = files_pages if files_pages is not None else [[_file("app/main.py")]]
        self.review_payload: dict[str, Any] | None = None
        self.review_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_tok", "expires_at": _future()})
        if path.endswith("/files"):
            page = int(request.url.params.get("page", "1"))
            batch = self.files_pages[page - 1] if page - 1 < len(self.files_pages) else []
            return httpx.Response(200, json=batch)
        if "/pulls/" in path and "/reviews" in path:
            self.review_payload = jsonlib.loads(request.content)
            return httpx.Response(self.review_status, json={"id": 555, "state": "COMMENTED"})
        if "/pulls/" in path:  # PR detail → head sha
            return httpx.Response(200, json={"head": {"sha": "headsha123"}})
        return httpx.Response(404, json={"message": f"unrouted {path}"})


def _client_for(mock: _GitHubMock) -> tuple[GitHubClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock.handler), base_url="https://api.github.com")
    config = GitHubAppConfig(app_id="1", private_key=_private_pem())
    auth = GitHubAppAuth(config, http_client=http)
    return GitHubClient(auth, config, http_client=http), http


async def test_fetch_pr_diff_returns_files_and_head_sha() -> None:
    mock = _GitHubMock(files_pages=[[_file("app/main.py"), _file("app/util.py", status="added")]])
    client, http = _client_for(mock)
    try:
        diff = await client.fetch_pr_diff(installation_id=99, repo="acme/widgets", pr_number=7)
    finally:
        await http.aclose()

    assert diff.head_sha == "headsha123"
    assert [f.filename for f in diff.files] == ["app/main.py", "app/util.py"]
    assert diff.truncated is False
    assert "app/main.py (modified)" in diff.diff_text
    assert "app/util.py (added)" in diff.diff_text


async def test_fetch_pr_diff_paginates_and_flags_truncation() -> None:
    # 3 full pages of 100 → hits the page cap → truncated=True.
    full_page = [_file(f"f{i}.py") for i in range(100)]
    mock = _GitHubMock(files_pages=[full_page, full_page, full_page, [_file("overflow.py")]])
    client, http = _client_for(mock)
    try:
        diff = await client.fetch_pr_diff(installation_id=1, repo="a/b", pr_number=1)
    finally:
        await http.aclose()

    assert len(diff.files) == 300  # 3 pages × 100, capped
    assert diff.truncated is True


async def test_fetch_pr_diff_stops_on_short_page() -> None:
    mock = _GitHubMock(files_pages=[[_file("only.py")]])  # single short page
    client, http = _client_for(mock)
    try:
        diff = await client.fetch_pr_diff(installation_id=1, repo="a/b", pr_number=1)
    finally:
        await http.aclose()
    assert len(diff.files) == 1
    assert diff.truncated is False


async def test_submit_review_posts_verdict_and_comments() -> None:
    mock = _GitHubMock()
    client, http = _client_for(mock)
    submission = ReviewSubmission(
        verdict=ReviewVerdict.REQUEST_CHANGES,
        summary="One blocker, one nit.",
        comments=[
            ReviewComment(path="app/main.py", line=12, body="Hardcoded secret."),
            ReviewComment(path="app/util.py", line=3, body="Prefer f-string."),
        ],
    )
    try:
        result = await client.submit_review(
            installation_id=99,
            repo="acme/widgets",
            pr_number=7,
            head_sha="headsha123",
            submission=submission,
        )
    finally:
        await http.aclose()

    assert result["id"] == 555
    assert mock.review_payload is not None
    assert mock.review_payload["event"] == "REQUEST_CHANGES"
    assert mock.review_payload["commit_id"] == "headsha123"
    assert mock.review_payload["body"] == "One blocker, one nit."
    assert len(mock.review_payload["comments"]) == 2
    assert mock.review_payload["comments"][0] == {
        "path": "app/main.py",
        "line": 12,
        "side": "RIGHT",
        "body": "Hardcoded secret.",
    }


async def test_submit_review_raises_on_api_error() -> None:
    mock = _GitHubMock()
    mock.review_status = 422  # e.g. comment anchored outside the diff
    client, http = _client_for(mock)
    try:
        with pytest.raises(GitHubClientError, match="submit_review failed"):
            await client.submit_review(
                installation_id=1,
                repo="a/b",
                pr_number=1,
                head_sha="sha",
                submission=ReviewSubmission(verdict=ReviewVerdict.COMMENT, summary="x"),
            )
    finally:
        await http.aclose()
