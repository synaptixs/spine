"""Real-git unit tests for WorkspaceManager.

These shell out to a real ``git`` in a tmp dir (no network, no monorepo).
They prove the worktree lifecycle works and that two issue keys get isolated
working trees backed by the same base repo.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from orchestrator.sdlc.workspace import (
    WorkspaceError,
    WorkspaceManager,
    _base_dirname,
    _run_git,
    default_workspace_root,
)

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


async def test_a_scratch_base_is_never_used_for_a_clone(tmp_path: Path) -> None:
    """A scratch base left by a --safe run must NOT be reused for a clone run:
    the reused base has no `origin`, so the eventual push fails. Each source
    gets its own base, so the clone run builds one from the real remote."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    # First, a scratch (no repo_url) manager builds a remote-less base.
    scratch = WorkspaceManager(root=root)
    await scratch.create("s0", "PROJ-0")
    with pytest.raises(WorkspaceError):  # scratch base has no origin
        await _run_git("remote", "get-url", "origin", cwd=scratch.base_repo)

    # A clone manager on the SAME root gets a base cloned from the remote.
    clone = WorkspaceManager(root=root, repo_url=str(remote))
    await clone.create("s1", "ISSUE-1")
    assert clone.base_repo != scratch.base_repo
    origin = (await _run_git("remote", "get-url", "origin", cwd=clone.base_repo)).strip()
    assert origin == str(remote)


async def test_reused_base_is_fast_forwarded_to_remote_latest(tmp_path: Path) -> None:
    """Comprehension can only compound if the reused base pulls merged work:
    a feature landed on the remote after the first clone must appear in the
    next run's base (and thus the worktree the PKG is extracted from)."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    # Run 1 clones the base at the seed commit.
    first = WorkspaceManager(root=root, repo_url=str(remote))
    await first.create("s1", "ISSUE-1")
    assert not (first.base_repo / "shipped.py").exists()

    # A feature merges to the remote between runs.
    (remote / "shipped.py").write_text("def shipped():\n    return 1\n", encoding="utf-8")
    await _run_git("add", "shipped.py", cwd=remote)
    await _run_git("commit", "-m", "feat: ship it", cwd=remote)

    # Run 2 reuses the base but must fast-forward it to the remote latest.
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")
    assert (first.base_repo / "shipped.py").exists()


async def test_matching_base_is_reused_not_rebuilt(tmp_path: Path) -> None:
    """A base built for the same source is reused as-is (no expensive re-clone)."""
    root = tmp_path / "ws"
    remote = tmp_path / "remote"
    await _seed_remote(remote)

    mgr = WorkspaceManager(root=root, repo_url=str(remote))
    await mgr.create("s1", "ISSUE-1")
    sentinel = mgr.base_repo / ".reuse_sentinel"
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


async def _seed_superproject(tmp_path: Path) -> Path:
    """A local ``super`` remote that pins a local ``lib`` remote as ``libs/lib``."""
    lib = tmp_path / "lib-remote"
    lib.mkdir()
    await _run_git("init", cwd=lib)
    await _run_git("config", "user.email", "seed@example.com", cwd=lib)
    await _run_git("config", "user.name", "Seed", cwd=lib)
    (lib / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    await _run_git("add", "core.py", cwd=lib)
    await _run_git("commit", "-m", "lib", cwd=lib)

    super_ = tmp_path / "super-remote"
    await _seed_remote(super_)
    await _run_git("submodule", "add", str(lib), "libs/lib", cwd=super_)
    await _run_git("commit", "-m", "pin lib", cwd=super_)
    return super_


async def test_a_superproject_worktree_carries_its_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``git worktree add`` leaves every submodule directory empty; the manager fills them.

    Without this the test env cannot import the submodule and codegen asked to touch it
    writes into a folder git does not own. Local-path submodules need the ``file``
    transport, which git disables by default — allowed here through git's environment
    config so the code under test runs exactly as it does in production.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    origin = await _seed_superproject(tmp_path)

    manager = WorkspaceManager(root=tmp_path / "ws", repo_url=str(origin))
    path = await manager.create("s1", "ISSUE-1")

    assert (path / "libs" / "lib" / "core.py").is_file()
    # And it is a checkout of its own, pinned where the superproject says.
    pinned = (await _run_git("rev-parse", "HEAD:libs/lib", cwd=path)).strip()
    actual = (await _run_git("rev-parse", "HEAD", cwd=path / "libs" / "lib")).strip()
    assert actual == pinned


# --- The base is proven before reuse, kept per source, and locked across processes ---------
#
# NSS-1231 / NSS-1243: every ticket on a pilot machine failed at `worktree add` with "fatal:
# not a git repository". The shared base under /tmp had lost `.git/HEAD` to the OS's age-based
# sweep; `.git` still existed, so the base was reused, the refresh failed silently, and every
# later run died the same way until the base was deleted by hand.


async def test_a_base_without_head_is_rebuilt_not_reused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The field failure, exactly: `.git` present, `HEAD` gone. The next run must rebuild.

    The root sits inside another repository on purpose: git discovery walks *up* past a broken
    `.git`, so a check without `--git-dir` would find the enclosing repo and call the base fine
    — and `worktree add` would then branch the *enclosing* repository.
    """
    await _run_git("init", cwd=tmp_path)
    await _run_git(
        "-c",
        "user.email=e@example.com",
        "-c",
        "user.name=E",
        "commit",
        "--allow-empty",
        "-m",
        "outer",
        cwd=tmp_path,
    )
    remote = tmp_path / "remote"
    await _seed_remote(remote)
    root = tmp_path / "ws"

    first = WorkspaceManager(root=root, repo_url=str(remote))
    await first.create("s1", "ISSUE-1")
    (first.base_repo / ".git" / "HEAD").unlink()

    with caplog.at_level("WARNING", logger="orchestrator.sdlc.workspace"):
        path = await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")

    assert (path / "README.md").is_file()
    assert (first.base_repo / ".git" / "HEAD").is_file()
    assert any("base_unusable" in r.getMessage() for r in caplog.records)


