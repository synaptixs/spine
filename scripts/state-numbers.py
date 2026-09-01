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
)


def check() -> list[str]:
    """Every claim that disagrees with its derivation, or that could not be found at all."""
    problems: list[str] = []
    for claim in CLAIMS:
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
    if problems:
        print(f"\nstate-numbers --check: FAILED — {len(problems)} claim(s) disagree with the source.")
        print("Refresh them, or the page's own maintenance rule is a rule nothing enforces.")
        return 1
    print(f"state-numbers --check: OK — {len(CLAIMS)} claims match the source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
