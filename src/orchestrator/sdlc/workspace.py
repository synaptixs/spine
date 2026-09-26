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

**Where the bases live, and why one is proven before it is reused** (NSS-1231, NSS-1243 —
every ticket on a pilot machine failed with ``fatal: not a git repository`` before codegen):

* **One base per source**, under ``root/_bases/<name>-<hash>``. A single ``root/_base``
  shared by every repository was torn down and re-cloned each time a run switched repos, and
  every worktree the other repository had in flight lost the object store behind it.
* **Proven, not presumed.** ``.git`` existing was the whole check, and the default root was
  ``/tmp`` — whose age-based sweep on macOS deletes the files git never rewrites (``HEAD``,
  ``description``, ``packed-refs``) and keeps the ones every refresh touches. A ``.git`` with
  no ``HEAD`` passed, the refresh failed and was swallowed, and ``worktree add`` died on every
  run until someone deleted the base by hand. A base is now reused only when
  ``git --git-dir <base>/.git rev-parse --verify HEAD^{commit}`` succeeds; ``--git-dir``
  because plain discovery walks *up* past a broken ``.git`` and would operate on whatever
  repository encloses the root. The default root is ``~/.cache``, not ``/tmp``.
* **Serialised across processes.** The ``asyncio.Lock`` covers one process; two CLI runs
  each had their own and raced ``fetch`` / ``reset --hard`` / ``worktree add`` on the same
  base. An advisory ``flock`` on a lock file beside the base now spans them (POSIX only —
  on Windows the in-process lock is all there is).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from orchestrator.sdlc.gitauth import authenticate_repo_url

if sys.platform != "win32":
    import fcntl

logger = logging.getLogger("orchestrator.sdlc.workspace")

#: The directory under ``root`` holding one base checkout per source.
_BASES_DIRNAME = "_bases"
#: How often a run waiting on another process's lock re-tries it. Polling rather than a
#: blocking ``flock`` in a thread, because a cancelled waiter must not go on to take the lock.
_LOCK_POLL_SECONDS = 0.2


class WorkspaceError(RuntimeError):
    """A git operation backing the workspace failed."""


def default_workspace_root() -> Path:
    """``SDLC_WORKSPACE_ROOT``, else ``~/.cache/orchestrator/sdlc-workspaces``.

    Never ``/tmp``: macOS removes files there that have not been touched for three days, one
    file at a time, and a git repository whose untouched files are gone is not a repository.
    Beside the intake cache (``~/.cache/orchestrator/intake``) on purpose.
    """
    env = os.getenv("SDLC_WORKSPACE_ROOT")
    # expanduser: `.env` files are not shell-expanded, and the documented example uses `~`.
    return Path(env).expanduser() if env else Path.home() / ".cache" / "orchestrator" / "sdlc-workspaces"


