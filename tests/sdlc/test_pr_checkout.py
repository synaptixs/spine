"""checkout_pr_worktree: the clone-and-checkout dance, once, injectable for tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.review_response import PRCheckoutError, checkout_pr_worktree


async def test_checkout_clones_then_checks_out_the_pr_and_reports_the_branch() -> None:
    seen: list[tuple[str, ...]] = []

    async def run(*argv: str, cwd: str) -> str:
        seen.append(argv)
        if argv[:2] == ("git", "rev-parse"):
            return "feat/abc/PROJ-1\n"
        return ""

    workdir, branch = await checkout_pr_worktree("https://gh/o/r.git", "https://gh/o/r/pull/3", run=run)
    assert branch == "feat/abc/PROJ-1"
    assert Path(workdir).name == "wt" and Path(workdir).exists()
    assert seen[0][:3] == ("git", "clone", "--quiet") and seen[0][3] == "https://gh/o/r.git"
    assert seen[1] == ("gh", "pr", "checkout", "https://gh/o/r/pull/3")


async def test_a_failing_step_names_itself() -> None:
    async def run(*argv: str, cwd: str) -> str:
        if argv[0] == "gh":
            raise PRCheckoutError("gh", "not logged in")
        return ""

    with pytest.raises(PRCheckoutError) as info:
        await checkout_pr_worktree("u", "p", run=run)
    assert info.value.step == "gh" and "not logged in" in str(info.value)
