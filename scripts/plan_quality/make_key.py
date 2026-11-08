"""An answer key from git, so nobody hand-picks what the right answer was.

    python scripts/plan_quality/make_key.py <repo> <ticket> <base> --commits h1,h2,... <out.json>
    python scripts/plan_quality/make_key.py <repo> <ticket> <base> <fix> <out.json>

Fix files are the ``src`` Python files the fix changed. Changed symbols are the functions and classes
named in the diff's hunk headers (git's own function context). ``--commits`` takes the specific
single-bug commits ``select_set.py`` attributed to the ticket; the other form takes a commit range.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_FILE = re.compile(r"^\+\+\+ b/(\S+)")
_HUNK = re.compile(r"^@@[^@]*@@\s*(?:async\s+)?(?:def|class)\s+(\w+)")


def _git(repo: Path | str, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def symbols_in(diff: str) -> dict[str, set[str]]:
    """File → functions/classes its hunks fall in, for the Python files of a ``-U0`` diff."""
    out: dict[str, set[str]] = {}
    current: str | None = None
    for line in diff.splitlines():
        m = _FILE.match(line)
        if m:
            current = m.group(1) if m.group(1).endswith(".py") else None
            if current:
                out.setdefault(current, set())
            continue
        h = _HUNK.match(line)
        if current and h:
            out[current].add(h.group(1))
    return out


def key_from(
    repo: Path | str, ticket: str, base: str, *, commits: list[str] | None = None, fix: str | None = None
) -> dict[str, Any]:
    if commits:
        diffs = [_git(repo, "show", "-U0", "--format=", h, "--", "src") for h in commits]
        fix_ref = ",".join(commits)
    elif fix:
        diffs = [_git(repo, "diff", "-U0", base, fix, "--", "src")]
        fix_ref = fix
    else:
        raise ValueError("give commits or a fix ref")
    merged: dict[str, set[str]] = {}
    for d in diffs:
        for f, names in symbols_in(d).items():
            merged.setdefault(f, set()).update(names)
    return {
        "ticket": ticket,
        "target": base,
        "fix": fix_ref,
        "fix_files": sorted(merged),
        "doc_only_files": [],
        "changed_symbols": {f: sorted(names) for f, names in sorted(merged.items())},
        "derived_by": "make_key.py (git hunk headers)",
    }


def main(argv: list[str]) -> int:
    if len(argv) == 7 and argv[4] == "--commits":
        key = key_from(argv[1], argv[2], argv[3], commits=argv[5].split(","))
        out = argv[6]
    elif len(argv) == 6:
        key = key_from(argv[1], argv[2], argv[3], fix=argv[4])
        out = argv[5]
    else:
        print(__doc__, file=sys.stderr)
        return 2
    Path(out).write_text(json.dumps(key, indent=1))
    print(f"{key['ticket']}: {len(key['fix_files'])} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
