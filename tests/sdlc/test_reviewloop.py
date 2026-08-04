"""Review → fix → re-test → re-review, and the four ways it is allowed to stop.

The loop's value is not that it fixes things — a model will always produce *an* edit. It is
that it knows when to stop: out of rounds, nothing changed, the fix broke the tests, or the
finding was never the fixer's to act on. Each of those is asserted here, because a loop that
cannot stop is worse than no loop at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.verifiers import Finding, Severity
from orchestrator.sdlc.reviewloop import review_and_fix, worktree_diff


def _repo(tmp_path: Path, *, second_commit: str = "x = 2\n") -> Path:
    """A git repo with two commits, so ``git diff HEAD~1`` has something to say."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args: str) -> None:
        subprocess.run(args, cwd=repo, capture_output=True, check=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (repo / "mod.py").write_text(second_commit, encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "change")
    return repo


def _finding(message: str = "hardcoded secret", severity: Severity = Severity.BLOCKER) -> Finding:
    return Finding(verifier_id="test", rule="R1", severity=severity, path="mod.py", line=1, message=message)


class _Reviewer:
    """Returns queued finding-sets, one per round."""

    def __init__(self, rounds: list[list[Finding]]) -> None:
        self._rounds = list(rounds)
        self.calls = 0

    async def review(self, diff: PRDiff) -> tuple[str, list[Finding]]:
        self.calls += 1
        return ("summary", self._rounds.pop(0) if self._rounds else [])


class _Fixer:
    def __init__(self, edits: list[list[str]]) -> None:
        self._edits = list(edits)
        self.prompts: list[str] = []

    async def refine(self, *, spec: dict[str, Any], path: str, issue_key: str, failures: str) -> Any:
        self.prompts.append(failures)
        return SimpleNamespace(files=self._edits.pop(0) if self._edits else [], summary="fixed")


class _Tests:
    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)

    async def run(self, *, path: str) -> Any:
        return SimpleNamespace(passed=self._results.pop(0) if self._results else True, output="")


# ---- the happy case: found, fixed, proven ----------------------------------


async def test_a_finding_is_fixed_and_the_fix_is_proven(tmp_path: Path) -> None:
    """The acceptance criterion: a defect is found, fixed, re-tested and re-reviewed with no
    human in the loop."""
    repo = _repo(tmp_path)
    reviewer = _Reviewer([[_finding()], []])  # round 1 finds it, round 2 is clean
    fixer = _Fixer([["mod.py"]])
    tests = _Tests([True])

    result = await review_and_fix(
        path=repo, spec={}, issue_key="X-1", fixer=fixer, tests=tests, reviewer=reviewer
    )

    assert result.clean
    assert len(result.rounds) == 2
    assert result.rounds[0].fixed_files == ("mod.py",)
    assert result.rounds[0].tests_passed is True
    assert result.stopped == "review clean"
    # The fixer was handed the finding as data it can act on, not as prose.
    assert "mod.py:1" in fixer.prompts[0] and "blocker" in fixer.prompts[0]


