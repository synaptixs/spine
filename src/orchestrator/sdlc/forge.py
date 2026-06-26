"""Block C: pull-request seam.

The feature pipeline opens a PR for the generated change through a
``PRAdapter``. The Block-C default is a stub that returns a synthetic URL and
makes no GitHub call; Block D wires this to the real GitHub client from
``orchestrator.codereview`` so the worktree branch becomes a real PR.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger("orchestrator.sdlc.forge")


class PRError(RuntimeError):
    """A real PR open failed (git or gh returned non-zero)."""


@dataclass(frozen=True)
class PRResult:
    """A pull request opened (or simulated) for one issue's change."""

    url: str
    number: int | None = None


@dataclass(frozen=True)
class MergeResult:
    """Outcome of merging one PR — merge-on-green's terminal action."""

    merged: bool
    url: str
    detail: str = ""


@dataclass(frozen=True)
class PRComment:
    """One human review comment on a PR (the unit the agent responds to)."""

    author: str
    body: str
    # "review" (a submitted review's summary), "inline" (a code comment), or
    # "issue" (a plain PR-thread comment) — kept for context, not behavior.
    kind: str = "issue"


@runtime_checkable
class PRAdapter(Protocol):
    """Opens, merges, and responds to feedback on the PR for one change."""

    async def open_pr(self, *, issue_key: str, path: str, branch: str, title: str, body: str) -> PRResult: ...

    async def merge_pr(self, *, pr_url: str) -> MergeResult: ...

    async def fetch_review_comments(
        self, *, pr_url: str, exclude_author: str | None = None
    ) -> list[PRComment]: ...

    async def push_followup(self, *, path: str, branch: str, message: str) -> bool: ...


class StubPRAdapter:
    """Returns a synthetic PR URL — no real GitHub write."""

    def __init__(self, base_url: str = "https://stub.github/pr") -> None:
        self._base_url = base_url.rstrip("/")

    async def open_pr(self, *, issue_key: str, path: str, branch: str, title: str, body: str) -> PRResult:
        _ = (path, branch, title, body)
        return PRResult(url=f"{self._base_url}/{issue_key}")

    async def merge_pr(self, *, pr_url: str) -> MergeResult:
        return MergeResult(merged=True, url=pr_url, detail="stub merge")

    async def fetch_review_comments(
        self, *, pr_url: str, exclude_author: str | None = None
    ) -> list[PRComment]:
        _ = (pr_url, exclude_author)
        return []

    async def push_followup(self, *, path: str, branch: str, message: str) -> bool:
        _ = (path, branch, message)
        return False


