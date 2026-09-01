"""Materialise a git repository at an exact commit — verified, or not at all.

Two callers need the same thing for different reasons: ``evals.corpus_fetch`` puts the pinned
G6 corpus on disk, and ``codereview.checkout`` puts a pull request's head on disk so the
PKG-grounded verifiers have a tree to read. Both consume the result as *evidence*, so both have
the same failure mode — **a partial checkout is worse than none**, because the metrics and
findings computed from it are well-formed and false.

So the discipline lives here once rather than in two copies that drift:

**The marker is written last.** ``WorkspaceManager`` established the ordering — fetch, verify,
*then* mark — so a process that dies mid-fetch leaves a directory that does not read as
complete, and the next attempt tears it down instead of measuring a truncated tree.

**Reuse re-asks the repository.** The marker records intent; ``git rev-parse HEAD`` records
fact. Trusting the marker alone would reuse a checkout someone had since modified, which is
invariant 8's "commit-keyed, and only trusted on a clean tree" in another setting.

**The commit is fetched directly**, never a branch. ``git fetch --depth 1 origin <sha>`` asks
for the object we named; a branch can move between the fetch and the read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

#: A pin is a full 40-character object name. Abbreviations read as SHAs, resolve for a human,
#: and cannot be handed to `git fetch` — so they are refused where they are introduced rather
#: than failing later on a machine with a network.
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

_MARKER = ".spine-pin"


class CheckoutError(RuntimeError):
    """A repository could not be materialised at the commit that was asked for."""


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CheckoutError(f"git {' '.join(args)}: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _pin(url: str, sha: str) -> str:
    """The identity a checkout must match — URL *and* commit.

    Both: the same commit id in a different repository is a different tree, and a marker
    recording only the SHA would let a renamed entry reuse the wrong checkout.
    """
    return f"{url}@{sha}"


def verified(dest: Path, url: str, sha: str) -> bool:
    """Is ``dest`` a complete, unmodified checkout of exactly this commit?"""
    marker = dest / _MARKER
    if not (dest / ".git").exists() or not marker.exists():
        return False
    try:
        if marker.read_text(encoding="utf-8").strip() != _pin(url, sha):
            return False
        if _git("rev-parse", "HEAD", cwd=dest) != sha:
            return False
        return _git("status", "--porcelain", cwd=dest) == ""
    except (CheckoutError, OSError):
        return False  # unreadable, or not a repo → not verified, so rebuild


def materialize_at(url: str, sha: str, dest: Path | str, *, depth: int = 1) -> Path:
    """Put ``url`` at commit ``sha`` in ``dest``, verified, and return the path.

    Idempotent: a checkout that verifies is reused, anything else is torn down and refetched.
    Never reconciles a partial state — after a failure there is nothing to reconcile, because
    the marker that would have claimed completeness was never written.

    ``depth`` is 1 because every caller here reads a *tree*. Pass 2 to read the commit as a
    **change**: at depth 1 the commit has no parent, so ``git show --numstat`` reports every
    file in the repository as freshly added, which looks like a diff and is not one.
    """
    if not FULL_SHA.match(sha):
        raise CheckoutError(f"{sha!r} is not a full 40-character commit id")
    path = Path(dest)
    if verified(path, url, sha):
        return path

    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "--quiet", str(path))
    _git("remote", "add", "origin", url, cwd=path)
    # Depth 1 on the commit itself: a tree is all any caller here reads, and asking for the
    # object removes any question of which branch it sits on.
    _git("fetch", "--depth", str(depth), "--quiet", "origin", sha, cwd=path)
    _git("checkout", "--quiet", "FETCH_HEAD", cwd=path)

    head = _git("rev-parse", "HEAD", cwd=path)
    if head != sha:
        shutil.rmtree(path, ignore_errors=True)
        raise CheckoutError(f"fetched {head}, asked for {sha}")

    # Last. Everything above must have succeeded for this file to exist.
    (path / _MARKER).write_text(_pin(url, sha) + "\n", encoding="utf-8")
    return path


__all__ = ["FULL_SHA", "CheckoutError", "materialize_at", "verified"]
