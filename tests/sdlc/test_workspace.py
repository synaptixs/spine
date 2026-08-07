"""Real-git unit tests for WorkspaceManager.

These shell out to a real ``git`` in a tmp dir (no network, no monorepo).
They prove the worktree lifecycle works and that two issue keys get isolated
working trees backed by the same base repo.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.sdlc.workspace import WorkspaceError, WorkspaceManager, _run_git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


async def test_create_returns_isolated_worktree(tmp_path: Path) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    path = await mgr.create("sdlc-1", "PROJ-1")

    assert path == tmp_path / "ws" / "sdlc-1" / "PROJ-1"
    assert path.is_dir()
    # A worktree carries the seed commit's README and its own .git pointer file.
    assert (path / "README.md").exists()
    assert (path / ".git").exists()


async def test_two_issues_are_isolated(tmp_path: Path) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    path_a = await mgr.create("sdlc-1", "PROJ-1")
    path_b = await mgr.create("sdlc-1", "PROJ-2")

    assert path_a != path_b
    # Writing in one worktree does not leak into the other.
    (path_a / "only_in_a.txt").write_text("a", encoding="utf-8")
    assert not (path_b / "only_in_a.txt").exists()


async def test_cleanup_removes_worktree(tmp_path: Path) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    path = await mgr.create("sdlc-1", "PROJ-1")
    # An uncommitted stub file must not block teardown (cleanup forces removal).
    (path / "stub.py").write_text("print('hi')\n", encoding="utf-8")

    await mgr.cleanup(path)

    assert not path.exists()


async def test_base_repo_built_once_under_concurrent_create(tmp_path: Path) -> None:
    import asyncio

    mgr = WorkspaceManager(root=tmp_path / "ws")
    # Race two creates; the init lock must serialise base-repo bootstrap so
    # neither worktree add fails on a half-built base.
    paths = await asyncio.gather(
        mgr.create("sdlc-1", "PROJ-1"),
        mgr.create("sdlc-1", "PROJ-2"),
    )
    assert all(p.is_dir() for p in paths)
    assert (mgr.base_repo / ".git").exists()


async def test_cleanup_unknown_path_raises(tmp_path: Path) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    await mgr.create("sdlc-1", "PROJ-1")  # bootstrap base repo

    with pytest.raises(WorkspaceError):
        await mgr.cleanup(tmp_path / "ws" / "sdlc-1" / "does-not-exist")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_cloned_base_gets_neutral_commit_identity(tmp_path: Path) -> None:
    """Worktrees from a CLONED base must not inherit the machine's global git
    identity — a private global email makes GitHub reject the push with GH007
    (run #14's live lesson)."""
    # A local "remote" to clone from.
    remote = tmp_path / "remote"
    remote.mkdir()
    await _run_git("init", cwd=remote)
    await _run_git("config", "user.email", "seed@example.com", cwd=remote)
    await _run_git("config", "user.name", "Seed", cwd=remote)
    (remote / "README.md").write_text("seed\n", encoding="utf-8")
    await _run_git("add", "README.md", cwd=remote)
    await _run_git("commit", "-m", "seed", cwd=remote)

    manager = WorkspaceManager(root=tmp_path / "ws", repo_url=str(remote))
    path = await manager.create("s1", "ISSUE-1")

    email = (await _run_git("config", "user.email", cwd=path)).strip()
    name = (await _run_git("config", "user.name", cwd=path)).strip()
    assert email == "sdlc@orchestrator.local"
    assert name == "SDLC Orchestrator"


async def _seed_remote(remote: Path) -> None:
    remote.mkdir()
    await _run_git("init", cwd=remote)
    await _run_git("config", "user.email", "seed@example.com", cwd=remote)
    await _run_git("config", "user.name", "Seed", cwd=remote)
    (remote / "README.md").write_text("seed\n", encoding="utf-8")
    await _run_git("add", "README.md", cwd=remote)
    await _run_git("commit", "-m", "seed", cwd=remote)


async def _seed_origin_with_branch(origin: Path, *, branch: str, marker: str) -> None:
    """A remote whose default branch is `main`, plus a second branch that has moved on."""
    origin.mkdir()
    await _run_git("init", "--initial-branch", "main", cwd=origin)
    await _run_git("config", "user.email", "seed@example.com", cwd=origin)
    await _run_git("config", "user.name", "Seed", cwd=origin)
    (origin / "marker.txt").write_text("on-main\n", encoding="utf-8")
    await _run_git("add", "marker.txt", cwd=origin)
    await _run_git("commit", "-m", "main", cwd=origin)
    await _run_git("checkout", "-b", branch, cwd=origin)
    (origin / "marker.txt").write_text(f"{marker}\n", encoding="utf-8")
    await _run_git("add", "marker.txt", cwd=origin)
    await _run_git("commit", "-m", branch, cwd=origin)
    # Leave the remote's HEAD on main, so a plain clone gets main.
    await _run_git("checkout", "main", cwd=origin)


async def test_stale_scratch_base_is_rebuilt_for_clone(tmp_path: Path) -> None:
    """A scratch base left by a --safe run must NOT be reused for a clone run:
    the reused base has no `origin`, so the eventual push fails. The manager
    detects the source mismatch and rebuilds from the real remote."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    # First, a scratch (no repo_url) manager builds a remote-less base.
    await WorkspaceManager(root=root).create("s0", "PROJ-0")
    with pytest.raises(WorkspaceError):  # scratch base has no origin
        await _run_git("remote", "get-url", "origin", cwd=root / "_base")

    # A clone manager on the SAME root rebuilds the base from the remote.
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s1", "ISSUE-1")
    origin = (await _run_git("remote", "get-url", "origin", cwd=root / "_base")).strip()
    assert origin == str(remote)


async def test_reused_base_is_fast_forwarded_to_remote_latest(tmp_path: Path) -> None:
    """Comprehension can only compound if the reused base pulls merged work:
    a feature landed on the remote after the first clone must appear in the
    next run's base (and thus the worktree the PKG is extracted from)."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    # Run 1 clones the base at the seed commit.
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s1", "ISSUE-1")
    assert not (root / "_base" / "shipped.py").exists()

    # A feature merges to the remote between runs.
    (remote / "shipped.py").write_text("def shipped():\n    return 1\n", encoding="utf-8")
    await _run_git("add", "shipped.py", cwd=remote)
    await _run_git("commit", "-m", "feat: ship it", cwd=remote)

    # Run 2 reuses the base but must fast-forward it to the remote latest.
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")
    assert (root / "_base" / "shipped.py").exists()


async def test_matching_base_is_reused_not_rebuilt(tmp_path: Path) -> None:
    """A base built for the same source is reused as-is (no expensive re-clone)."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    mgr = WorkspaceManager(root=root, repo_url=str(remote))
    await mgr.create("s1", "ISSUE-1")
    sentinel = root / "_base" / ".reuse_sentinel"
    sentinel.write_text("kept", encoding="utf-8")

    # Second create with the same source must not rebuild (sentinel survives).
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")
    assert sentinel.exists()


# --- The work branches from the PR target, not the remote's default -----------------
#
# A clone with no --branch checks out the remote's *default* branch. A run opening a PR
# into `develop` therefore built on `main`, so the generated change was written against a
# tree predating everything merged to develop since the last release — and could revert it
# without anything noticing. Caught when a run reverted the change merged two hours earlier.


async def test_the_base_is_cloned_at_the_requested_branch(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    await _seed_origin_with_branch(origin, branch="develop", marker="on-develop")

    mgr = WorkspaceManager(root=tmp_path / "ws", repo_url=str(origin), base_branch="develop")
    work = await mgr.create("sdlc1", "KEY-1")

    assert (work / "marker.txt").read_text().strip() == "on-develop"


async def test_without_a_base_branch_the_default_is_used(tmp_path: Path) -> None:
    """Unchanged behaviour when no target is given — this is not a silent switch."""
    origin = tmp_path / "origin"
    await _seed_origin_with_branch(origin, branch="develop", marker="on-develop")

    mgr = WorkspaceManager(root=tmp_path / "ws", repo_url=str(origin))
    work = await mgr.create("sdlc1", "KEY-1")

    assert (work / "marker.txt").read_text().strip() == "on-main"


async def test_a_base_built_for_another_branch_is_not_reused(tmp_path: Path) -> None:
    """The branch is part of the base's identity; reuse is how a run builds on the wrong tree."""
    origin = tmp_path / "origin"
    await _seed_origin_with_branch(origin, branch="develop", marker="on-develop")
    root = tmp_path / "ws"

    first = await WorkspaceManager(root=root, repo_url=str(origin)).create("sdlc1", "KEY-1")
    assert (first / "marker.txt").read_text().strip() == "on-main"

    second = await WorkspaceManager(root=root, repo_url=str(origin), base_branch="develop").create(
        "sdlc2", "KEY-2"
    )
    assert (second / "marker.txt").read_text().strip() == "on-develop"
