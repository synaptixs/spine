"""Respond to human review comments on a live PR (persona: be a colleague).

The autonomous loop stops at an opened PR; a human then reviews it. A real
engineer reads the review comments, revises the change, and pushes — they
don't make the reviewer re-explain. This closes that half of the loop: fetch
the PR's human comments, feed them to codegen as feedback, re-drive the change
to green (tests + CI-parity preflight), and push the fix to the PR branch.

This runs out-of-band (human-triggered) against a worktree already checked out
to the PR branch — it is NOT part of the A→Z autonomous run, where the merge
gate is the human review point. Everything funnels through the same ``SDLCDeps``
adapters as the pipeline, so it is fully exercised by the same stubs/fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.forge import format_review_feedback

logger = logging.getLogger("orchestrator.sdlc.review_response")


@dataclass(frozen=True)
class ReviewResponse:
    """Outcome of one pass at addressing a PR's human review comments."""

    comments: int  # human comments found
    addressed: bool  # a fix was pushed to the PR branch
    green: bool  # tests + preflight passed after refining
    refines: int  # codegen refine cycles spent
    detail: str = ""


async def respond_to_pr_feedback(
    deps: SDLCDeps,
    *,
    pr_url: str,
    branch: str,
    path: str,
    issue_key: str = "",
    spec: dict[str, Any] | None = None,
    bot_login: str | None = None,
    max_refines: int = 3,
) -> ReviewResponse:
    """Address the PR's human comments and push a fix to ``branch``.

    ``path`` is a worktree already on the PR branch. Returns a structured
    result; pushes only when the revised change passes tests + preflight.
    """
    spec = spec or {}
    comments = await deps.pr.fetch_review_comments(pr_url=pr_url, exclude_author=bot_login)
    if not comments:
        return ReviewResponse(
            comments=0, addressed=False, green=False, refines=0, detail="no human comments to address"
        )

    feedback = format_review_feedback(comments)
    logger.info("sdlc.review_response.addressing", extra={"url": pr_url, "comments": len(comments)})

    # First refine is seeded with the reviewer feedback; subsequent ones with
    # whatever tests/preflight then complain about (a fix can break them).
    failures = feedback
    refines = 0
    green = False
    last = ""
    while refines < max(1, max_refines):
        await _refine(deps, spec=spec, path=path, issue_key=issue_key, failures=failures, run_key=pr_url)
        refines += 1
        test = await deps.tests.run(path=path)
        if not test.passed:
            last = test.output
            failures = last
            continue
        pre = await deps.preflight.run(path=path)
        if pre.passed:
            green = True
            break
        last = pre.output
        failures = last

    if not green:
        return ReviewResponse(
            comments=len(comments),
            addressed=False,
            green=False,
            refines=refines,
            detail=f"could not reach green after {refines} refine(s): {last[-200:]}",
        )

    pushed = await deps.pr.push_followup(
        path=path, branch=branch, message=f"Address review feedback ({len(comments)} comment(s))"
    )
    return ReviewResponse(
        comments=len(comments),
        addressed=pushed,
        green=True,
        refines=refines,
        detail="pushed fix to PR branch" if pushed else "no change after refine",
    )


async def _refine(
    deps: SDLCDeps, *, spec: dict[str, Any], path: str, issue_key: str, failures: str, run_key: str
) -> None:
    """Run codegen.refine under the per-run budget, if one is configured."""
    budget = deps.budget
    if budget is None:
        await deps.codegen.refine(spec=spec, path=path, issue_key=issue_key, failures=failures)
        return
    with budget.activate(run_key):
        await deps.codegen.refine(spec=spec, path=path, issue_key=issue_key, failures=failures)


__all__ = ["ReviewResponse", "respond_to_pr_feedback"]