async def test_a_clean_review_does_not_call_the_fixer(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fixer = _Fixer([])

    result = await review_and_fix(path=repo, spec={}, issue_key="X-1", fixer=fixer, reviewer=_Reviewer([[]]))

    assert result.clean and fixer.prompts == []


# ---- the four ways it stops ------------------------------------------------


async def test_a_fix_that_changes_nothing_ends_the_loop(tmp_path: Path) -> None:
    """The refine loop's rule: no edit means the next review sees the same code."""
    repo = _repo(tmp_path)
    reviewer = _Reviewer([[_finding()], [_finding()]])
    fixer = _Fixer([[]])  # edits nothing

    result = await review_and_fix(
        path=repo, spec={}, issue_key="X-1", fixer=fixer, reviewer=reviewer, max_rounds=3
    )

    assert not result.clean
    assert "edited nothing" in result.stopped
    assert len(result.rounds) == 1  # did not spend a second round watching the same finding
    assert reviewer.calls == 1


async def test_a_fix_that_breaks_the_tests_stops_the_loop(tmp_path: Path) -> None:
    """A green suite turned red by a review fix is a regression, not progress."""
    repo = _repo(tmp_path)
    result = await review_and_fix(
        path=repo,
        spec={},
        issue_key="X-1",
        fixer=_Fixer([["mod.py"]]),
        tests=_Tests([False]),
        reviewer=_Reviewer([[_finding()], []]),
        max_rounds=3,
    )

    assert not result.clean
    assert "broke the tests" in result.stopped
    assert result.rounds[-1].tests_passed is False


async def test_the_loop_is_bounded(tmp_path: Path) -> None:
    """A model that cannot fix something in two attempts rewrites more each time."""
    repo = _repo(tmp_path)
    reviewer = _Reviewer([[_finding()], [_finding()], [_finding()]])

    result = await review_and_fix(
        path=repo,
        spec={},
        issue_key="X-1",
        fixer=_Fixer([["mod.py"], ["mod.py"], ["mod.py"]]),
        tests=_Tests([True, True, True]),
        reviewer=reviewer,
        max_rounds=2,
    )

    assert len(result.rounds) == 2
    assert "budget spent after 2 round" in result.stopped
    assert reviewer.calls == 2


async def test_a_design_objection_is_deferred_not_patched(tmp_path: Path) -> None:
    """Asking a model to satisfy "the approach is wrong" produces a change that answers the
    words rather than the concern. It goes to a human with its evidence."""
    repo = _repo(tmp_path)
    objection = _finding("this belongs in another module — the design is wrong")
    fixer = _Fixer([])

    result = await review_and_fix(
        path=repo, spec={}, issue_key="X-1", fixer=fixer, reviewer=_Reviewer([[objection]])
    )

    assert fixer.prompts == []  # never handed to the fixer
    assert [f.message for f in result.deferred] == [objection.message]
    assert "for a human or a ticket" in result.render()


# ---- inputs and edges ------------------------------------------------------


async def test_advisory_findings_do_not_trigger_a_fix_cycle(tmp_path: Path) -> None:
    """A nit is real feedback for a human, not worth an LLM call and a re-test."""
    repo = _repo(tmp_path)
    fixer = _Fixer([])

    result = await review_and_fix(
        path=repo,
        spec={},
        issue_key="X-1",
        fixer=fixer,
        reviewer=_Reviewer([[_finding("prefer f-strings", Severity.NIT)]]),
    )

    assert fixer.prompts == []
    assert result.clean and "advisory" in result.stopped


async def test_the_diff_is_read_from_the_worktree(tmp_path: Path) -> None:
    """No pull request exists yet — that is the point of fixing things first."""
    diff = await worktree_diff(_repo(tmp_path))

    assert [f.filename for f in diff.files] == ["mod.py"]
    assert diff.pr_number == 0
    assert "x = 2" in diff.diff_text


async def test_a_missing_worktree_is_not_a_crash(tmp_path: Path) -> None:
    """A reaped or relocated worktree must not take the run down with it."""
    diff = await worktree_diff(tmp_path / "gone")
    assert diff.files == ()

    result = await review_and_fix(path=tmp_path / "gone", spec={}, issue_key="X-1")
    assert result.clean and "no diff" in result.stopped


async def test_findings_are_reported_when_no_fixer_is_wired(tmp_path: Path) -> None:
    result = await review_and_fix(
        path=_repo(tmp_path), spec={}, issue_key="X-1", reviewer=_Reviewer([[_finding()]])
    )

    assert not result.clean
    assert "no fixer wired" in result.stopped
    assert len(result.remaining) == 1


@pytest.mark.parametrize("severity", [Severity.BLOCKER, Severity.WARNING])
async def test_blockers_and_warnings_are_both_actionable(severity: Severity, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    fixer = _Fixer([["mod.py"]])

    await review_and_fix(
        path=repo,
        spec={},
        issue_key="X-1",
        fixer=fixer,
        tests=_Tests([True]),
        reviewer=_Reviewer([[_finding("x", severity)], []]),
    )

    assert len(fixer.prompts) == 1


async def test_changed_file_counts_are_derived_from_the_patch(tmp_path: Path) -> None:
    diff = await worktree_diff(_repo(tmp_path))
    (changed,) = diff.files
    assert isinstance(changed, ChangedFile)
    assert changed.additions == 1 and changed.deletions == 1