async def test_a_deleted_base_with_its_marker_left_behind_is_rebuilt(tmp_path: Path) -> None:
    """Deleting the base by hand (the field workaround) must work even if the marker survives."""
    remote = tmp_path / "remote"
    await _seed_remote(remote)
    root = tmp_path / "ws"

    first = WorkspaceManager(root=root, repo_url=str(remote))
    await first.create("s1", "ISSUE-1")
    shutil.rmtree(first.base_repo)

    path = await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")
    assert (path / "README.md").is_file()


async def test_switching_repos_keeps_both_bases_and_their_worktrees(tmp_path: Path) -> None:
    """One `_base` shared by every repo was re-cloned on each switch, orphaning worktrees."""
    repo_a, repo_b = tmp_path / "repo-a", tmp_path / "repo-b"
    await _seed_remote(repo_a)
    await _seed_remote(repo_b)
    root = tmp_path / "ws"

    mgr_a = WorkspaceManager(root=root, repo_url=str(repo_a))
    work_a = await mgr_a.create("s1", "A-1")
    sentinel = mgr_a.base_repo / ".reuse_sentinel"
    sentinel.write_text("kept", encoding="utf-8")

    mgr_b = WorkspaceManager(root=root, repo_url=str(repo_b))
    await mgr_b.create("s2", "B-1")
    await WorkspaceManager(root=root, repo_url=str(repo_a)).create("s3", "A-2")

    assert mgr_a.base_repo != mgr_b.base_repo
    assert sentinel.exists()  # A's base was never torn down
    await _run_git("status", cwd=work_a)  # and A's first worktree still has its object store


async def test_worktrees_deleted_by_hand_are_pruned_from_the_base(tmp_path: Path) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    gone = await mgr.create("s1", "PROJ-1")
    shutil.rmtree(gone)

    await mgr.create("s2", "PROJ-2")

    listed = await _run_git("worktree", "list", "--porcelain", cwd=mgr.base_repo)
    assert str(gone) not in listed


async def test_a_failed_refresh_says_why_and_the_run_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The reason used to ride in `extra=`, which the CLI never prints: users saw only the name."""
    remote = tmp_path / "remote"
    await _seed_remote(remote)
    root = tmp_path / "ws"
    await WorkspaceManager(root=root, repo_url=str(remote)).create("s1", "ISSUE-1")
    remote.rename(tmp_path / "remote-moved")  # the fetch now fails

    with caplog.at_level("WARNING", logger="orchestrator.sdlc.workspace"):
        path = await WorkspaceManager(root=root, repo_url=str(remote)).create("s2", "ISSUE-2")

    assert path.is_dir()
    [record] = [r for r in caplog.records if "base_refresh_failed" in r.getMessage()]
    assert "git fetch" in record.getMessage()


async def test_a_base_that_cannot_be_removed_is_reported_not_cloned_into(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = WorkspaceManager(root=tmp_path / "ws")
    await mgr.create("s1", "PROJ-1")
    (mgr.base_repo / ".git" / "HEAD").unlink()
    monkeypatch.setattr("orchestrator.sdlc.workspace.shutil.rmtree", lambda *_a, **_k: None)

    with pytest.raises(WorkspaceError, match="could not remove"):
        await WorkspaceManager(root=tmp_path / "ws").create("s2", "PROJ-2")


@pytest.mark.skipif(sys.platform == "win32", reason="advisory flock is POSIX-only")
async def test_a_base_locked_by_another_process_is_waited_for(tmp_path: Path) -> None:
    """Two CLI runs each had their own asyncio lock and raced on the same base."""
    import asyncio
    import fcntl
    import os

    mgr = WorkspaceManager(root=tmp_path / "ws")
    await mgr.create("s1", "PROJ-1")
    # Another open file description on the lock file behaves as another process does.
    fd = os.open(tmp_path / "ws" / "_bases" / f"{mgr.base_repo.name}.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        waiting = asyncio.ensure_future(WorkspaceManager(root=tmp_path / "ws").create("s2", "PROJ-2"))
        await asyncio.sleep(0.6)
        assert not waiting.done()
    finally:
        os.close(fd)
    assert (await waiting).is_dir()


def test_the_default_root_is_not_under_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_workspace_root() == tmp_path / ".cache" / "orchestrator" / "sdlc-workspaces"

    monkeypatch.setenv("SDLC_WORKSPACE_ROOT", str(tmp_path / "elsewhere"))
    assert default_workspace_root() == tmp_path / "elsewhere"

    monkeypatch.setenv("SDLC_WORKSPACE_ROOT", "~/ws")  # `.env` values are not shell-expanded
    assert default_workspace_root() == tmp_path / "ws"


def test_base_names_are_readable_distinct_and_carry_no_credentials() -> None:
    main = _base_dirname("https://x-access-token:s3cret@github.com/acme/order-portal.git")
    develop = _base_dirname("https://x-access-token:s3cret@github.com/acme/order-portal.git#develop")

    assert main.startswith("order-portal-")
    assert main != develop
    assert "s3cret" not in main + develop
    assert _base_dirname("(scratch)").startswith("scratch-")