def _base_dirname(source: str) -> str:
    """A readable, collision-safe directory name for the base of ``source``.

    The repository's last path segment, for whoever lists the directory, plus a digest of
    the whole identity (URL and branch) so two sources never share a base. The segment is
    taken after the last separator, so credentials in a URL's authority never reach a path.
    """
    digest = hashlib.sha1(source.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    location = source.split("#", 1)[0].rstrip("/\\")
    stem = re.split(r"[/\\:]", location)[-1].removesuffix(".git")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:40] or "base"
    return f"{slug}-{digest}"


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

    ``root`` holds one base repo per source (``root/_bases/``) and every worktree
    (``root/<sdlc_id>/<issue_key>``). With no ``repo_url`` (skeleton/test mode) the base
    repo is a freshly initialised scratch repo; otherwise Block D clones ``repo_url``.
    """

    def __init__(self, root: Path, repo_url: str | None = None, base_branch: str | None = None) -> None:
        self._root = Path(root)
        self._repo_url = repo_url
        # Branch the work from here. Without it a clone checks out the remote's
        # *default* branch, so a run targeting `develop` still built on `main` —
        # the generated change was written against code that predated everything
        # merged to develop since the last release, and could silently revert it.
        self._base_branch = base_branch or None
        name = _base_dirname(self._desired_source())
        bases = self._root / _BASES_DIRNAME
        self._base = bases / name
        # Written last, once the base is fully built: a base with no marker is one whose
        # build was interrupted, and it is rebuilt rather than trusted.
        self._marker = bases / f"{name}.source"
        self._lockfile = bases / f"{name}.lock"
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
        async with self._exclusive():
            await self._ensure_base_repo()
        return self._base

    def _desired_source(self) -> str:
        """Identity of the base this manager wants — repo URL + branch, or scratch.

        The branch is part of the identity: a base cloned for ``main`` cannot be
        reused for a run branching from ``develop``, and reusing it is exactly how
        a run silently built on the wrong tree.
        """
        source = self._repo_url or "(scratch)"
        return f"{source}#{self._base_branch}" if self._base_branch else source

    def _base_matches(self) -> bool:
        """True when an existing base was built for the source we want now."""
        try:
            return self._marker.read_text(encoding="utf-8").strip() == self._desired_source()
        except OSError:
            return False  # missing marker → interrupted build / unknown → rebuild

    async def _base_is_usable(self) -> bool:
        """True when git can read the base's ``HEAD`` commit from its own ``.git``."""
        git_dir = self._base / ".git"
        if not git_dir.is_dir():
            return False
        try:
            await _run_git("--git-dir", str(git_dir), "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        except WorkspaceError:
            return False
        return True

    @contextlib.asynccontextmanager
    async def _exclusive(self) -> AsyncIterator[None]:
        """Hold this base against other coroutines *and* other processes."""
        async with self._init_lock:
            self._lockfile.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                yield
                return
            fd = os.open(self._lockfile, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                waiting = False
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if not waiting:
                            waiting = True
                            # WARNING, not INFO: the CLI shows warnings only; a silent wait reads as a hang.
                            logger.warning(
                                "sdlc.workspace.waiting_for_lock: another run is using this base (%s)",
                                self._lockfile,
                            )
                        await asyncio.sleep(_LOCK_POLL_SECONDS)
                yield
            finally:
                os.close(fd)  # closing the descriptor releases the lock

    async def _ensure_base_repo(self) -> None:
        """Make sure a usable base repo for the desired source exists. Call under ``_exclusive``.

        A base is reused only when it was built for this source *and* git can still read
        it; anything else — an interrupted build, a ``.git`` with files missing — is
        discarded and rebuilt. The first caller through the lock builds it and the rest
        see it present.
        """
        if self._base_matches():
            if await self._base_is_usable():
                # Pull the latest before branching so the worktree (and the
                # PKG extracted from it) reflect previously-merged features —
                # otherwise comprehension grounds on a frozen first-clone
                # snapshot and never sees the work it already shipped.
                if self._repo_url and not self._refreshed:
                    await self._refresh_base()
                # Drop the records of worktrees whose directories are gone, so they do not
                # pile up in the base's `.git/worktrees/` for as long as it lives.
                await _run_git("worktree", "prune", cwd=self._base)
                return
            logger.warning(
                "sdlc.workspace.base_unusable: %s is missing or git can no longer read it — rebuilding it",
                self._base,
            )
        self._discard_base()
        await self._build_base()

    def _discard_base(self) -> None:
        """Remove the base and its marker, or say plainly that it could not be removed.

        The marker goes first, so a deletion interrupted half-way leaves a base that the next
        run rebuilds rather than one it trusts. Cloning into a half-deleted directory fails
        with an error that names neither cause nor cure, hence the explicit check.
        """
        self._marker.unlink(missing_ok=True)
        shutil.rmtree(self._base, ignore_errors=True)
        if self._base.exists():
            raise WorkspaceError(
                f"could not remove the unusable workspace base at {self._base} — delete it and re-run"
            )

    async def _build_base(self) -> None:
        """Clone ``repo_url`` (or seed a scratch repo) into the empty base directory."""
        self._base.mkdir(parents=True, exist_ok=True)
        if self._repo_url:
            # Embed a token (env PAT or a GitHub App installation token) so
            # cloning + the later push that reuses `origin` authenticate
            # against a private repo without an ambient credential helper.
            # Falls back to the bare URL when no token is configured.
            clone_url = await authenticate_repo_url(self._repo_url)
            # A superproject's submodules come with it. Without this the worktree carries
            # empty directories where a submodule should be, the test env cannot import
            # it, and codegen asked to touch it writes into a folder git does not own.
            clone_args = ["clone", "--recurse-submodules"]
            if self._base_branch:
                clone_args += ["--branch", self._base_branch]
            clone_args += [clone_url or self._repo_url, str(self._base)]
            await _run_git(*clone_args)
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
        leaves the existing base in place rather than failing the run — and says
        why, in the message itself: the reason used to ride in ``extra=``, which the
        CLI's handler never prints, so a user saw the event name and nothing else.
        """
        self._refreshed = True
        try:
            fresh_url = await authenticate_repo_url(self._repo_url)
            if fresh_url:
                await _run_git("remote", "set-url", "origin", fresh_url, cwd=self._base)
            await _run_git("fetch", "--quiet", "origin", cwd=self._base)
            branch = (await _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=self._base)).strip()
            await _run_git("reset", "--hard", f"origin/{branch}", cwd=self._base)
            await self._populate_submodules(self._base)
        except WorkspaceError as exc:
            logger.warning(
                "sdlc.workspace.base_refresh_failed: building on the base as it was — %s",
                str(exc)[:300],
                extra={"error": str(exc)[:200]},
            )

    async def _populate_submodules(self, tree: Path) -> None:
        """Check out the submodules ``tree``'s pins point at, when it declares any.

        ``git worktree add`` and ``git reset --hard`` move the superproject and leave every
        submodule directory empty; only ``submodule update`` fills them. Guarded on
        ``.gitmodules`` so a repository without submodules costs no extra git call.
        """
        if (tree / ".gitmodules").is_file():
            await _run_git("submodule", "update", "--init", "--recursive", "--quiet", cwd=tree)

    async def _set_identity(self) -> None:
        """Pin a neutral commit identity on the base repo (worktrees inherit it)."""
        await _run_git("config", "user.email", "sdlc@orchestrator.local", cwd=self._base)
        await _run_git("config", "user.name", "SDLC Orchestrator", cwd=self._base)

    async def create(self, sdlc_id: str, issue_key: str) -> Path:
        """Add a worktree for ``issue_key`` under ``root/{sdlc_id}/{issue_key}``.

        Returns the worktree path. A dedicated branch keeps each issue's
        history isolated.
        """
        path = self._root / sdlc_id / issue_key
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"feat/{sdlc_id}/{issue_key}"
        # `worktree add` writes the base's refs and `.git/worktrees/`, so it runs under the
        # same lock as the refresh that rewrites them.
        async with self._exclusive():
            await self._ensure_base_repo()
            await _run_git("worktree", "add", "-b", branch, str(path), "HEAD", cwd=self._base)
        await self._populate_submodules(path)
        return path

    async def cleanup(self, path: Path) -> None:
        """Remove the worktree at ``path`` (force, to drop uncommitted stub files)."""
        async with self._exclusive():
            await _run_git("worktree", "remove", "--force", str(path), cwd=self._base)
