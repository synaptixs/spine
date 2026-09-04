"""Close the tracker issue for a merged PR — the merge → Done bookend.

The linear ``sdlc feature`` path stops at an open PR for a human to review and merge;
this reconciles the tracker afterwards. It verifies the PR is merged (via ``gh``), derives
the issue key from the PR's head branch (``feat/<sdlc_id>/<KEY>``) unless one is given,
transitions the issue, comments the merge, and marks the backlog intent done.

One implementation, shared by ``orchestrator sdlc complete`` and the MCP plugin's
``sdlc_complete``: the CLI used to carry this inline with ``typer.Exit`` codes, which a
tool cannot return. The exits became :class:`CompleteError` with the same codes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class CompleteError(RuntimeError):
    """Why the issue was not closed. ``code`` keeps the CLI's exit codes: 1 a tool or the
    tracker failed, 2 no issue key could be derived, 3 the PR is not merged."""

    def __init__(self, message: str, *, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CompletionResult:
    issue: str
    pr: str
    merged: bool
    status: str
    backlog_done: bool


def issue_key_from_branch(branch: str) -> str | None:
    """Issue key from a feature branch ``feat/<sdlc_id>/<ISSUE-KEY>``."""
    parts = branch.split("/")
    if len(parts) >= 3 and parts[0] == "feat" and parts[-1]:
        return parts[-1]
    return None


async def _gh_pr_view(pr: str) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "pr",
        "view",
        pr,
        "--json",
        "state,mergedAt,headRefName",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    raw, _ = await proc.communicate()
    out = raw.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise CompleteError(f"gh pr view failed: {out[-300:]}", code=1)
    return dict(json.loads(out))


async def complete_issue_for_pr(
    pr: str,
    *,
    issue: str | None = None,
    status: str = "Done",
    allow_unmerged: bool = False,
    pr_view: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    tracker: Any = None,
) -> CompletionResult:
    """Transition the PR's issue to ``status`` and comment the merge.

    ``pr_view`` (→ the ``gh pr view`` JSON) and ``tracker`` (a ``JiraAdapter``-shaped
    object with ``transition_issue`` / ``comment_issue`` / ``aclose``) are injectable for
    tests; the defaults shell out to ``gh`` and build a **real, non-dry-run** Jira adapter,
    because closing the ticket is the whole point.
    """
    info = await (pr_view or _gh_pr_view)(pr)
    merged = bool(info.get("mergedAt")) or str(info.get("state", "")).upper() == "MERGED"
    if not merged and not allow_unmerged:
        raise CompleteError(
            f"PR {pr} is not merged (state={info.get('state')}). Pass allow_unmerged to override.", code=3
        )

    issue_key = issue or issue_key_from_branch(str(info.get("headRefName") or ""))
    if not issue_key:
        raise CompleteError("Could not derive the issue key from the PR branch; pass issue.", code=2)

    if tracker is None:
        from orchestrator.intake.jira import JiraAdapter, JiraConfig

        tracker = JiraAdapter(JiraConfig(dry_run=False))
    from orchestrator.intake.jira import IssueTrackerError

    try:
        moved = await tracker.transition_issue(issue_key, status)
        await tracker.comment_issue(issue_key, f"Merged via {pr}.")
    except IssueTrackerError as exc:
        raise CompleteError(str(exc), code=1) from exc
    finally:
        await tracker.aclose()

    # Mark the backlog intent done (done = PR merged) and refresh the local ledger.
    backlog_done = False
    if merged:
        from orchestrator.intake.backlog_doc import backlog_path, write_backlog
        from orchestrator.intake.cache import complete_by_pr, load_progress

        matched = complete_by_pr(pr)
        if matched is not None:
            src, plan = matched
            write_backlog(backlog_path(), src, plan, load_progress(src))
            backlog_done = True

    return CompletionResult(
        issue=issue_key, pr=pr, merged=merged, status=moved or status, backlog_done=backlog_done
    )


__all__ = ["CompleteError", "CompletionResult", "complete_issue_for_pr", "issue_key_from_branch"]