class GhPRAdapter:
    """Real PR via ``git`` + the authenticated ``gh`` CLI — no shell.

    Runs entirely inside the worktree at ``path`` (already on ``branch``,
    created by ``WorkspaceManager``): stage everything, commit if there's
    anything to commit, push the branch to ``origin``, then ``gh pr create``.
    Every call goes through ``create_subprocess_exec`` with an explicit argv
    list, so a worktree path / branch name can't smuggle shell metacharacters.

    ``gh`` must already be authenticated (``gh auth status``); the worktree's
    ``origin`` must point at the GitHub repo the PR targets.
    """

    def __init__(
        self,
        *,
        base_branch: str | None = None,
        commit_prefix: str = "",
        timeout: float = 120.0,
    ) -> None:
        # ``base_branch`` is the PR target; None lets gh use the repo default.
        self._base_branch = base_branch
        self._commit_prefix = commit_prefix
        self._timeout = timeout

    async def open_pr(self, *, issue_key: str, path: str, branch: str, title: str, body: str) -> PRResult:
        await self._git(path, "add", "-A")
        # Commit only if the index has staged changes; an empty commit would
        # fail and a no-op push/PR on an unchanged branch is pointless.
        if await self._has_staged_changes(path):
            message = f"{self._commit_prefix}{title}".strip()
            await self._git(path, "commit", "-m", message)
        await self._git(path, "push", "-u", "origin", branch)

        args = ["pr", "create", "--head", branch, "--title", title, "--body", body]
        if self._base_branch:
            args += ["--base", self._base_branch]
        url = (await self._gh(path, *args)).strip().splitlines()[-1].strip()
        number = await self._pr_number(path, branch)
        logger.info("sdlc.forge.pr_opened", extra={"issue_key": issue_key, "url": url})
        return PRResult(url=url, number=number)

    async def merge_pr(self, *, pr_url: str) -> MergeResult:
        """Merge a green, approved PR via ``gh pr merge <url> --merge``.

        ``gh`` resolves the repo from the URL, so no worktree cwd is needed.
        Fails closed: any non-zero exit (checks pending, conflicts, missing
        permissions) returns ``merged=False`` with gh's message.
        """
        rc, out = await self._exec("gh", "pr", "merge", pr_url, "--merge", cwd=".")
        if rc != 0:
            logger.warning("sdlc.forge.merge_failed", extra={"url": pr_url})
            return MergeResult(merged=False, url=pr_url, detail=out.strip()[-300:])
        return MergeResult(merged=True, url=pr_url, detail="merged")

    async def fetch_review_comments(
        self, *, pr_url: str, exclude_author: str | None = None
    ) -> list[PRComment]:
        """Human review feedback on the PR: submitted-review summaries, inline
        code comments, and plain PR-thread comments.

        ``gh pr view --json`` resolves the repo from the URL. The agent's own
        comments (``exclude_author``) and empty bodies are filtered out so the
        agent never "responds" to itself. Read-only; returns [] on any gh error.
        """
        try:
            out = await self._gh(".", "pr", "view", pr_url, "--json", "comments,reviews")
            data = json.loads(out)
        except (PRError, json.JSONDecodeError):
            logger.warning("sdlc.forge.fetch_comments_failed", extra={"url": pr_url})
            return []

        excl = (exclude_author or "").lower()
        comments: list[PRComment] = []
        for review in data.get("reviews") or []:
            if (c := _comment_from(review, kind="review", excl=excl)) is not None:
                comments.append(c)
        for comment in data.get("comments") or []:
            if (c := _comment_from(comment, kind="issue", excl=excl)) is not None:
                comments.append(c)
        return comments

    async def push_followup(self, *, path: str, branch: str, message: str) -> bool:
        """Commit any worktree changes and push to ``branch`` — a follow-up to
        an open PR. Returns True if something was pushed, False if nothing was
        staged (no change to the PR).
        """
        await self._git(path, "add", "-A")
        if not await self._has_staged_changes(path):
            return False
        await self._git(path, "commit", "-m", f"{self._commit_prefix}{message}".strip())
        await self._git(path, "push", "origin", branch)
        logger.info("sdlc.forge.followup_pushed", extra={"branch": branch})
        return True

    async def _has_staged_changes(self, path: str) -> bool:
        # ``diff --cached --quiet`` exits 1 when there are staged changes.
        rc, _out = await self._exec("git", "diff", "--cached", "--quiet", cwd=path)
        return rc != 0

    async def _pr_number(self, path: str, branch: str) -> int | None:
        try:
            out = await self._gh(path, "pr", "view", branch, "--json", "number")
            return int(json.loads(out)["number"])
        except (PRError, KeyError, ValueError, json.JSONDecodeError):
            return None

    async def _git(self, path: str, *args: str) -> str:
        rc, out = await self._exec("git", *args, cwd=path)
        if rc != 0:
            raise PRError(f"git {args[0]} failed (rc={rc}): {out.strip()[-500:]}")
        return out

    async def _gh(self, path: str, *args: str) -> str:
        rc, out = await self._exec("gh", *args, cwd=path)
        if rc != 0:
            raise PRError(f"gh {args[0]} failed (rc={rc}): {out.strip()[-500:]}")
        return out

    async def _exec(self, *argv: str, cwd: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise PRError(f"{argv[0]} timed out after {self._timeout}s") from None
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, stdout_bytes.decode("utf-8", "replace")


# GitHub usernames of bots whose PR comments are never actionable feedback.
_BOT_AUTHORS = frozenset({"github-actions[bot]", "codecov[bot]", "dependabot[bot]"})


def _comment_from(raw: dict[str, object], *, kind: str, excl: str) -> PRComment | None:
    """Build a ``PRComment`` from a gh JSON node, or None if it should drop.

    Drops empty bodies, the agent's own comments (``excl``), CI/bot noise, and
    a review node that only approved with no written summary.
    """
    author_obj = raw.get("author")
    author = ""
    if isinstance(author_obj, dict):
        author = str(author_obj.get("login") or "")
    body = str(raw.get("body") or "").strip()
    if not body or not author:
        return None
    if author.lower() == excl or author in _BOT_AUTHORS:
        return None
    return PRComment(author=author, body=body, kind=kind)


def format_review_feedback(comments: list[PRComment]) -> str:
    """Render human PR comments as a refine-ready feedback block (or '').

    Pure and deterministic so it can be unit-tested without GitHub. The block
    is fed to codegen the same way a test failure is — "here is what's wrong,
    address it" — so the agent revises the change to satisfy the reviewer.
    """
    if not comments:
        return ""
    lines = [
        "Human reviewers left the following comments on the open pull request. "
        "Address every actionable request and revise the change accordingly:"
    ]
    for c in comments:
        lines.append(f"- @{c.author} ({c.kind}): {c.body}")
    return "\n".join(lines)


__all__ = [
    "GhPRAdapter",
    "MergeResult",
    "PRAdapter",
    "PRComment",
    "PRError",
    "PRResult",
    "StubPRAdapter",
    "format_review_feedback",
]
