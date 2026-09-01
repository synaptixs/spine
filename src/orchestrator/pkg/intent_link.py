"""The recorded intent tier — which ticket a symbol was last changed for.

The graph's vocabulary is mechanical: it says what calls what, never what anything is *for*.
That meaning already exists in this system — in tickets, commit messages, build documents — and
none of it points at a symbol. This joins one half to the other, deterministically, with no
model: git says a commit last touched these lines, and the commit message names an issue key.

**Blame, not hunk ranges — and the direction matters.** The obvious implementation walks
``git log`` for keyed commits and joins their changed line ranges to symbol provenance. It is
wrong: a commit's line numbers are as-of-that-commit, while provenance is against today's tree,
and lines drift with every edit above them. ``git blame`` maps *current* lines to the commits
that last touched them, which is the direction the join actually needs.

**Two things this does not claim.**

*"Last changed for", not "built for".* Blame reports the most recent commit to touch a line.
Recovering the commit that *introduced* a symbol needs ``git log -L`` per symbol — accurate and
one subprocess per symbol. The roadmap wants both eventually; this ships the cheap half, and
says which half it is.

*Coverage depends entirely on the repository.* Measured here: **11.8% of symbols** (1,172 of
9,969) reach an intent, from 34 tickets. The line-level rate is lower — 2.8% — because a symbol
counts as attributed when *any* keyed commit touched *any* line of its span, which is the right
semantics: a function edited for SSPN-49 serves SSPN-49 whether that was one line or forty.

Neither figure is a limit of the method. This repository's history was squashed on import from
a private one, so most surviving lines trace to a handful of large import commits carrying no
keys at all — ``git blame`` names them, and they say nothing. A repository developed in the
open, with issue keys in its commit messages, attributes far more. The rate is reported on
every run rather than hidden, because a coverage figure a reader cannot see is exactly the
failure mode this roadmap exists to prevent.

Issue keys come from **commit messages**, not branch names. The roadmap specifies branch names
via ``_issue_key_from_branch``, which is exact for branches the SDLC pipeline generates and
almost empty in practice: of 465 commits here, 102 carry a key in the message across 45
distinct intents, while exactly one branch matches ``feat/<sdlc_id>/<ISSUE-KEY>``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import SYMBOL_KINDS, Edge, EdgeKind, FactBatch, Node, NodeKind

# Deliberately generic: `SSPN-49`, `PROJ-1`, `AB1-23`. No repository outside this one uses this
# project's prefix, so a hard-coded key pattern would make the feature useless everywhere else.
#
# Generic is also why it over-matches. On this repository it read `SHA-256`, `ISO-8601`,
# `UTF-16`, `CVE-2024`, `CHANGE-2046` and `CB-676` as tickets — 5 of 37 intents and 92 of 1,418
# edges asserting a symbol was changed for a ticket nobody filed. The join was right; treating
# every match as an issue key was not. `_dominant_prefix` is the answer, and the group is
# captured here so the prefix can be read out of a match.
DEFAULT_KEY_PATTERN = r"\b([A-Z][A-Z0-9]{1,9})-\d+\b"

_BLAME_LINE = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)", re.M)
_GIT_TIMEOUT = 120

# `git blame` is one subprocess per file and dominates the scan: 23.3s of 25s on this
# repository, 587 files at ~40ms each. The work is I/O-bound — a process starting, git
# reading, the GIL released throughout — so threads help where they usually would not.
# Measured: 8 workers takes it to 8.1s; 16 is *worse* (13.8 vs 16.6 ms/file) because the
# contention costs more than the extra concurrency buys.
_BLAME_WORKERS = 8


@dataclass(frozen=True)
class IntentCoverage:
    """How much of the graph the recorded tier could actually reach.

    Reported on every run. ``symbols_total`` is the denominator that matters: a tier that
    attributes 12 of 4,000 symbols is working exactly as designed and still says almost
    nothing, and only the ratio makes that visible.
    """

    intents: int
    serves: int
    symbols_total: int
    symbols_attributed: int
    commits_scanned: int
    commits_keyed: int
    #: The prefixes accepted as issue keys, and the ones seen and rejected as not tickets.
    #: Reported so an operator can see what was inferred and override it — an inference nobody
    #: can inspect is a guess wearing a measurement's clothes.
    prefixes_used: tuple[str, ...] = ()
    prefixes_rejected: tuple[str, ...] = ()

    @property
    def rate(self) -> float | None:
        return self.symbols_attributed / self.symbols_total if self.symbols_total else None


def _git(root: Path, *args: str) -> str | None:
    """Run git; ``None`` on any failure. A shallow clone or a non-repo yields no intents."""
    try:
        proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _dominant_prefix(keys: dict[str, str | None]) -> tuple[str | None, list[str]]:
    """The repository's issue-key prefix, and every prefix rejected as not one.

    **A tracker prefix appears with many distinct numbers; a standard appears with one.**
    `SSPN` is used with 45 different numbers here; `SHA` only ever with `256`, `ISO` only with
    `8601`, `CVE` only with `2024`. That is the shape of the data rather than a list of
    standards to maintain, and it needs no denylist to keep current.

    **A single dominant prefix, not a threshold.** A cut-off of "2 or more distinct numbers"
    keeps `UTF` (8 and 16); "3 or more" discards the legitimate `WI-2`. Any constant between
    them is the arbitrary tolerance band `pkg/accuracy.py`'s GATES argues against — one that
    eventually fires on something legitimate and gets widened until it means nothing. A
    repository has one issue tracker, and here it wins by 45 distinct numbers to 2, so the
    maximum is the answer and no band is needed.

    The one floor: the winner must have **at least two** distinct numbers. A prefix seen with
    exactly one number is indistinguishable from a standard, so a repository whose leader has
    only one is a repository with no discernible tracker — and gets no intents rather than a
    coin-flip.

    A repository with genuinely two trackers, or one whose history is too thin for the
    inference, passes ``prefixes`` explicitly. This is the default, not the mechanism.
    """
    numbers: dict[str, set[str]] = {}
    for key in keys.values():
        if not key:
            continue
        prefix, _, number = key.rpartition("-")
        numbers.setdefault(prefix, set()).add(number)
    if not numbers:
        return None, []
    winner = max(numbers, key=lambda p: (len(numbers[p]), p))
    if len(numbers[winner]) < 2:
        return None, sorted(numbers)
    return winner, sorted(p for p in numbers if p != winner)


def _all_commit_keys(root: Path, pattern: re.Pattern[str]) -> dict[str, str | None]:
    """``{sha: issue key or None}`` for the whole history, in one subprocess.

    Was one `git log -1` per distinct commit — 137 of them here, ~14ms each, 1.9s. Small
    beside blame, but it is a subprocess per commit for information one command already
    returns in full.
    """
    out = _git(root, "log", "--format=%H%x1f%s%n%b%x1e")
    if not out:
        return {}
    keys: dict[str, str | None] = {}
    for record in out.split("\x1e"):
        sha, sep, message = record.strip().partition("\x1f")
        if not sep or len(sha) != 40:
            continue
        found = pattern.search(message)
        keys[sha] = found.group(0) if found else None
    return keys


def _blame(root: Path, rel: str) -> dict[int, str]:
    """``{line: sha}`` for one file. Empty when the file is untracked or git is unavailable."""
    out = _git(root, "blame", "--line-porcelain", "--", rel)
    if not out:
        return {}
    return {int(line): sha for sha, line in _BLAME_LINE.findall(out)}


def link_intents(
    batch: FactBatch,
    root: Path | str,
    *,
    key_pattern: str = DEFAULT_KEY_PATTERN,
    prefixes: Sequence[str] | None = None,
) -> IntentCoverage:
    """Add ``Intent`` nodes and ``SERVES`` edges to ``batch``. Mutates in place.

    Deterministic for a given history: no timestamps, no wall-clock, and commits are identified
    by hash. The same tree at the same commit yields the same facts.

    ``prefixes`` names the issue-key prefixes to accept. Omit it and the repository's dominant
    prefix is inferred — see :func:`_dominant_prefix` for why that is a maximum and not a
    threshold. Pass it when a repository has two trackers, or when the inference is wrong and
    you can see that it is, because the coverage report says what it chose.
    """
    base = Path(root)
    pattern = re.compile(key_pattern)
    key_cache = _all_commit_keys(base, pattern)

    if prefixes is not None:
        allowed: set[str] = set(prefixes)
        rejected: list[str] = []
    else:
        winner, rejected = _dominant_prefix(key_cache)
        allowed = {winner} if winner else set()
    # Drop every key outside the allow-list *before* the join, so a rejected prefix cannot
    # reach a node, an edge or the attributed count.
    key_cache = {
        sha: (key if key and key.rpartition("-")[0] in allowed else None) for sha, key in key_cache.items()
    }

    # Only symbols carry intent. A Module is a file, and attributing a whole file to whichever
    # ticket last touched line 1 of it would be noise wearing a provenance label.
    symbols = [n for n in batch.nodes if n.grounded and n.provenance is not None and n.kind in SYMBOL_KINDS]

    # Blame every file holding a symbol, concurrently, before the join. Doing it inside the
    # per-symbol loop serialised 587 subprocesses behind each other for no reason.
    wanted = sorted({n.provenance.file for n in symbols if n.provenance})
    with ThreadPoolExecutor(max_workers=_BLAME_WORKERS) as pool:
        blame_cache = dict(zip(wanted, pool.map(lambda rel: _blame(base, rel), wanted), strict=True))

    attributed = 0
    intents: dict[str, Node] = {}
    for node in symbols:
        prov = node.provenance
        if prov is None:  # pragma: no cover - excluded by the filter above
            continue
        lines = blame_cache.get(prov.file) or {}
        if not lines:
            continue

        # The symbol's own span, so a function is attributed to the tickets that touched its
        # body — not only to whatever last edited its `def` line.
        span = range(prov.line, (prov.end_line or prov.line) + 1)
        keys = {key for line in span if (sha := lines.get(line)) and (key := key_cache.get(sha))}
        if keys:
            attributed += 1
        for key in sorted(keys):
            intent_id = f"intent:{key}"
            if intent_id not in intents:
                # No provenance: an Intent is not a place in a file. `grounded` is therefore
                # False, so `pkg verify`'s provenance check skips it rather than failing on a
                # locator that was never meant to resolve.
                intents[intent_id] = Node(intent_id, NodeKind.INTENT, key, "", None)
                batch.add_node(intents[intent_id])
            batch.add_edge(Edge(node.id, intent_id, EdgeKind.SERVES, prov))

    keyed = sum(1 for v in key_cache.values() if v)
    return IntentCoverage(
        intents=len(intents),
        serves=sum(1 for e in batch.edges if e.kind is EdgeKind.SERVES),
        symbols_total=len(symbols),
        symbols_attributed=attributed,
        commits_scanned=len(key_cache),
        commits_keyed=keyed,
        prefixes_used=tuple(sorted(allowed)),
        prefixes_rejected=tuple(rejected),
    )


__all__ = ["DEFAULT_KEY_PATTERN", "IntentCoverage", "link_intents"]
