"""Respond to human PR comments: forge parsing/formatting + the responder loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.sdlc.forge import PRComment, StubPRAdapter, _comment_from, format_review_feedback
from orchestrator.sdlc.review_response import respond_to_pr_feedback

# ---- comment parsing / filtering ------------------------------------------


def test_comment_from_drops_self_bots_and_empties() -> None:
    def node(login: str, body: str) -> dict[str, Any]:
        return {"author": {"login": login}, "body": body}

    assert _comment_from(node("alice", "fix this"), kind="issue", excl="agent-bot") == PRComment(
        author="alice", body="fix this", kind="issue"
    )
    # the agent's own comment is excluded
    assert _comment_from(node("agent-bot", "I opened this"), kind="issue", excl="agent-bot") is None
    # CI bot noise excluded
    assert _comment_from(node("github-actions[bot]", "checks passed"), kind="review", excl="x") is None
    # empty body / missing author dropped
    assert _comment_from(node("alice", "   "), kind="issue", excl="x") is None
    assert _comment_from({"body": "no author"}, kind="issue", excl="x") is None


def test_format_review_feedback_lists_comments() -> None:
    block = format_review_feedback(
        [
            PRComment(author="alice", body="rename foo to bar", kind="review"),
            PRComment(author="bob", body="add a docstring", kind="inline"),
        ]
    )
    assert "@alice (review): rename foo to bar" in block
    assert "@bob (inline): add a docstring" in block
    assert format_review_feedback([]) == ""


# ---- the responder loop (fakes, no network/codegen) -----------------------


class _Tests:
    def __init__(self, passed: bool) -> None:
        self._passed = passed

    async def run(self, *, path: str) -> Any:
        _ = path
        return type("R", (), {"passed": self._passed, "returncode": 0, "output": "stub"})()


class _Preflight:
    def __init__(self, passed: bool) -> None:
        self._passed = passed

    async def run(self, *, path: str) -> Any:
        _ = path
        return type("P", (), {"passed": self._passed, "output": "stub"})()


class _Codegen:
    def __init__(self) -> None:
        self.refine_feedback: list[str] = []

    async def refine(self, *, spec: Any, path: str, issue_key: str, failures: str) -> Any:
        _ = (spec, path, issue_key)
        self.refine_feedback.append(failures)
        return type("C", (), {"files": ["m.py"], "summary": "refined"})()


class _PR(StubPRAdapter):
    def __init__(self, comments: list[PRComment]) -> None:
        super().__init__()
        self._comments = comments
        self.pushed: list[str] = []

    async def fetch_review_comments(
        self, *, pr_url: str, exclude_author: str | None = None
    ) -> list[PRComment]:
        _ = (pr_url, exclude_author)
        return list(self._comments)

    async def push_followup(self, *, path: str, branch: str, message: str) -> bool:
        _ = path
        self.pushed.append(f"{branch}:{message}")
        return True


def _deps(*, pr: _PR, tests_pass: bool, preflight_pass: bool) -> Any:
    from orchestrator.sdlc.deps import SDLCDeps
    from orchestrator.sdlc.workspace import WorkspaceManager

    return SDLCDeps(
        session_factory=None,  # type: ignore[arg-type]  # unused here
        workspace=WorkspaceManager(root=Path("/tmp/unused")),
        codegen=_Codegen(),  # type: ignore[arg-type]
        tests=_Tests(tests_pass),
        preflight=_Preflight(preflight_pass),
        pr=pr,
    )


async def test_no_comments_is_a_no_op() -> None:
    pr = _PR([])
    out = await respond_to_pr_feedback(
        _deps(pr=pr, tests_pass=True, preflight_pass=True),
        pr_url="https://gh/pr/1",
        branch="feat/x",
        path="/tmp/wt",
    )
    assert out.comments == 0
    assert out.addressed is False
    assert pr.pushed == []


async def test_addresses_feedback_and_pushes_when_green() -> None:
    pr = _PR([PRComment(author="alice", body="rename foo")])
    deps = _deps(pr=pr, tests_pass=True, preflight_pass=True)
    out = await respond_to_pr_feedback(deps, pr_url="https://gh/pr/1", branch="feat/x", path="/tmp/wt")
    assert out.comments == 1
    assert out.green is True
    assert out.addressed is True
    assert out.refines == 1
    assert pr.pushed and pr.pushed[0].startswith("feat/x:")
    # The first refine was seeded with the human feedback, not a test failure.
    assert "rename foo" in deps.codegen.refine_feedback[0]


async def test_no_push_when_cannot_reach_green() -> None:
    pr = _PR([PRComment(author="alice", body="do the thing")])
    out = await respond_to_pr_feedback(
        _deps(pr=pr, tests_pass=False, preflight_pass=True),
        pr_url="https://gh/pr/1",
        branch="feat/x",
        path="/tmp/wt",
        max_refines=2,
    )
    assert out.green is False
    assert out.addressed is False
    assert out.refines == 2  # exhausted the budget
    assert pr.pushed == []
