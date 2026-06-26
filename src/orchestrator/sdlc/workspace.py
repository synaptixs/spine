"""Block C: per-issue git worktree lifecycle.

Each fanned-out feature workflow needs an isolated checkout so concurrent
issues never collide on the working tree. A git *worktree* gives each issue
its own directory backed by one shared object store — cheap to create and
tear down.

Security: every git invocation goes through ``asyncio.create_subprocess_exec``
with an explicit argv list. We never build a shell string, so issue keys or
paths can't smuggle in shell metacharacters. These calls run inside a Temporal
activity (side-effecting, non-deterministic), never in workflow code.

For the skeleton, ``WorkspaceManager`` with no ``repo_url`` bootstraps a
scratch repo under ``root`` via ``git init`` + an initial commit, so the
worktree mechanics (and the stubbed file write) are exercised without a real
monorepo. Block D points ``repo_url`` at the real source.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from orchestrator.sdlc.gitauth import authenticate_repo_url

logger = logging.getLogger("orchestrator.sdlc.workspace")

# Marker file (kept in ``root``, outside the repo working tree) recording which
# source the current ``_base`` was built for. Lets us detect a stale base — e.g.
# a scratch base left by a ``--safe`` run being reused for a ``--live`` run that
# needs a real clone, or a changed ``repo_url`` — and rebuild instead of
# silently cloning-skipping onto the wrong (or remote-less) base.
_BASE_MARKER = ".sdlc_base_source"


class WorkspaceError(RuntimeError):
    """A git operation backing the workspace failed."""


async def _run_git(*args: str, cwd: Path | None = None) -> str:
    """Run ``git <args>`` via exec (no shell). Returns stdout, raises on error."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        cmd = "git " + " ".join(args)
        stderr = stderr_bytes.decode("utf-8", "replace").strip()
        raise WorkspaceError(f"{cmd!r} failed (exit {proc.returncode}): {stderr}")
    return stdout_bytes.decode("utf-8", "replace")


class WorkspaceManager:
    """Creates and tears down per-issue git worktrees under a shared root.

    ``root`` is the directory that holds the base repo and all worktrees.
    With no ``repo_url`` (skeleton/test mode) the base repo is a freshly
    initialised scratch repo; otherwise Block D clones ``repo_url``.
    """

    def __init__(self, root: Path, repo_url: str | None = None) -> None:
        self._root = Path(root)
        self._repo_url = repo_url
        self._base = self._root / "_base"
        self._marker = self._root / _BASE_MARKER
        self._init_lock = asyncio.Lock()
        self._refreshed = False  # base fast-forwarded to the remote latest this run?

    @property
    def base_repo(self) -> Path:
        return self._base

    async def ensure_base_repo(self) -> Path:
        """Ensure the base checkout exists (clone/refresh) and return its path.

        For read-only consumers (e.g. project profiling before any worktree) —
        the same base the per-issue worktrees branch from.
        """
        await self._ensure_base_repo()
        return self._base

    def _desired_source(self) -> str:
        """Identity of the base this manager wants — the repo URL, or scratch."""
        return self._repo_url or "(scratch)"

    def _base_matches(self) -> bool:
        """True when an existing base was built for the source we want now."""
        try:
            return self._marker.read_text(encoding="utf-8").strip() == self._desired_source()
        except OSError:
            return False  # missing marker → predates this check / unknown → rebuild

    async def _ensure_base_repo(self) -> None:
        """Make sure a base repo for the desired source exists at ``_base``.

        Idempotent and serialised: concurrent children racing into ``create``
        must not both build the base, so the first one through the lock builds
        it and the rest see it already present. A base built for a *different*
        source (stale ``--safe`` scratch base, changed ``repo_url``) is torn
        down and rebuilt rather than reused.
        """
        async with self._init_lock:
            if (self._base / ".git").exists():
                if self._base_matches():
                    # Pull the latest before branching so the worktree (and the
                    # PKG extracted from it) reflect previously-merged features —
                    # otherwise comprehension grounds on a frozen first-clone
                    # snapshot and never sees the work it already shipped.
                    if self._repo_url and not self._refreshed:
                        await self._refresh_base()
                    return
                shutil.rmtree(self._base, ignore_errors=True)
            self._base.mkdir(parents=True, exist_ok=True)
            if self._repo_url:
                # Embed a token (env PAT or a GitHub App installation token) so
                # cloning + the later push that reuses `origin` authenticate
                # against a private repo without an ambient credential helper.
                # Falls back to the bare URL when no token is configured.
                clone_url = await authenticate_repo_url(self._repo_url)
                await _run_git("clone", clone_url or self._repo_url, str(self._base))
                # Same neutral identity as the scratch path: worktrees inherit
                # the clone's local config, and without this the feature
                # commits pick up the machine's global email — which GitHub
                # rejects with GH007 when that address is private (run #14's
                # live lesson).
                await self._set_identity()
                self._mark_base()
                self._refreshed = True  # a fresh clone is already at the remote latest
                return
            # Scratch repo: init + a seed commit so `worktree add` has a base
            # commit to branch from. Identity is set locally so the commit
            # works even on a machine with no global git config.
            await _run_git("init", cwd=self._base)
            await self._set_identity()
            (self._base / "README.md").write_text("# scratch workspace\n", encoding="utf-8")
            await _run_git("add", "README.md", cwd=self._base)
            await _run_git("commit", "-m", "chore: seed scratch workspace", cwd=self._base)
            self._mark_base()

    def _mark_base(self) -> None:
        """Record which source the freshly built base was cloned/seeded for."""
        self._marker.write_text(self._desired_source(), encoding="utf-8")

    async def _refresh_base(self) -> None:
        """Fast-forward the reused base to the remote's latest (once per run).

        Re-authenticates ``origin`` first (installation tokens expire ~1h), then
        fetches and hard-resets the checked-out branch to its upstream. Sibling
        worktrees sit on their own ``feat/*`` branches and are untouched.
        Best-effort: a fetch failure (offline, expired token, deleted branch)
        leaves the existing base in place rather than failing the run.
        """
        self._refreshed = True
        try:
            fresh_url = await authenticate_repo_url(self._repo_url)
            if fresh_url:
                await _run_git("remote", "set-url", "origin", fresh_url, cwd=self._base)
            await _run_git("fetch", "--quiet", "origin", cwd=self._base)
            branch = (await _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=self._base)).strip()
            await _run_git("reset", "--hard", f"origin/{branch}", cwd=self._base)
        except WorkspaceError as exc:
            logger.warning("sdlc.workspace.base_refresh_failed", extra={"error": str(exc)[:200]})

    async def _set_identity(self) -> None:
        """Pin a neutral commit identity on the base repo (worktrees inherit it)."""
        await _run_git("config", "user.email", "sdlc@orchestrator.local", cwd=self._base)
        await _run_git("config", "user.name", "SDLC Orchestrator", cwd=self._base)

    async def create(self, sdlc_id: str, issue_key: str) -> Path:
        """Add a worktree for ``issue_key`` under ``root/{sdlc_id}/{issue_key}``.

        Returns the worktree path. A dedicated branch keeps each issue's
        history isolated.
        """
        await self._ensure_base_repo()
        path = self._root / sdlc_id / issue_key
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"feat/{sdlc_id}/{issue_key}"
        await _run_git("worktree", "add", "-b", branch, str(path), "HEAD", cwd=self._base)
        return path

    async def cleanup(self, path: Path) -> None:
        """Remove the worktree at ``path`` (force, to drop uncommitted stub files)."""
        await _run_git("worktree", "remove", "--force", str(path), cwd=self._base)
