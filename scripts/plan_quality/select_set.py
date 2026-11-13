"""Draw a graded ticket set by a written rule, so the set is not chosen by whoever wants a result.

    python scripts/plan_quality/select_set.py <repo> <candidates.json> <out.json>
        [--ref origin/develop] [--exclude K1,K2] [--seed 20261108] [--n 12] [--dev 8]

``candidates.json`` is a list of ``{"key", "summary"}`` (closed bugs from a JQL query). A candidate
qualifies when at least one non-merge commit on ``--ref`` names its key (``SSPN-n``) or its ledger
number (``[Bn]`` in the summary) **and no other key or ledger number**, and those commits change at
least one ``src/**/*.py`` file. Release cuts, multi-bug fix-forwards and later mentions all name other
tickets, and their files cannot be credited to one bug, so they never enter an answer key (rule R1,
added 2026-11-08 after the first draw put all three into keys).

Base = the parent of the earliest qualifying commit. When more than ``--n`` qualify, ``--n`` are drawn
with ``random.Random(seed)``, and the same generator orders them; the first ``--dev`` are the dev set and
the rest are held out (graded once, at the end).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_KEY = re.compile(r"\bSSPN-\d+\b")
_LEDGER = re.compile(r"\bB\d{2,3}\b")


def _git(repo: Path | str, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _commits(repo: Path | str, ref: str) -> list[tuple[str, int, str]]:
    log = _git(repo, "log", ref, "--no-merges", "--format=%H%x09%ct%x09%s%x09%b%x1e")
    out: list[tuple[str, int, str]] = []
    for rec in log.split("\x1e"):
        rec = rec.strip()
        if rec:
            sha, stamp, subject, *body = rec.split("\t")
            out.append((sha, int(stamp), subject + " " + " ".join(body)))
    return out


def _src_files(repo: Path | str, sha: str) -> set[str]:
    names = _git(repo, "show", "--name-only", "--format=", sha).split()
    return {f for f in names if f.startswith("src/") and f.endswith(".py")}


def qualify(
    repo: Path | str, cand: dict[str, Any], commits: list[tuple[str, int, str]]
) -> tuple[dict[str, Any] | None, str]:
    key = cand["key"]
    ledger = re.search(r"\[(B\d+)\]", cand.get("summary", ""))
    mine = {key} | ({ledger.group(1)} if ledger else set())
    patterns = [re.compile(rf"\b{re.escape(m)}\b") for m in mine]

    def single(msg: str) -> bool:
        return not ((set(_KEY.findall(msg)) | set(_LEDGER.findall(msg))) - mine)

    hits = [c for c in commits if any(p.search(c[2]) for p in patterns) and single(c[2])]
    if not hits:
        return None, "no single-bug commit names it"
    files: set[str] = set()
    with_src: list[tuple[str, int, str]] = []
    for c in hits:
        changed = _src_files(repo, c[0])
        if changed:
            with_src.append(c)
            files |= changed
    if not files:
        return None, "no single-bug commit changes src/*.py"
    with_src.sort(key=lambda c: c[1])
    base = _git(repo, "rev-parse", "--short", f"{with_src[0][0]}^").strip()
    return (
        {
            "key": key,
            "summary": cand.get("summary", ""),
            "bnum": ledger.group(1) if ledger else None,
            "base": base,
            "commits": [c[0][:8] for c in with_src],
            "fix_files": sorted(files),
        },
        f"qualifies: {len(with_src)} commit(s), {len(files)} src file(s)",
    )


def select(
    repo: Path | str,
    candidates: list[dict[str, Any]],
    *,
    ref: str,
    exclude: set[str],
    seed: int,
    n: int,
    dev: int,
) -> dict[str, Any]:
    commits = _commits(repo, ref)
    qualified: list[dict[str, Any]] = []
    report: list[tuple[str, str]] = []
    for cand in candidates:
        if cand["key"] in exclude:
            report.append((cand["key"], "excluded"))
            continue
        row, why = qualify(repo, cand, commits)
        report.append((cand["key"], why))
        if row:
            qualified.append(row)
    rng = random.Random(seed)  # noqa: S311 — a reproducible draw, not a secret
    picked = rng.sample(qualified, n) if len(qualified) > n else list(qualified)
    rng.shuffle(picked)
    for i, c in enumerate(picked):
        c["split"] = "dev" if i < dev else "held-out"
    return {
        "rule": __doc__,
        "seed": seed,
        "qualified": len(qualified),
        "candidates": len(report),
        "picked": picked,
        "report": report,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("candidates")
    ap.add_argument("out")
    ap.add_argument("--ref", default="origin/develop")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--dev", type=int, default=8)
    a = ap.parse_args(argv[1:])
    exclude = {k for k in a.exclude.split(",") if k}
    res = select(
        a.repo,
        json.loads(Path(a.candidates).read_text()),
        ref=a.ref,
        exclude=exclude,
        seed=a.seed,
        n=a.n,
        dev=a.dev,
    )
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(f"{res['candidates']} candidates, {res['qualified']} qualify, {len(res['picked'])} picked")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
