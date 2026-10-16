"""Check that every base commit of the 500 instances exists in the local mirrors.

Run in the bench environment: ``python check_commits.py [--mirrors DIR]``. Exits non-zero if any is missing.
"""

from __future__ import annotations

import argparse
import collections
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loc_harness as h  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mirrors", type=Path, default=h.DEFAULT_MIRRORS)
    a = ap.parse_args()
    byrepo: dict[str, set[str]] = collections.defaultdict(set)
    for r in h.load_rows():
        byrepo[r["repo"]].add(r["base_commit"])
    total = missing = 0
    for repo, commits in sorted(byrepo.items()):
        mirror = a.mirrors.expanduser() / (repo.replace("/", "__") + ".git")
        p = subprocess.run(
            ["git", "--git-dir", str(mirror), "cat-file", "--batch-check"],
            input="\n".join(c + "^{commit}" for c in commits) + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        bad = (
            sum(1 for line in p.stdout.splitlines() if "missing" in line)
            if p.returncode == 0
            else len(commits)
        )
        print(f"{repo:28} commits={len(commits):4} missing={bad}")
        total += len(commits)
        missing += bad
    print("TOTAL distinct commits", total, "missing", missing)
    raise SystemExit(1 if missing else 0)


if __name__ == "__main__":
    main()
