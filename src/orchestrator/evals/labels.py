"""The G6 gold set — hand-labelled issues, and the rules that keep them re-derivable.

G6 D1 chose a hand-labelled gold set over mined pull requests, because a mined PR touches
files unrelated to its fix and a poor localization score could not then be attributed to Spine
rather than to the label. That choice only pays if the labels are **independent of the system
under test** and **re-derivable by someone else**, so this module enforces both at load rather
than at the point where a bad label becomes a wrong number.

**Independence.** Ground truth comes from the commit that fixed the issue: what that commit
changed *is* the answer, which is git rather than anyone's opinion. A candidate proposed by
reading the ticket the way ``investigate`` reads it would not be independent — it would score
agreement between two readers of the same clues and report it as accuracy.

**Re-derivability.** Every label carries the issue URL and the full fixing commit, so a reader
can check any row against the upstream repository. Abbreviated commits are refused for the same
reason the corpus manifest refuses them: they read as commits, resolve for a human, and cannot
be handed to git.

**Exclusions are recorded, not dropped.** An issue examined and set aside is data — it says what
the corpus does not cover. Silently omitting it is how a benchmark comes to imply coverage it
never had.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orchestrator.core.pinned_checkout import FULL_SHA

#: Ships inside the package, like the corpus manifest and the scoreboard: a pip-installed
#: Spine must be able to read the gold set it scores against.
LABELS = Path(__file__).with_name("comprehension_labels.yaml")

_ISSUE_URL = re.compile(r"^https?://\S+$")


class LabelError(ValueError):
    """A gold-set entry that cannot be trusted or reproduced."""


@dataclass(frozen=True)
class FixSite:
    """Where the fix landed. ``path`` is the unit scored; ``symbol`` is context.

    Path-level because that is what a reader can check and what ``investigate`` can be held
    to: it returns symbols, and a symbol's file is unambiguous. Symbol-level scoring is a
    stricter question that a later phase can ask of the same labels.
    """

    path: str
    symbol: str = ""


@dataclass(frozen=True)
class Label:
    """One issue, its fix, and where the fix landed."""

    repo: str  # a name from the corpus manifest, never a URL
    issue: str
    title: str  # the ticket text `investigate` is given — stored so a re-run needs no network
    fix_commit: str
    fix_sites: tuple[FixSite, ...]
    body: str = ""
    note: str = ""

    @property
    def paths(self) -> set[str]:
        return {s.path for s in self.fix_sites}


@dataclass(frozen=True)
class Excluded:
    """An issue examined and set aside, with the reason — coverage, stated."""

    repo: str
    issue: str
    reason: str


@dataclass(frozen=True)
class GoldSet:
    """The labelled corpus: what is scored, and what was deliberately not."""

    labels: tuple[Label, ...] = ()
    excluded: tuple[Excluded, ...] = field(default=())

    @property
    def measured(self) -> bool:
        """False when nothing is labelled — a zero that means "not yet", not "bad"."""
        return bool(self.labels)


def _require(entry: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if not entry.get(k)]
    if missing:
        raise LabelError(f"{where}: missing {missing}")


def load_labels(path: Path | str = LABELS, *, known_repos: set[str] | None = None) -> GoldSet:
    """Read the gold set, refusing anything a reader could not check.

    ``known_repos`` cross-checks each label against the corpus manifest, so a typo in a repo
    name is refused here rather than silently scoring zero issues for a repository that does
    not exist.
    """
    text = Path(path).read_text(encoding="utf-8")
    raw: Any = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise LabelError(f"{path}: expected a mapping")

    labels: list[Label] = []
    for i, entry in enumerate(raw.get("labels") or []):
        where = f"{path}: label {i}"
        if not isinstance(entry, dict):
            raise LabelError(f"{where}: expected a mapping")
        _require(entry, ("repo", "issue", "title", "fix_commit", "fix_sites"), where)
        sha = str(entry["fix_commit"])
        if not FULL_SHA.match(sha):
            raise LabelError(
                f"{where}: fix_commit {sha!r} must be a full 40-character commit id — an "
                "abbreviation reads as a commit and cannot be handed to git"
            )
        if not _ISSUE_URL.match(str(entry["issue"])):
            raise LabelError(f"{where}: issue must be a URL a reader can open")
        repo = str(entry["repo"])
        if known_repos is not None and repo not in known_repos:
            raise LabelError(f"{where}: repo {repo!r} is not in the corpus manifest ({sorted(known_repos)})")
        sites = entry["fix_sites"]
        if not isinstance(sites, list) or not sites:
            raise LabelError(f"{where}: fix_sites must be a non-empty list")
        parsed: list[FixSite] = []
        for site in sites:
            if isinstance(site, str):
                parsed.append(FixSite(path=site))
                continue
            if not isinstance(site, dict) or not site.get("path"):
                raise LabelError(f"{where}: every fix site needs a path")
            parsed.append(FixSite(path=str(site["path"]), symbol=str(site.get("symbol", ""))))
        labels.append(
            Label(
                repo=repo,
                issue=str(entry["issue"]),
                title=str(entry["title"]),
                fix_commit=sha,
                fix_sites=tuple(parsed),
                body=str(entry.get("body", "")),
                note=str(entry.get("note", "")),
            )
        )

    excluded: list[Excluded] = []
    for i, entry in enumerate(raw.get("excluded") or []):
        where = f"{path}: excluded {i}"
        if not isinstance(entry, dict):
            raise LabelError(f"{where}: expected a mapping")
        _require(entry, ("repo", "issue", "reason"), where)
        excluded.append(
            Excluded(repo=str(entry["repo"]), issue=str(entry["issue"]), reason=str(entry["reason"]))
        )

    seen: set[str] = set()
    for label in labels:
        if label.issue in seen:
            raise LabelError(f"{path}: {label.issue} is labelled twice")
        seen.add(label.issue)

    return GoldSet(labels=tuple(labels), excluded=tuple(excluded))


def gold_digest(gold: GoldSet) -> str:
    """A stable fingerprint of the labels being scored.

    The gate compares localization only when this is unchanged, and that condition is the whole
    design. Localization is a **ratio over a fixed denominator**: add one label the tool gets
    wrong and every rate falls, so a gate comparing rates across different gold sets would fail
    a pull request **for growing the corpus** — the exact mistake that killed the doc-drift gate
    one day earlier, where the denominator did not move and every added claim read as a
    regression.

    So the gate holds `investigate` to account and stays silent about corpus growth, which is a
    rebaseline rather than a regression.

    Ordering-independent and content-addressed: what is scored, not how the file is arranged.
    """
    import hashlib

    parts = sorted(
        f"{label.repo}|{label.issue}|{label.fix_commit}|{','.join(sorted(label.paths))}"
        for label in gold.labels
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def unresolvable_paths(label: Label, root: Path | str) -> list[str]:
    """Labelled paths that do not exist in the checked-out tree.

    The likeliest labelling mistake, and a silent one: naming a file the fix *created*. The
    corpus is pinned at a commit **before** these fixes, so a path introduced by the fix is not
    in the tree ``investigate`` searches, and no run could ever have found it — the label would
    quietly cost a point it was never possible to score.
    """
    base = Path(root)
    return sorted(p for p in label.paths if not (base / p).exists())


__all__ = [
    "LABELS",
    "Excluded",
    "FixSite",
    "GoldSet",
    "Label",
    "LabelError",
    "gold_digest",
    "load_labels",
    "unresolvable_paths",
]
