"""Merge-on-green: the PRAdapter merge seam + the all-or-nothing activity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.sdlc.forge import GhPRAdapter, MergeResult, StubPRAdapter


async def test_stub_merge_succeeds() -> None:
    result = await StubPRAdapter().merge_pr(pr_url="https://stub.github/pr/E-1")
    assert result.merged and result.url.endswith("E-1")


async def test_gh_merge_fails_closed_on_nonzero_exit() -> None:
    adapter = GhPRAdapter()

    async def fake_exec(*argv: str, cwd: str) -> tuple[int, str]:
        assert argv[:3] == ("gh", "pr", "merge")
        return 1, "GraphQL: Pull request is not mergeable (checks pending)"

    adapter._exec = fake_exec  # type: ignore[method-assign]
    result = await adapter.merge_pr(pr_url="https://github.com/a/b/pull/9")
    assert not result.merged and "not mergeable" in result.detail


async def test_gh_merge_success() -> None:
    adapter = GhPRAdapter()

    async def fake_exec(*argv: str, cwd: str) -> tuple[int, str]:
        return 0, "✓ Merged pull request"

    adapter._exec = fake_exec  # type: ignore[method-assign]
    result = await adapter.merge_pr(pr_url="https://github.com/a/b/pull/9")
    assert result.merged


# ---- the activity -----------------------------------------------------------


class _ScriptedPR:
    """merge_pr succeeds unless the URL is in the fail set."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.merged_urls: list[str] = []

    async def open_pr(self, **kwargs: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    async def merge_pr(self, *, pr_url: str) -> MergeResult:
        self.merged_urls.append(pr_url)
        return MergeResult(merged=pr_url not in self.fail, url=pr_url)

    async def fetch_review_comments(self, **kwargs: Any) -> list[Any]:  # pragma: no cover - unused
        _ = kwargs
        return []

    async def push_followup(self, **kwargs: Any) -> bool:  # pragma: no cover - unused
        _ = kwargs
        return False


def _activities(pr: _ScriptedPR) -> Any:
    from orchestrator.sdlc.activities import SDLCActivities
    from orchestrator.sdlc.deps import SDLCDeps
    from orchestrator.sdlc.workspace import WorkspaceManager

    deps = SDLCDeps(
        session_factory=None,  # type: ignore[arg-type]  # merge activity never touches the DB
        workspace=WorkspaceManager(root=Path("/tmp/unused")),
        pr=pr,
    )
    return SDLCActivities(deps)


async def test_merge_prs_all_green() -> None:
    pr = _ScriptedPR()
    out = await _activities(pr).merge_prs({"pr_urls": ["https://g/1", "https://g/2"]})
    assert out["verdict"] == "pass"
    assert pr.merged_urls == ["https://g/1", "https://g/2"]


async def test_merge_prs_one_failure_fails_stage() -> None:
    pr = _ScriptedPR(fail={"https://g/2"})
    out = await _activities(pr).merge_prs({"pr_urls": ["https://g/1", "https://g/2"]})
    assert out["verdict"] == "fail"
    assert [m["merged"] for m in out["merges"]] == [True, False]


async def test_merge_prs_empty_urls_cannot_pass() -> None:
    out = await _activities(_ScriptedPR()).merge_prs({"pr_urls": []})
    assert out["verdict"] == "fail"  # nothing merged is not a success
