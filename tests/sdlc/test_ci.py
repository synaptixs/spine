"""GHACIAdapter: await real check runs on feature PRs (Track 4.2)."""

from __future__ import annotations

from typing import Any

from orchestrator.codereview.github_client import CheckRun, GitHubClientError
from orchestrator.sdlc.ci import GHACIAdapter, StubCIAdapter

PR = "https://github.com/acme/app/pull/7"


class _FakeGitHub:
    """Scripted check-run pages: one list per poll, last page repeats."""

    def __init__(self, pages: list[list[CheckRun]], *, sha: str = "abc123", head_error: bool = False) -> None:
        self._pages = pages
        self._sha = sha
        self._head_error = head_error
        self.polls = 0

    async def fetch_pr_head_sha(self, *, installation_id: int, repo: str, pr_number: int) -> str:
        if self._head_error:
            raise GitHubClientError("boom")
        return self._sha

    async def fetch_check_runs(self, *, installation_id: int, repo: str, ref: str) -> list[CheckRun]:
        assert ref == self._sha
        page = self._pages[min(self.polls, len(self._pages) - 1)]
        self.polls += 1
        return page


async def _no_sleep(_: float) -> None:
    return None


def _adapter(gh: _FakeGitHub, **kwargs: Any) -> GHACIAdapter:
    defaults: dict[str, Any] = {
        "installation_id": 1,
        "poll_interval": 1.0,
        "timeout": 3.0,
        "sleep": _no_sleep,
    }
    return GHACIAdapter(gh, **{**defaults, **kwargs})  # type: ignore[arg-type]


def _run(name: str, status: str, conclusion: str = "") -> CheckRun:
    return CheckRun(name=name, status=status, conclusion=conclusion)


async def test_all_green_passes() -> None:
    gh = _FakeGitHub([[_run("test", "completed", "success"), _run("lint", "completed", "skipped")]])
    result = await _adapter(gh).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert result.passed and "2 check(s) green" in result.summary


async def test_pending_then_green_polls_until_complete() -> None:
    gh = _FakeGitHub(
        [
            [_run("test", "in_progress")],
            [_run("test", "completed", "success")],
        ]
    )
    result = await _adapter(gh).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert result.passed and gh.polls == 2


async def test_failed_check_fails_with_name() -> None:
    gh = _FakeGitHub([[_run("test", "completed", "failure")]])
    result = await _adapter(gh).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert not result.passed and "failed checks: test" in result.summary


async def test_timeout_on_never_completing_check() -> None:
    gh = _FakeGitHub([[_run("test", "in_progress")]])
    result = await _adapter(gh).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert not result.passed and "timed out waiting on: test" in result.summary


async def test_no_checks_fails_by_default_but_can_be_allowed() -> None:
    strict = await _adapter(_FakeGitHub([[]])).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert not strict.passed and "no check runs appeared" in strict.summary

    lax = await _adapter(_FakeGitHub([[]]), require_checks=False).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert lax.passed and "no checks configured" in lax.summary


async def test_no_pr_urls_cannot_attest() -> None:
    result = await _adapter(_FakeGitHub([[]])).run_checks(issue_keys=["E-1"], pr_urls=[])
    assert not result.passed and "cannot attest" in result.summary


async def test_head_lookup_failure_fails_that_pr() -> None:
    gh = _FakeGitHub([[]], head_error=True)
    result = await _adapter(gh).run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert not result.passed and "head lookup failed" in result.summary


async def test_multiple_prs_all_must_pass() -> None:
    class _TwoPR(_FakeGitHub):
        async def fetch_check_runs(self, *, installation_id: int, repo: str, ref: str) -> list[CheckRun]:
            self.polls += 1
            return [_run("test", "completed", "success" if self.polls == 1 else "failure")]

    gh = _TwoPR([])
    result = await _adapter(gh).run_checks(
        issue_keys=["E-1", "E-2"],
        pr_urls=[PR, "https://github.com/acme/app/pull/8"],
    )
    assert not result.passed  # second PR's failure sinks the set


async def test_stub_still_conforms_with_pr_urls() -> None:
    result = await StubCIAdapter().run_checks(issue_keys=["E-1"], pr_urls=[PR])
    assert result.passed
