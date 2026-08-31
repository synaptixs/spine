"""Materialise the pinned comprehension corpus — fetch, verify, then mark.

The G6 metrics are measured on five external repositories at fixed commits. Getting them onto
disk is the part with a failure mode worth designing around, because the metric that consumes
them is **ratcheted**: a partially-fetched repository scores lower simply because symbols are
missing, and that number is indistinguishable from a real regression. A bad fetch would not
misreport once — it would lower the bar permanently. **A measurement that cannot cover its
manifest did not happen**, so this module fails loudly rather than returning a usable-looking
tree.

**Fetch-and-persist, scoped to the task.** Not a long-lived shared cache: the lifetime is the
run, so nothing survives to be half-trusted by the next one. Within a run the corpus is fetched
once and reused by every metric.

**The marker is written last**, and that ordering is the whole design. ``WorkspaceManager``
established it — clone, *then* mark — so a process that dies mid-fetch leaves a directory that
does not read as complete, and the next attempt tears it down instead of measuring a truncated
tree. Reuse additionally re-verifies ``git rev-parse HEAD`` against the pin rather than trusting
the marker alone, which is the same discipline as invariant 8: caches are commit-keyed and
trusted only on a clean tree.

**The commit is fetched directly**, not a branch. ``git fetch --depth 1 origin <sha>`` asks for
the object we pinned; cloning a branch and hoping it still points there is how a corpus drifts
under a published number.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: The pinned corpus ships inside the package, for the reason `scoreboard.json` does: a
#: pip-installed Spine must be able to read the manifest it measures against.
MANIFEST = Path(__file__).with_name("comprehension_corpus.yaml")

#: A pin is a full 40-character object name. Abbreviations are refused at load — they read as
#: SHAs, resolve for a human, and cannot be handed to `git fetch`. (Written after padding four
#: abbreviations into plausible-looking 40-character strings while building this file: they
#: were well-formed, wrong, and nothing would have noticed until a fetch failed.)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

_MARKER = ".spine-corpus-pin"


class CorpusFetchError(RuntimeError):
    """A pinned repository could not be materialised at the commit the manifest names."""


@dataclass(frozen=True)
class PinnedRepo:
    """One entry of the comprehension corpus."""

    name: str
    language: str
    url: str
    sha: str
    why: str

    @property
    def pin(self) -> str:
        """The identity a materialised checkout must match — URL *and* commit.

        Both, because the same commit id in a different repository is a different tree, and a
        marker that recorded only the SHA would let a renamed entry reuse the wrong checkout.
        """
        return f"{self.url}@{self.sha}"


def load_manifest(path: Path | str = MANIFEST) -> list[PinnedRepo]:
    """Read the pinned corpus, refusing anything that cannot be reproduced.

    Every field is required and every SHA must be full-length. A manifest is the one artefact
    in this programme that nothing else can cross-check, so it is validated at the door rather
    than at the point where a bad value becomes a wrong number.
    """
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("repos"), list):
        raise CorpusFetchError(f"{path}: expected a mapping with a 'repos' list")
    out: list[PinnedRepo] = []
    seen: set[str] = set()
    for entry in raw["repos"]:
        if not isinstance(entry, dict):
            raise CorpusFetchError(f"{path}: every repo entry must be a mapping")
        missing = [k for k in ("name", "language", "url", "sha", "why") if not entry.get(k)]
        if missing:
            raise CorpusFetchError(f"{path}: entry {entry.get('name', '?')!r} is missing {missing}")
        sha = str(entry["sha"])
        if not _FULL_SHA.match(sha):
            raise CorpusFetchError(
                f"{path}: {entry['name']!r} pins {sha!r} — a pin must be a full 40-character "
                "commit id, not an abbreviation or a branch"
            )
        name = str(entry["name"])
        if name in seen:
            raise CorpusFetchError(f"{path}: duplicate repo name {name!r}")
        seen.add(name)
        out.append(
            PinnedRepo(
                name=name,
                language=str(entry["language"]),
                url=str(entry["url"]),
                sha=sha,
                why=str(entry["why"]),
            )
        )
    if not out:
        raise CorpusFetchError(f"{path}: no repositories declared")
    return out


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CorpusFetchError(f"git {' '.join(args)}: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _verified(dest: Path, repo: PinnedRepo) -> bool:
    """Is ``dest`` a complete checkout of exactly this pin?

    Marker present *and* matching, HEAD at the pinned commit, working tree clean. The marker
    alone is not enough — it records intent, and this asks the repository itself.
    """
    marker = dest / _MARKER
    if not (dest / ".git").exists() or not marker.exists():
        return False
    try:
        if marker.read_text(encoding="utf-8").strip() != repo.pin:
            return False
        if _git("rev-parse", "HEAD", cwd=dest) != repo.sha:
            return False
        return _git("status", "--porcelain", cwd=dest) == ""
    except (CorpusFetchError, OSError):
        return False  # unreadable or not a repo → not verified, so rebuild


def materialize(repo: PinnedRepo, root: Path | str) -> Path:
    """Put ``repo`` on disk at its pinned commit under ``root``, and return the path.

    Idempotent within a task: a checkout that verifies is reused, anything else is torn down
    and refetched. Never reconciles a partial state — after a failure there is nothing to
    reconcile, because the marker that would have claimed completeness was never written.
    """
    dest = Path(root) / repo.name
    if _verified(dest, repo):
        return dest

    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "--quiet", str(dest))
    _git("remote", "add", "origin", repo.url, cwd=dest)
    # Depth 1 on the commit itself: the whole history is not needed to extract a tree, and
    # asking for the object removes any question of which branch it sits on.
    _git("fetch", "--depth", "1", "--quiet", "origin", repo.sha, cwd=dest)
    _git("checkout", "--quiet", "FETCH_HEAD", cwd=dest)

    head = _git("rev-parse", "HEAD", cwd=dest)
    if head != repo.sha:
        shutil.rmtree(dest, ignore_errors=True)
        raise CorpusFetchError(f"{repo.name}: fetched {head}, manifest pins {repo.sha}")

    # Last. Everything above must have succeeded for this file to exist.
    (dest / _MARKER).write_text(repo.pin + "\n", encoding="utf-8")
    return dest


def materialize_all(root: Path | str, repos: list[PinnedRepo] | None = None) -> dict[str, Path]:
    """Every pinned repository, or none of them.

    All-or-nothing on purpose: a caller that scored the repositories which happened to
    materialise would publish a number whose denominator moved with the network, and ratchet
    the gate down on a bad afternoon.
    """
    entries = repos if repos is not None else load_manifest()
    return {r.name: materialize(r, root) for r in entries}


__all__ = [
    "MANIFEST",
    "CorpusFetchError",
    "PinnedRepo",
    "load_manifest",
    "materialize",
    "materialize_all",
]
