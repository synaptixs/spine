"""Block C/D: CI / integration-checks seam.

After the per-issue features fan back in, the parent workflow runs the
cross-issue integration checks through a ``CIAdapter``. The stub always
passes; ``GHACIAdapter`` is the real thing — it awaits the **GitHub Actions
check runs** on each feature PR (the checks the branch push already
triggered) and fans their verdicts into one ``CIResult``. Merge-on-green
starts here.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from orchestrator.codereview.github_client import GitHubClient, GitHubClientError

logger = logging.getLogger("orchestrator.sdlc.ci")

_PR_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")


@dataclass(frozen=True)
class CIResult:
    """Outcome of the cross-issue integration checks."""

    passed: bool
    summary: str = ""
    issue_keys: list[str] = field(default_factory=list)


@runtime_checkable
class CIAdapter(Protocol):
    """Runs integration checks across the fanned-in issues.

    ``pr_urls`` carries the feature PRs the fan-out produced; adapters that
    don't need them (the stub) ignore the argument.
    """

    async def run_checks(self, *, issue_keys: list[str], pr_urls: list[str] | None = None) -> CIResult: ...


class StubCIAdapter:
    """Always-pass integration checks — the skeleton's no-op CI."""

    async def run_checks(self, *, issue_keys: list[str], pr_urls: list[str] | None = None) -> CIResult:
        _ = pr_urls
        return CIResult(passed=True, summary="stub integration checks", issue_keys=list(issue_keys))


class GHACIAdapter:
    """Await the GitHub Actions check runs on each feature PR.

    For every ``github.com/<owner>/<repo>/pull/<n>`` URL: resolve the head
    SHA, then poll its check runs until all complete (or ``timeout``).
    Passed = every check on every PR concluded success/neutral/skipped.

    ``require_checks`` guards the silent-green failure mode: a repo with no
    CI configured reports zero check runs, which must not count as a pass
    when the pipeline's merge gate depends on this verdict.
    """

    def __init__(
        self,
        github: GitHubClient,
        *,
        installation_id: int,
        poll_interval: float = 15.0,
        timeout: float = 1800.0,
        require_checks: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._github = github
        self._installation_id = installation_id
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._require_checks = require_checks
        self._sleep = sleep

    async def run_checks(self, *, issue_keys: list[str], pr_urls: list[str] | None = None) -> CIResult:
        targets = [(m.group(1), int(m.group(2))) for u in (pr_urls or []) if (m := _PR_URL_RE.search(u))]
        if not targets:
            return CIResult(
                passed=False,
                summary="no GitHub PR URLs to check — cannot attest integration",
                issue_keys=list(issue_keys),
            )

        lines: list[str] = []
        all_passed = True
        for repo, number in targets:
            passed, line = await self._await_pr_checks(repo, number)
            all_passed = all_passed and passed
            lines.append(line)
            logger.info("sdlc.ci.pr_checks", extra={"repo": repo, "pr": number, "passed": passed})
        return CIResult(passed=all_passed, summary="; ".join(lines), issue_keys=list(issue_keys))

    async def _await_pr_checks(self, repo: str, number: int) -> tuple[bool, str]:
        try:
            sha = await self._github.fetch_pr_head_sha(
                installation_id=self._installation_id, repo=repo, pr_number=number
            )
        except GitHubClientError as exc:
            return False, f"{repo}#{number}: head lookup failed ({exc})"

        waited = 0.0
        while True:
            try:
                runs = await self._github.fetch_check_runs(
                    installation_id=self._installation_id, repo=repo, ref=sha
                )
            except GitHubClientError as exc:
                return False, f"{repo}#{number}: check-run fetch failed ({exc})"

            if not runs:
                if not self._require_checks:
                    return True, f"{repo}#{number}: no checks configured (allowed)"
                if waited >= self._timeout:
                    return False, f"{repo}#{number}: no check runs appeared within {self._timeout:.0f}s"
            elif all(r.completed for r in runs):
                failed = [r.name for r in runs if not r.passed]
                if failed:
                    return False, f"{repo}#{number}: failed checks: {', '.join(failed)}"
                return True, f"{repo}#{number}: {len(runs)} check(s) green"
            elif waited >= self._timeout:
                pending = [r.name for r in runs if not r.completed]
                return False, f"{repo}#{number}: timed out waiting on: {', '.join(pending)}"

            await self._sleep(self._poll_interval)
            waited += self._poll_interval


__all__ = ["CIAdapter", "CIResult", "GHACIAdapter", "StubCIAdapter"]
