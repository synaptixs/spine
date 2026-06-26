"""Block A.5 unit tests: LLM reviewer parsing, submission building, and the
end-to-end ReviewService (mocked GitHub + mock LLM)."""

from __future__ import annotations

import json as jsonlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from orchestrator.codereview.auth import GitHubAppAuth
from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.codereview.github_client import (
    ChangedFile,
    GitHubClient,
    PRDiff,
    ReviewVerdict,
)
from orchestrator.codereview.reviewer import (
    LLMReviewer,
    ReviewService,
    build_review_submission,
)
from orchestrator.codereview.verifiers import Finding, Severity
from orchestrator.core.llm import CompletionResult, Message, MockLLMClient


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def _diff(patch: str = "@@ -0,0 +1,2 @@\n+line one\n+line two") -> PRDiff:
    return PRDiff(
        repo="acme/widgets",
        pr_number=7,
        head_sha="headsha",
        files=(
            ChangedFile(filename="app/main.py", status="modified", additions=2, deletions=0, patch=patch),
        ),
    )


# ---- LLMReviewer parsing --------------------------------------------------


async def test_llm_reviewer_parses_findings() -> None:
    payload = {
        "summary": "Looks mostly fine; one bug.",
        "findings": [
            {
                "path": "app/main.py",
                "line": 1,
                "severity": "blocker",
                "category": "correctness",
                "message": "Off-by-one.",
            },
            {
                "path": "app/main.py",
                "line": 2,
                "severity": "nit",
                "category": "clarity",
                "message": "Rename x.",
            },
        ],
    }
    reviewer = LLMReviewer(_llm_returning(jsonlib.dumps(payload)))
    summary, findings = await reviewer.review(_diff())
    assert summary == "Looks mostly fine; one bug."
    assert [f.severity for f in findings] == [Severity.BLOCKER, Severity.NIT]
    assert findings[0].verifier_id == "code_reviewer"
    assert findings[0].rule == "correctness"


async def test_llm_reviewer_tolerates_code_fence() -> None:
    payload = {"summary": "ok", "findings": []}
    fenced = "```json\n" + jsonlib.dumps(payload) + "\n```"
    reviewer = LLMReviewer(_llm_returning(fenced))
    summary, findings = await reviewer.review(_diff())
    assert summary == "ok"
    assert findings == []


async def test_llm_reviewer_degrades_on_garbage() -> None:
    reviewer = LLMReviewer(_llm_returning("not json at all"))
    summary, findings = await reviewer.review(_diff())
    assert findings == []
    assert "no parseable output" in summary


async def test_llm_reviewer_skips_malformed_findings() -> None:
    payload = {
        "summary": "s",
        "findings": [
            {"path": "app/main.py", "line": 1, "severity": "warning", "message": "valid"},
            {"line": 2, "message": "missing path"},  # dropped
            {"path": "x.py", "message": "missing line"},  # dropped
            {"path": "y.py", "line": 3, "message": ""},  # empty message dropped
        ],
    }
    reviewer = LLMReviewer(_llm_returning(jsonlib.dumps(payload)))
    _, findings = await reviewer.review(_diff())
    assert len(findings) == 1
    assert findings[0].message == "valid"


# ---- build_review_submission ----------------------------------------------


def test_submission_anchors_inline_vs_floating_and_blocks() -> None:
    diff = _diff()  # added lines at new-file lines 1 and 2
    findings = [
        Finding("code_reviewer", "correctness", Severity.BLOCKER, "app/main.py", 1, "Bug here."),
        Finding("code_reviewer", "design", Severity.WARNING, "app/main.py", 999, "Off-diff note."),
    ]
    sub = build_review_submission(diff, findings, "summary text")
    # Blocker present → REQUEST_CHANGES.
    assert sub.verdict is ReviewVerdict.REQUEST_CHANGES
    # Line 1 anchors inline; line 999 isn't in the diff → folded into body.
    assert len(sub.comments) == 1
    assert sub.comments[0].path == "app/main.py"
    assert sub.comments[0].line == 1
    assert "Off-diff note." in sub.summary
    assert "1 blocker" in sub.summary


def test_submission_warnings_only_does_not_block() -> None:
    diff = _diff()
    findings = [Finding("style", "trailing_whitespace", Severity.NIT, "app/main.py", 1, "ws")]
    sub = build_review_submission(diff, findings, "")
    assert sub.verdict is ReviewVerdict.COMMENT  # nits never block, never auto-approve


