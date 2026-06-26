"""Block A.1 unit tests: GitHub webhook signature + event handling.

Drives the webhook router through a real FastAPI app (no DB needed — the
audit write no-ops when no session factory is bound) with HMAC-signed
payloads. Signature verification is also tested as a pure function.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.codereview.models import PullRequestEvent
from orchestrator.codereview.webhook import verify_signature
from orchestrator.core.llm import MockLLMClient
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

WEBHOOK_SECRET = "test-webhook-secret"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(*, action: str = "opened", draft: bool = False, number: int = 7) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "title": "Add CSV export",
            "state": "open",
            "draft": draft,
            "html_url": f"https://github.com/acme/widgets/pull/{number}",
            "diff_url": f"https://github.com/acme/widgets/pull/{number}.diff",
            "head": {"ref": "feature/csv", "sha": "abc123"},
            "base": {"ref": "main", "sha": "def456"},
            "user": {"login": "alice"},
        },
        "repository": {
            "full_name": "acme/widgets",
            "name": "widgets",
            "owner": {"login": "acme"},
        },
        "installation": {"id": 99},
    }


# ---- pure-function signature tests ----------------------------------------


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello": "world"}'
    sig = _sign(WEBHOOK_SECRET, body)
    assert verify_signature(secret=WEBHOOK_SECRET, body=body, signature_header=sig) is True


def test_verify_signature_rejects_tampered_body() -> None:
    sig = _sign(WEBHOOK_SECRET, b'{"hello": "world"}')
    assert verify_signature(secret=WEBHOOK_SECRET, body=b'{"hello": "evil"}', signature_header=sig) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"payload"
    sig = _sign("other-secret", body)
    assert verify_signature(secret=WEBHOOK_SECRET, body=body, signature_header=sig) is False


def test_verify_signature_rejects_missing_or_malformed_header() -> None:
    body = b"payload"
    assert verify_signature(secret=WEBHOOK_SECRET, body=body, signature_header=None) is False
    assert verify_signature(secret=WEBHOOK_SECRET, body=body, signature_header="md5=deadbeef") is False


# ---- model tests ----------------------------------------------------------


def test_event_should_review_for_opened_nondraft() -> None:
    event = PullRequestEvent.model_validate(_pr_payload(action="opened"))
    assert event.should_review is True
    assert event.review_target == {
        "repo": "acme/widgets",
        "pr_number": 7,
        "head_sha": "abc123",
        "installation_id": 99,
    }


def test_event_skips_draft_and_noise_actions() -> None:
    assert PullRequestEvent.model_validate(_pr_payload(draft=True)).should_review is False
    assert PullRequestEvent.model_validate(_pr_payload(action="labeled")).should_review is False
    assert PullRequestEvent.model_validate(_pr_payload(action="closed")).should_review is False


# ---- router tests ---------------------------------------------------------
#
# These are unit tests: we drive the ASGI app directly via ASGITransport
# *without* running the lifespan, so no DB engine / session_factory is bound.
# The webhook's audit write no-ops when no factory is present (guarded), so
# the 202 path is exercised without needing Postgres. The real audit-row
# write is covered by an integration test in Block A.5.


def _app(*, enabled: bool = True) -> Any:
    # _env_file=None keeps app_id/private_key empty (api_configured=False) so
    # the 202 path's _dispatch_review no-ops instead of firing a real review
    # off a developer's local .env creds. These tests probe webhook plumbing,
    # not live review dispatch.
    return create_app(
        Settings(api_key="k"),
        llm_client=MockLLMClient(),
        github_app_config=GitHubAppConfig(
            enabled=enabled,
            webhook_secret=WEBHOOK_SECRET,
            _env_file=None,  # type: ignore[call-arg]
        ),
    )


async def _post(app: Any, *, body: bytes, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/github/webhook", content=body, headers=headers)


def _headers(body: bytes, *, event: str, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": _sign(secret, body),
        "X-GitHub-Delivery": "delivery-1",
        "Content-Type": "application/json",
    }


async def test_webhook_accepts_signed_pull_request_event() -> None:
    body = json.dumps(_pr_payload()).encode()
    resp = await _post(_app(), body=body, headers=_headers(body, event="pull_request"))
    assert resp.status_code == 202


async def test_webhook_rejects_bad_signature() -> None:
    body = json.dumps(_pr_payload()).encode()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256=deadbeef",
        "Content-Type": "application/json",
    }
    resp = await _post(_app(), body=body, headers=headers)
    assert resp.status_code == 401


async def test_webhook_ping_returns_pong() -> None:
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    resp = await _post(_app(), body=body, headers=_headers(body, event="ping"))
    assert resp.status_code == 200
    assert resp.json() == {"msg": "pong"}


async def test_webhook_ignores_draft_pr_with_204() -> None:
    body = json.dumps(_pr_payload(draft=True)).encode()
    resp = await _post(_app(), body=body, headers=_headers(body, event="pull_request"))
    assert resp.status_code == 204


async def test_webhook_ignores_non_pr_event_with_204() -> None:
    body = json.dumps({"some": "issue_comment payload"}).encode()
    resp = await _post(_app(), body=body, headers=_headers(body, event="issue_comment"))
    assert resp.status_code == 204


async def test_webhook_router_absent_when_disabled() -> None:
    body = json.dumps(_pr_payload()).encode()
    resp = await _post(_app(enabled=False), body=body, headers=_headers(body, event="pull_request"))
    assert resp.status_code == 404  # router not mounted
