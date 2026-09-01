#!/usr/bin/env python3
"""Re-derive the numbers `STATE-OF-SPINE.md` states, and fail if the prose disagrees.

That page ends with a maintenance rule: *"A number here without a 'how it is known' is a number
that should not be here."* Every row in §2 carries its derivation in a column beside it. So the
derivations exist, they are written down — and until now nothing ran them.

**They rot, and they rot silently.** On 2026-09-01 alone, three derived figures were stale in
prose at the same time: the spec-file count, the CLI command count, and the version string in
four places. Two were caught only because an *unrelated* gate happened to fail — the capability
matrix count, and the architecture SVG that happens to render the version. Nothing was checking
the numbers themselves.

This closes that. It is deliberately narrow: it checks the claims whose derivation is a command,
not whether a spec's *status line* matches shipped reality. That second thing needs judgement and
is still open (`STATE-OF-SPINE` §8).

    python scripts/state-numbers.py            # print each claim, derived and stated
    python scripts/state-numbers.py --check    # non-zero if any disagree

**Why not parse the "how it is known" column and run it?** Because that column is prose written
for a human — it abbreviates, and one row's derivation spans two commands joined by a semicolon.
Executing prose from a document is also how a documentation change becomes arbitrary code
execution in CI. The derivations are re-implemented here instead, and the doc's column stays the
human-readable statement of the same thing.

**And re-implemented in Python rather than shelled out.** The first version ran the documented
`grep` through `sh` and got 307 test files where the same command in a terminal gave 296 — a
gate whose answer depends on the shell it is invoked from is not a gate. Reading the files
directly is deterministic, which is the property the rest of this pipeline is built on.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "docs" / "specs" / "STATE-OF-SPINE.md"
SPEC_INDEX = ROOT / "docs" / "specs" / "SPEC-INDEX.md"
WALKTHROUGH = ROOT / "docs" / "specs" / "doc-binding-walkthrough.md"


_TEST_DEF = re.compile(r"^(?:async )?def test_", re.M)


def _test_files() -> list[Path]:
    return sorted((ROOT / "tests").rglob("*.py"))


def cli_commands() -> int:
    return len(re.findall(r"\.command\(", (ROOT / "src" / "orchestrator" / "cli.py").read_text()))


def source_modules() -> int:
    return len(list((ROOT / "src" / "orchestrator").rglob("*.py")))


def test_functions() -> int:
    return sum(len(_TEST_DEF.findall(p.read_text(encoding="utf-8", errors="replace"))) for p in _test_files())


def test_files() -> int:
    return sum(1 for p in _test_files() if _TEST_DEF.search(p.read_text(encoding="utf-8", errors="replace")))


def spec_files() -> int:
    return len(list((ROOT / "docs" / "specs").glob("*.md")))


#: The doc-binding figures come from one expensive pass — a full extraction plus a doc walk,
#: about ten seconds — so it is done once and every claim reads from it.
_BINDING: dict[str, int] | None = None


def _binding() -> dict[str, int]:
    """Every mention, sorted into the four buckets the walkthrough tabulates.

    The buckets partition the mentions: a mention has exactly one symbol anchor, or more than
    one, or none but a file, or nothing. `link_docs` draws an edge only for the first, and the
    difference between that count and the edges drawn is de-duplication.

    That distinction is the whole reason these claims exist. The walkthrough's first table
    reported `DocBinding.bound` — true for a symbol anchor *or* a file anchor — as though it
    meant "will become an edge", inflating two figures and hiding the file-only bucket
    entirely. Nothing derived them, so it stood for a day.
    """
    global _BINDING
    if _BINDING is not None:
        return _BINDING

    from orchestrator.pkg.doc_link import link_docs
    from orchestrator.pkg.doc_source import read_doc_pages
    from orchestrator.pkg.docs import DocReconciler
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.facts import EdgeKind, NodeKind

    batch = RepoCodeExtractor().extract(ROOT)
    bindings, drift = DocReconciler(batch, repo_root=ROOT).reconcile(read_doc_pages(ROOT))
    linked = link_docs(RepoCodeExtractor().extract(ROOT), ROOT)

    _BINDING = {
        "mentions": len(bindings),
        "one_symbol": sum(1 for b in bindings if len(b.anchor_ids) == 1),
        "many_symbols": sum(1 for b in bindings if len(b.anchor_ids) > 1),
        "file_only": sum(1 for b in bindings if not b.anchor_ids and b.anchor_files),
        "nothing": sum(1 for b in bindings if not b.anchor_ids and not b.anchor_files),
        "edges": sum(1 for e in linked.edges if e.kind is EdgeKind.MENTIONS),
        "doc_nodes": sum(1 for n in linked.nodes if n.kind is NodeKind.DOC),
        # The audit box in the walkthrough quotes this. It is prose, nothing derived it, and
        # the two figures beside it went stale within the hour they were written — the same
        # way every hand-carried number in this repository has.
        "drift": len(drift),
    }
    return _BINDING


def ts_calls_recall() -> str:
    """TypeScript `CALLS` recall, to two places, read from the committed scoreboard.

    Chained deliberately: source → scoreboard (gated by `pkg accuracy --check`) → prose (gated
    here). The scoreboard cannot go stale against the code, so a claim that matches it is a
    claim that matches the code.

    This figure moved three times on 2026-09-01 — a stale 0.50 corrected to 0.571, then 0.357
    when the corpus doubled — while being quoted in the README and in three places on
    `STATE-OF-SPINE`. It is the clearest case in the repository for deriving a number instead
    of carrying it.
    """
    import json

    board = json.loads((ROOT / "src" / "orchestrator" / "pkg" / "scoreboard.json").read_text())
    calls = board["metrics"]["corpus"]["languages"]["typescript"]["edges"]["CALLS"]
    return f"{calls['matched'] / calls['expected']:.2f}"


def package_version() -> str:
    import tomllib

    return str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])


@dataclass(frozen=True)
class Claim:
    """One number a document states, and the way to derive it independently."""

    label: str
    path: Path
    #: Captures the stated value in group 1. Written to match the row, not the whole table, so a
    #: reworded sentence around it does not silently stop the check from finding anything —
    #: a pattern that matches nothing is reported as a failure, never as a pass.
    pattern: re.Pattern[str]
    derive: Callable[[], object]
    #: Digits only: the prose writes thousands with a comma, and the derivation does not.
    numeric: bool = True
    #: Whether a mismatch fails the build.
    #:
    #: The doc-binding figures are **not** gated, and measuring is what decided that: they moved
    #: within an hour of being written, because they count mentions across every markdown file
    #: and anchors across every symbol — so any commit touching either moves them. Gating that
    #: would fail nearly every pull request for refreshing seven numbers in a walkthrough, which
    #: is the failure that killed the doc-drift ratchet a day earlier: a gate firing on ordinary
    #: work gets switched off, and then it protects nothing.
    #:
    #: They are still derived and still reported, which is what catches the error they exist
    #: for — a figure wrong *when written*, as opposed to one that has merely aged.
    gated: bool = True


CLAIMS: tuple[Claim, ...] = (
    Claim("CLI commands", STATE, re.compile(r"\| CLI commands \| \*\*([\d,]+)\*\*"), cli_commands),
    Claim("source modules", STATE, re.compile(r"\| Source modules \| \*\*([\d,]+)\*\*"), source_modules),
    Claim(
        "test functions",
        STATE,
        re.compile(r"\| Test functions \| \*\*([\d,]+)\*\* across"),
        test_functions,
    ),
    Claim(
        "test files",
        STATE,
        re.compile(r"\| Test functions \| \*\*[\d,]+\*\* across ([\d,]+) files"),
        test_files,
    ),
    Claim("spec files (STATE)", STATE, re.compile(r"holds \*\*([\d,]+)\*\* markdown files"), spec_files),
    Claim(
        "spec files (SPEC-INDEX prose)",
        SPEC_INDEX,
        re.compile(r"holds \*\*([\d,]+)\*\* markdown files"),
        spec_files,
    ),
    Claim(
        "spec files (SPEC-INDEX command)",
        SPEC_INDEX,
        re.compile(r"ls docs/specs/\*\.md \| wc -l\s+# ([\d,]+)"),
        spec_files,
    ),
    Claim(
        "version",
        STATE,
        re.compile(r"\| Version \| \*\*([\d.]+)\*\*"),
        package_version,
        numeric=False,
    ),
    Claim(
        "TypeScript CALLS recall (STATE §2)",
        STATE,
        re.compile(r"\| `CALLS` recall \| \*\*1\.00\*\* \(C, SQL\) → \*\*([\d.]+)\*\*"),
        ts_calls_recall,
        numeric=False,
    ),
    Claim(
        "TypeScript CALLS recall (STATE §3)",
        STATE,
        re.compile(r"· \*\*([\d.]+) \(typescript\)\*\*"),
        ts_calls_recall,
        numeric=False,
    ),
    Claim(
        "TypeScript CALLS recall (README)",
        ROOT / "README.md",
        re.compile(r"down to ([\d.]+) on TypeScript"),
        ts_calls_recall,
        numeric=False,
    ),
    # The doc-binding walkthrough. Every figure below is a partition of the same population, so
    # a miscategorisation moves two of them in opposite directions — which is exactly what went
    # unnoticed for a day when nothing derived them.
    Claim(
        "binding: total mentions",
        WALKTHROUGH,
        re.compile(r"\| \*\*total mentions\*\* \| \*\*([\d,]+)\*\*"),
        lambda: _binding()["mentions"],
        gated=False,
    ),
    Claim(
        "binding: one symbol anchor",
        WALKTHROUGH,
        re.compile(r"\| \*\*one symbol anchor → `MENTIONS` edge\*\* \| \*\*([\d,]+)\*\*"),
        lambda: _binding()["one_symbol"],
        gated=False,
    ),
    Claim(
        "binding: ambiguous",
        WALKTHROUGH,
        re.compile(r"\| more than one symbol anchor → skipped \| ([\d,]+) \|"),
        lambda: _binding()["many_symbols"],
        gated=False,
    ),
    Claim(
        "binding: file only",
        WALKTHROUGH,
        re.compile(r"\| resolved to a \*\*file\*\*, no symbol \| ([\d,]+) \|"),
        lambda: _binding()["file_only"],
        gated=False,
    ),
    Claim(
        "binding: no anchor",
        WALKTHROUGH,
        re.compile(r"\| nothing at all \| ([\d,]+) \|"),
        lambda: _binding()["nothing"],
        gated=False,
    ),
    Claim(
        "binding: edges drawn",
        WALKTHROUGH,
        re.compile(r"\n([\d,]+) edges are drawn from those"),
        lambda: _binding()["edges"],
        gated=False,
    ),
    Claim(
        "binding: drift findings",
        WALKTHROUGH,
        re.compile(r"\*\*[\d,]+ → ([\d,]+)\*\* and \*\*not one"),
        lambda: _binding()["drift"],
        gated=False,
    ),
    Claim(
        "binding: Doc nodes",
        WALKTHROUGH,
        re.compile(r"\*\*Measured here: ([\d,]+) `Doc` nodes\*\*"),
        lambda: _binding()["doc_nodes"],
        gated=False,
    ),
)


def check(*, gated_only: bool = True) -> list[str]:
    """Claims that disagree with their derivation, or that could not be found at all.

    ``gated_only`` keeps the ungated ones out of the build's verdict while still allowing a
    caller to ask for everything — which is how they are reported as a trend.
    """
    problems: list[str] = []
    for claim in CLAIMS:
        if gated_only and not claim.gated:
            continue
        text = claim.path.read_text(encoding="utf-8")
        found = claim.pattern.search(text)
        if found is None:
            # Not a pass. A pattern that stops matching is how a check quietly stops checking —
            # the failure this whole page exists to catch, one level up.
            problems.append(f"{claim.label}: no longer found in {claim.path.name} — has it been reworded?")
            continue
        stated = found.group(1).replace(",", "") if claim.numeric else found.group(1)
        actual = str(claim.derive())
        if stated != actual:
            problems.append(f"{claim.label}: {claim.path.name} says {stated}, the source says {actual}")
    return problems


def main() -> int:
    problems = check()
    if "--check" not in sys.argv:
        for claim in CLAIMS:
            text = claim.path.read_text(encoding="utf-8")
            found = claim.pattern.search(text)
            stated = found.group(1) if found else "NOT FOUND"
            print(f"  {claim.label:32s} stated {stated:>10s}   derived {claim.derive()!s:>10s}")
        return 0
    for problem in problems:
        print(f"[STALE] {problem}")
    # Ungated claims are reported and never fail — the same standing `invention` and `drift`
    # have, and for the same reason: they move with ordinary commits.
    for drifted in [p for p in check(gated_only=False) if p not in problems]:
        print(f"[trend] {drifted}")
    if problems:
        print(f"\nstate-numbers --check: FAILED — {len(problems)} claim(s) disagree with the source.")
        print("Refresh them, or the page's own maintenance rule is a rule nothing enforces.")
        return 1
    gated = sum(1 for c in CLAIMS if c.gated)
    print(f"state-numbers --check: OK — {gated} gated claim(s) match; {len(CLAIMS) - gated} trended.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
