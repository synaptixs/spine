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

The materialisation itself — fetch the commit directly, verify ``rev-parse``, write the marker
last — lives in :mod:`orchestrator.core.pinned_checkout`, shared with the code-review checkout.
One copy, because two would drift and the failure mode is a well-formed false number.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from orchestrator.core.pinned_checkout import FULL_SHA, CheckoutError, materialize_at

#: The pinned corpus ships inside the package, for the reason `scoreboard.json` does: a
#: pip-installed Spine must be able to read the manifest it measures against.
MANIFEST = Path(__file__).with_name("comprehension_corpus.yaml")


class CorpusFetchError(CheckoutError):
    """A pinned repository could not be materialised at the commit the manifest names.

    A :class:`CheckoutError`, so a caller that only cares "the corpus is not usable" can catch
    the base and a caller that wants to say *which manifest entry* can catch this.
    """


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
        if not FULL_SHA.match(sha):
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


def materialize(repo: PinnedRepo, root: Path | str) -> Path:
    """Put ``repo`` on disk at its pinned commit under ``root``, and return the path.

    Naming which entry failed, because "fetched X, asked for Y" is unactionable when five
    repositories are being materialised in a loop.
    """
    try:
        return materialize_at(repo.url, repo.sha, Path(root) / repo.name)
    except CheckoutError as exc:
        raise CorpusFetchError(f"{repo.name}: {exc}") from exc


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