def test_submission_clean_diff_comments_not_approves() -> None:
    sub = build_review_submission(_diff(), [], "All good.")
    assert sub.verdict is ReviewVerdict.COMMENT
    assert sub.comments == []


# ---- ReviewService end-to-end ---------------------------------------------


def _private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _future() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


class _GitHubMock:
    def __init__(self, patch: str) -> None:
        self._patch = patch
        self.review_payload: dict[str, Any] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs", "expires_at": _future()})
        if path.endswith("/files"):
            page = int(request.url.params.get("page", "1"))
            if page > 1:
                return httpx.Response(200, json=[])
            file_entry = {
                "filename": "app/main.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch": self._patch,
            }
            return httpx.Response(200, json=[file_entry])
        if "/reviews" in path:
            self.review_payload = jsonlib.loads(request.content)
            return httpx.Response(200, json={"id": 1})
        if "/pulls/" in path:
            return httpx.Response(200, json={"head": {"sha": "headsha"}})
        return httpx.Response(404, json={})


def _service(mock: _GitHubMock, llm: MockLLMClient) -> tuple[ReviewService, httpx.AsyncClient, list[Any]]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock.handler), base_url="https://api.github.com")
    config = GitHubAppConfig(app_id="1", private_key=_private_pem())
    auth = GitHubAppAuth(config, http_client=http)
    github = GitHubClient(auth, config, http_client=http)
    audits: list[Any] = []

    async def _audit(action: str, resource_id: str, payload: dict[str, Any]) -> None:
        audits.append((action, resource_id, payload))

    svc = ReviewService(github=github, llm_reviewer=LLMReviewer(llm), audit=_audit)
    return svc, http, audits


async def test_review_service_posts_request_changes_on_secret() -> None:
    # A hardcoded secret on the added line → SecretsVerifier BLOCKER →
    # REQUEST_CHANGES, regardless of what the LLM says.
    patch = '@@ -0,0 +1 @@\n+token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
    mock = _GitHubMock(patch)
    llm = _llm_returning(jsonlib.dumps({"summary": "lgtm", "findings": []}))
    svc, http, audits = _service(mock, llm)
    try:
        sub = await svc.review_pull_request(installation_id=99, repo="acme/widgets", pr_number=7)
    finally:
        await http.aclose()

    assert sub.verdict is ReviewVerdict.REQUEST_CHANGES
    assert mock.review_payload is not None
    assert mock.review_payload["event"] == "REQUEST_CHANGES"
    # secret sits on an added line → inline comment present
    comment_bodies = " ".join(c["body"].lower() for c in mock.review_payload["comments"])
    assert "secret" in comment_bodies or "credential" in comment_bodies
    # audit fired with the verdict + counts
    assert audits and audits[0][0] == "pr_reviewed"
    assert audits[0][2]["verdict"] == "REQUEST_CHANGES"
    assert audits[0][2]["blocker"] >= 1


async def test_review_service_comments_on_clean_diff() -> None:
    patch = "@@ -0,0 +1 @@\n+x = 1"
    mock = _GitHubMock(patch)
    llm = _llm_returning(jsonlib.dumps({"summary": "clean", "findings": []}))
    svc, http, audits = _service(mock, llm)
    try:
        sub = await svc.review_pull_request(installation_id=1, repo="a/b", pr_number=1)
    finally:
        await http.aclose()
    assert sub.verdict is ReviewVerdict.COMMENT
    assert mock.review_payload is not None
    assert mock.review_payload["event"] == "COMMENT"


async def test_preview_does_not_post_or_audit() -> None:
    # The live-test-safe path: compute the review, never write to the PR.
    patch = '@@ -0,0 +1 @@\n+token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'
    mock = _GitHubMock(patch)
    llm = _llm_returning(jsonlib.dumps({"summary": "lgtm", "findings": []}))
    svc, http, audits = _service(mock, llm)
    try:
        diff, sub = await svc.preview_pull_request(installation_id=1, repo="a/b", pr_number=1)
    finally:
        await http.aclose()
    assert sub.verdict is ReviewVerdict.REQUEST_CHANGES  # secret still detected
    assert diff.head_sha == "headsha"
    assert mock.review_payload is None  # nothing posted
    assert audits == []  # nothing audited
