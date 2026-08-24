#!/usr/bin/env python3
"""Count the capability matrix, and fail if the prose around it disagrees.

`docs/specs/capability-matrix.md` opens by warning that a hand-authored matrix in this project
was once **22% wrong with nothing failing**. On 2026-08-21 its own summary said **16 rows where
Spine stands alone** while `STATE-OF-SPINE.md` said **11**, for the same table on the same day.
Both were wrong; the answer was 22. The warning was accurate and the document it guards was not
exempt from it.

A count nobody can re-derive is a claim, not a measurement. This derives it.

    python scripts/matrix-count.py            # print the counts
    python scripts/matrix-count.py --check    # non-zero if the prose disagrees

**What "stands alone" means here, precisely.** Spine is ✅ or 🟡 and *every* competitor cell is
➖ or n/a. ➖ means "not found in public docs" — never "proven absent" — so this counts rows
where no competitor's public documentation describes the capability, which is a weaker claim
than the phrase suggests and is the strongest one the source material supports.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MATRIX = Path(__file__).resolve().parent.parent / "docs" / "specs" / "capability-matrix.md"
STATE = Path(__file__).resolve().parent.parent / "docs" / "specs" / "STATE-OF-SPINE.md"

# The competitor columns start after the capability name and Spine's own cell.
_MIN_CELLS = 9


def rows(text: str) -> list[tuple[str, str, list[str]]]:
    """(capability, Spine's cell, competitor cells) for every scored row."""
    out: list[tuple[str, str, list[str]]] = []
    for line in text.split("\n"):
        if not line.startswith("|") or line.startswith("|---") or "Capability" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Layer headings are single-cell rows spanning the table; they score nothing.
        if len(cells) < _MIN_CELLS:
            continue
        out.append((cells[0], cells[1], cells[2:]))
    return out


def stands_alone(spine: str, others: list[str]) -> bool:
    return ("✅" in spine or "🟡" in spine) and all("➖" in c or "n/a" in c for c in others)


def main() -> int:
    scored = rows(MATRIX.read_text(encoding="utf-8"))
    if not scored:
        print("matrix-count: no scored rows found — has the table format changed?", file=sys.stderr)
        return 2

    total = len(scored)
    alone = [r for r in scored if stands_alone(r[1], r[2])]
    absent = [r for r in scored if "❌" in r[1]]
    partial = [r for r in scored if "🟡" in r[1]]

    print(f"{total} capability rows")
    print(f"{len(alone)} where Spine stands alone")
    print(f"{len(absent)} where Spine is ❌")
    print(f"{len(partial)} at 🟡")

    if "--check" not in sys.argv:
        return 0

    # The prose has to agree with the table it sits under. Both documents claim the number, so
    # both are checked — the drift that prompted this script was between them, not within one.
    failures: list[str] = []
    for path, pattern, expected, what in (
        (MATRIX, r"\*\*(\d+) rows where Spine stands alone\*\*", len(alone), "stands-alone"),
        (MATRIX, r"\*\*(\d+) rows where Spine is ❌\*\*", len(absent), "absent"),
        (STATE, r"\*\*Ahead — (\d+) rows in the capability matrix", len(alone), "stands-alone"),
    ):
        found = re.search(pattern, path.read_text(encoding="utf-8"))
        if found is None:
            failures.append(f"{path.name}: could not find the {what} count to check")
        elif int(found.group(1)) != expected:
            failures.append(f"{path.name}: says {found.group(1)} {what} rows, the table has {expected}")

    for line in failures:
        print(f"matrix-count: {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
