"""Block A.1: GitHub App webhook receiver.

Receives ``pull_request`` webhook events, verifies the HMAC-SHA256
signature GitHub stamps on every delivery, and — for review-worthy actions
(opened / reopened / synchronize on a non-draft PR) — records the request
in the audit log.

Day 1 scope: receive → verify → audit. The actual review (fetch diff →
run code_reviewer agent + verifier chain → post comments) is dispatched
from ``_dispatch_review``, which is a stub here and gets fleshed out in
Block A.5. Splitting it this way means the integration (signature, event
parsing, audit) is provable end-to-end before any GitHub API calls exist.

Signature verification is a standalone pure function so it's unit-testable
without a running app.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.codereview.models import PullRequestEvent
from orchestrator.registry.repositories import AuditLogRepo

logger = logging.getLogger("orchestrator.codereview.webhook")

router = APIRouter(prefix="/v1/github", tags=["codereview"])


def verify_signature(*, secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 check of a GitHub webhook delivery.

    GitHub sends ``X-Hub-Signature-256: sha256=<hexdigest>``. We recompute
    the digest over the raw body with the shared secret and compare in
    constant time. Returns False on any malformed / missing header rather
    than raising — the caller maps that to a 401.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@router.post(
    "/webhook",
    responses={
        202: {"description": "Review-worthy event accepted; review dispatched."},
        204: {"description": "Event received but not actionable (ignored)."},
        401: {"description": "Missing or invalid webhook signature."},
        503: {"description": "GitHub App webhook secret not configured."},
    },
)
async def github_webhook(
    request: Request,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    x_github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
) -> Response:
    config: GitHubAppConfig = request.app.state.github_app_config

    if not config.webhook_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App webhook secret not configured (GITHUB_APP_WEBHOOK_SECRET).",
        )

    body = await request.body()
    if not verify_signature(secret=config.webhook_secret, body=body, signature_header=x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature.")

    # GitHub fires a `ping` on install — answer the handshake.
    if x_github_event == "ping":
        return Response(content=json.dumps({"msg": "pong"}), media_type="application/json")

    # We only act on pull_request events; acknowledge everything else.
    if x_github_event != "pull_request":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON.") from exc

    event = PullRequestEvent.model_validate(payload)
    if not event.should_review:
        logger.info(
            "codereview.webhook.ignored",
            extra={"action": event.action, "draft": event.pull_request.draft},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await _record_review_requested(request, event, trace_id=x_github_delivery)
    await _dispatch_review(request, event, trace_id=x_github_delivery)
    return Response(status_code=status.HTTP_202_ACCEPTED)


async def _record_review_requested(
    request: Request, event: PullRequestEvent, *, trace_id: str | None
) -> None:
    """Write the ``pr_review_requested`` audit row.

    Opens its own short-lived session via the app's factory (same pattern
    the task runtime uses for verifier-audit). No-ops when no factory is
    bound — keeps unit tests that exercise the router without a DB working.
    """
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return
    target = event.review_target
    async with factory() as session:
        await AuditLogRepo(session).write(
            actor="github_app",
            action="pr_review_requested",
            resource_type="pull_request",
            resource_id=f"{target['repo']}#{target['pr_number']}",
            after={
                **target,
                "event_action": event.action,
                "title": event.pull_request.title,
                "author": event.pull_request.user.login,
                "html_url": event.pull_request.html_url,
            },
            trace_id=trace_id,
        )
        await session.commit()


async def _dispatch_review(request: Request, event: PullRequestEvent, *, trace_id: str | None) -> None:
    """Kick off the actual review (Block A.5).

    Builds a ReviewService from app state and runs it as a background task
    so the webhook returns 202 promptly — GitHub expects a fast webhook
    ack, and an LLM review takes seconds. When the App isn't configured for
    API calls (no app_id/private_key) or no LLM client is bound, we log and
    no-op: the audit row from ``_record_review_requested`` is still written.
    """
    config: GitHubAppConfig = request.app.state.github_app_config
    llm = getattr(request.app.state, "llm_client", None)
    if not config.api_configured or llm is None:
        logger.info(
            "codereview.webhook.review_skipped",
            extra={"target": event.review_target, "api_configured": config.api_configured},
        )
        return

    service = _build_review_service(request, trace_id=trace_id)
    target = event.review_target

    async def _run() -> None:
        try:
            await service.review_pull_request(
                installation_id=int(target["installation_id"]),
                repo=str(target["repo"]),
                pr_number=int(target["pr_number"]),
            )
        except Exception:  # noqa: BLE001 — background task; log and move on
            logger.exception("codereview.webhook.review_failed", extra={"target": target})

    # Fire-and-forget; keep a reference so the task isn't GC'd mid-flight.
    task = asyncio.create_task(_run())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# Strong refs to in-flight background reviews (asyncio only holds weak refs).
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _build_review_service(request: Request, *, trace_id: str | None) -> Any:
    """Construct a ReviewService wired to the app's config + LLM + audit.

    Imported lazily so the webhook module doesn't pull the reviewer (and its
    LLM/transitive deps) at import time for deployments that only receive
    webhooks without reviewing.
    """
    from orchestrator.codereview.auth import GitHubAppAuth
    from orchestrator.codereview.github_client import GitHubClient
    from orchestrator.codereview.reviewer import LLMReviewer, ReviewService

    config: GitHubAppConfig = request.app.state.github_app_config
    llm = request.app.state.llm_client
    auth = GitHubAppAuth(config)
    github = GitHubClient(auth, config)
    reviewer = LLMReviewer(llm)

    factory = getattr(request.app.state, "session_factory", None)
    actor = "github_app"

    async def _audit(action: str, resource_id: str, payload: dict[str, Any]) -> None:
        if factory is None:
            return
        async with factory() as session:
            await AuditLogRepo(session).write(
                actor=actor,
                action=action,
                resource_type="pull_request",
                resource_id=resource_id,
                after=payload,
                trace_id=trace_id,
            )
            await session.commit()

    return ReviewService(github=github, llm_reviewer=reviewer, audit=_audit)
