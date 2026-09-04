"""The merge → Done bookend as an engine function, shared by the CLI and the plugin."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.intake.jira import IssueTrackerError
from orchestrator.sdlc.complete import CompleteError, complete_issue_for_pr, issue_key_from_branch


def test_issue_key_from_branch() -> None:
    assert issue_key_from_branch("feat/f32ef54d82f34aae/PROJ-27") == "PROJ-27"
    assert issue_key_from_branch("main") is None
    assert issue_key_from_branch("feat/x/") is None


class _Tracker:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.closed = False
        self.fail = fail

    async def transition_issue(self, key: str, status: str) -> str:
        self.calls.append(("transition", key, status))
        if self.fail:
            raise IssueTrackerError("jira said no")
        return status

    async def comment_issue(self, key: str, body: str) -> None:
        self.calls.append(("comment", key, body))

    async def aclose(self) -> None:
        self.closed = True


def _view(**info: Any) -> Any:
    async def pr_view(pr: str) -> dict[str, Any]:
        return dict(info)

    return pr_view


@pytest.fixture(autouse=True)
def _no_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.intake.cache.complete_by_pr", lambda pr: None)


async def test_a_merged_pr_closes_its_issue_and_comments_the_merge() -> None:
    tracker = _Tracker()
    result = await complete_issue_for_pr(
        "https://gh/o/r/pull/7",
        pr_view=_view(state="MERGED", mergedAt="2026-09-04T00:00:00Z", headRefName="feat/abc/PROJ-9"),
        tracker=tracker,
    )
    assert result.issue == "PROJ-9" and result.merged and result.status == "Done"
    assert ("transition", "PROJ-9", "Done") in tracker.calls
    assert any(c[0] == "comment" and "pull/7" in c[2] for c in tracker.calls)
    assert tracker.closed  # always, even on the happy path


async def test_an_unmerged_pr_is_refused_unless_allowed() -> None:
    view = _view(state="OPEN", mergedAt=None, headRefName="feat/abc/PROJ-9")
    with pytest.raises(CompleteError) as info:
        await complete_issue_for_pr("pr", pr_view=view, tracker=_Tracker())
    assert info.value.code == 3
    result = await complete_issue_for_pr("pr", pr_view=view, tracker=_Tracker(), allow_unmerged=True)
    assert result.merged is False and result.issue == "PROJ-9"


async def test_no_derivable_key_is_a_code_2_and_an_explicit_key_wins() -> None:
    view = _view(state="MERGED", mergedAt="x", headRefName="hotfix")
    with pytest.raises(CompleteError) as info:
        await complete_issue_for_pr("pr", pr_view=view, tracker=_Tracker())
    assert info.value.code == 2
    assert (
        await complete_issue_for_pr("pr", pr_view=view, tracker=_Tracker(), issue="OPS-1")
    ).issue == "OPS-1"


async def test_a_tracker_failure_is_a_code_1_and_still_closes_the_client() -> None:
    tracker = _Tracker(fail=True)
    with pytest.raises(CompleteError) as info:
        await complete_issue_for_pr(
            "pr", pr_view=_view(state="MERGED", mergedAt="x", headRefName="feat/a/K-1"), tracker=tracker
        )
    assert info.value.code == 1 and "jira said no" in str(info.value)
    assert tracker.closed
