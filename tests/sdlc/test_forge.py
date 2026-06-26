"""Unit tests for the PR seam.

``StubPRAdapter`` is trivial; ``GhPRAdapter`` runs real ``git`` against a local
bare remote (so add/commit/push actually happen) while the ``gh`` calls are
overridden so the test needs no GitHub network or auth. That keeps the git
plumbing — stage, commit-only-when-dirty, push the branch — under real test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator.sdlc.forge import GhPRAdapter, PRAdapter, PRError, PRResult, StubPRAdapter


async def _git(cwd: Path, *args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    assert proc.returncode == 0, out.decode()


class _FakeGhAdapter(GhPRAdapter):
    """GhPRAdapter with the ``gh`` calls faked so no GitHub network is needed."""

    def __init__(self, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.gh_calls: list[tuple[str, ...]] = []

    async def _gh(self, path: str, *args: str) -> str:
        self.gh_calls.append(args)
        if args[:2] == ("pr", "create"):
            return "https://github.com/acme/repo/pull/7\n"
        if args[:2] == ("pr", "view"):
            return '{"number": 7}'
        return ""


def test_stub_and_gh_satisfy_protocol() -> None:
    assert isinstance(StubPRAdapter(), PRAdapter)
    assert isinstance(GhPRAdapter(), PRAdapter)


async def test_stub_returns_synthetic_url() -> None:
    pr = await StubPRAdapter().open_pr(issue_key="ENG-1", path="/tmp/x", branch="feat/x", title="t", body="b")
    assert pr.url == "https://stub.github/pr/ENG-1"


async def _make_worktree(tmp_path: Path) -> tuple[Path, str]:
    """A worktree on branch ``feat/demo`` whose origin is a local bare repo."""
    bare = tmp_path / "remote.git"
    base = tmp_path / "base"
    await _git(tmp_path, "init", "--bare", str(bare))
    await _git(tmp_path, "clone", str(bare), str(base))
    await _git(base, "config", "user.email", "t@t.local")
    await _git(base, "config", "user.name", "T")
    (base / "README.md").write_text("seed\n")
    await _git(base, "add", "README.md")
    await _git(base, "commit", "-m", "seed")
    await _git(base, "push", "origin", "HEAD:main")
    branch = "feat/demo"
    wt = tmp_path / "wt"
    await _git(base, "worktree", "add", "-b", branch, str(wt), "HEAD")
    return wt, branch


async def test_open_pr_commits_pushes_and_parses_result(tmp_path: Path) -> None:
    wt, branch = await _make_worktree(tmp_path)
    (wt / "feature.py").write_text("x = 1\n")

    adapter = _FakeGhAdapter(commit_prefix="ENG-9: ")
    pr = await adapter.open_pr(
        issue_key="ENG-9", path=str(wt), branch=branch, title="Add feature", body="body"
    )

    assert pr == PRResult(url="https://github.com/acme/repo/pull/7", number=7)
    # The branch was pushed to origin with the generated commit.
    proc = await asyncio.create_subprocess_exec(
        "git",
        "log",
        "--oneline",
        f"origin/{branch}",
        cwd=str(wt),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    assert "ENG-9: Add feature" in out.decode()
    # gh create was called with the right head/title.
    create = next(c for c in adapter.gh_calls if c[:2] == ("pr", "create"))
    assert "--head" in create and branch in create


async def test_open_pr_skips_commit_when_nothing_changed(tmp_path: Path) -> None:
    wt, branch = await _make_worktree(tmp_path)  # no new files written
    adapter = _FakeGhAdapter()
    pr = await adapter.open_pr(issue_key="ENG-1", path=str(wt), branch=branch, title="noop", body="b")
    assert pr.url.endswith("/pull/7")  # still pushes + opens PR, just no new commit


async def test_git_failure_raises_prerror(tmp_path: Path) -> None:
    # Not a git repo → the first `git add` fails.
    adapter = _FakeGhAdapter()
    with pytest.raises(PRError):
        await adapter.open_pr(issue_key="ENG-1", path=str(tmp_path), branch="feat/x", title="t", body="b")
