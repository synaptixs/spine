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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import SYMBOL_KINDS, Edge, EdgeKind, FactBatch, Node, NodeKind

# Deliberately generic: `SSPN-49`, `PROJ-1`, `AB1-23`. No repository outside this one uses this
# project's prefix, so a hard-coded key pattern would make the feature useless everywhere else.
DEFAULT_KEY_PATTERN = r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"

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
    batch: FactBatch, root: Path | str, *, key_pattern: str = DEFAULT_KEY_PATTERN
) -> IntentCoverage:
    """Add ``Intent`` nodes and ``SERVES`` edges to ``batch``. Mutates in place.

    Deterministic for a given history: no timestamps, no wall-clock, and commits are identified
    by hash. The same tree at the same commit yields the same facts.
    """
    base = Path(root)
    pattern = re.compile(key_pattern)
    key_cache = _all_commit_keys(base, pattern)

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
    )


__all__ = ["DEFAULT_KEY_PATTERN", "IntentCoverage", "link_intents"]
