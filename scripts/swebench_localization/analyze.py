"""The pre-registered tables for a localization run: ``python analyze.py run.jsonl [--out tables.md]``.

Reads a results file written by ``loc_harness.py --mode run`` and prints, for every method (Spine on the
full issue text = the headline, Spine on the first line only, BM25 text search, chance): the overall hit
rate at k with a 95% bootstrap interval, the per-project and macro rates, the subset whose issue text
does not quote the fixing file, the multi-file line, and paired sign tests. Bootstrap: 10,000 resamples
over instances, seed 17. Run it in the bench environment (standard library only).

Refuses a file that is not exactly 500 scored rows unless ``--allow-partial`` is given, and checks the
file's sha256 against ``--expect-sha`` when one is supplied.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
from pathlib import Path

KS = (1, 3, 5, 10)
VARS = {"B full text (HEADLINE)": "rank_b", "A first line": "rank_a", "BM25 text search": "rank_bm25"}


def hit(r: dict, key: str, k: int) -> bool:
    return r[key] is not None and r[key] <= k


def rate(rs: list[dict], key: str, k: int) -> float:
    return sum(hit(r, key, k) for r in rs) / len(rs) if rs else float("nan")


def chance(rs: list[dict], k: int) -> float:
    return sum(r["chance"][str(k)] for r in rs) / len(rs)


def sign_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def tables(hdr: dict, rows: list[dict]) -> str:
    n = len(rows)
    rng = random.Random(17)  # noqa: S311 - a seeded bootstrap, not a secret
    idx = [[rng.randrange(n) for _ in range(n)] for _ in range(10000)]

    def ci(vals: list[float]) -> tuple[float, float]:
        means = sorted(sum(vals[i] for i in s) / n for s in idx)
        return means[249], means[9749]

    out: list[str] = []
    p = out.append
    p(
        f"# Localization run (Spine {hdr['spine_sha'][:8]}, dataset {hdr['dataset_rev'][:8]}, "
        f"{n} instances, 0 skipped)\n"
    )
    p("## 1. Overall (micro), hit at k = any gold file among the first k distinct files\n")
    p("| | top-1 | top-3 | top-5 | top-10 |\n|---|---|---|---|---|")
    for name, key in VARS.items():
        cells = []
        for k in KS:
            v = [1.0 if hit(r, key, k) else 0.0 for r in rows]
            lo, hi = ci(v)
            cells.append(f"{sum(v) / n:.3f} ({int(sum(v))}/{n}) [{lo:.3f}, {hi:.3f}]")
        p(f"| {name} | " + " | ".join(cells) + " |")
    p("| chance (random distinct .py files) | " + " | ".join(f"{chance(rows, k):.3f}" for k in KS) + " |")
    p("\nIntervals: 95% percentile bootstrap over instances, 10,000 resamples, seed 17.\n")

    p("## 2. Per repo (n shown), variant B and A, BM25, at top-3 / top-10\n")
    p(
        "| repo | n | B top-3 | B top-10 | A top-3 | A top-10 | BM25 top-3 | BM25 top-10 | chance top-10 |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    byrepo: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        byrepo[r["repo"]].append(r)
    for repo, rs in sorted(byrepo.items(), key=lambda kv: -len(kv[1])):
        p(
            f"| {repo} | {len(rs)} | {rate(rs, 'rank_b', 3):.2f} | {rate(rs, 'rank_b', 10):.2f} | "
            f"{rate(rs, 'rank_a', 3):.2f} | {rate(rs, 'rank_a', 10):.2f} | {rate(rs, 'rank_bm25', 3):.2f} | "
            f"{rate(rs, 'rank_bm25', 10):.2f} | {chance(rs, 10):.2f} |"
        )

    p("\n## 3. Macro mean of the per-repo rates (each repo counts once; tiny repos included)\n")
    p("| | top-1 | top-3 | top-5 | top-10 |\n|---|---|---|---|---|")
    for name, key in VARS.items():
        p(
            f"| {name} | "
            + " | ".join(f"{sum(rate(rs, key, k) for rs in byrepo.values()) / len(byrepo):.3f}" for k in KS)
            + " |"
        )
    p(
        "| chance | "
        + " | ".join(f"{sum(chance(rs, k) for rs in byrepo.values()) / len(byrepo):.3f}" for k in KS)
        + " |"
    )

    p("\n## 4. Issue texts that quote a gold file's path or basename literally\n")
    q = [r for r in rows if r["quotes_gold"]]
    nq = [r for r in rows if not r["quotes_gold"]]
    p(f"Quote a gold path/basename: **{len(q)} of {n} ({len(q) / n * 100:.1f}%)**; do not: {len(nq)}.\n")
    p(
        "| subset | n | B top-1 | B top-3 | B top-10 | A top-10 | BM25 top-10 | chance top-10 |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    for name, rs in (("quotes the fix file", q), ("does NOT quote it", nq), ("all", rows)):
        p(
            f"| {name} | {len(rs)} | {rate(rs, 'rank_b', 1):.3f} | {rate(rs, 'rank_b', 3):.3f} | "
            f"{rate(rs, 'rank_b', 10):.3f} | {rate(rs, 'rank_a', 10):.3f} | "
            f"{rate(rs, 'rank_bm25', 10):.3f} | {chance(rs, 10):.3f} |"
        )

    p("\n## 5. Multi-file instances (two or more gold files)\n")
    mf = [r for r in rows if r["n_gold"] >= 2]
    all10 = sum(r["all_gold_in_10_b"] for r in mf)
    p(
        f"n = {len(mf)}. Any gold file in top-10 (B): {rate(mf, 'rank_b', 10):.3f} "
        f"({sum(hit(r, 'rank_b', 10) for r in mf)}/{len(mf)}); **all** gold files within the first 10 (B): "
        f"{all10 / len(mf):.3f} ({all10}/{len(mf)})."
    )

    p("\n## 6. Paired comparisons (descriptive)\n")
    p(
        "| comparison | k | only first hits | only second hits | both | neither | exact two-sided sign p |\n"
        "|---|---|---|---|---|---|---|"
    )
    for (n1, k1), (n2, k2) in ((("B", "rank_b"), ("BM25", "rank_bm25")), (("B", "rank_b"), ("A", "rank_a"))):
        for k in (3, 10):
            b = sum(hit(r, k1, k) and not hit(r, k2, k) for r in rows)
            c = sum(hit(r, k2, k) and not hit(r, k1, k) for r in rows)
            both = sum(hit(r, k1, k) and hit(r, k2, k) for r in rows)
            p(f"| {n1} vs {n2} | {k} | {b} | {c} | {both} | {n - b - c - both} | {sign_p(b, c):.2g} |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("--out", type=Path, help="also write the tables to this file")
    ap.add_argument("--expect-sha", help="refuse a results file whose sha256 differs")
    ap.add_argument("--allow-partial", action="store_true", help="accept fewer than 500 scored rows")
    a = ap.parse_args()
    raw = a.results.read_bytes()
    if a.expect_sha and hashlib.sha256(raw).hexdigest() != a.expect_sha:
        raise SystemExit("the results file is not the expected one (sha256 differs)")
    lines = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    hdr, rows = lines[0], lines[1:]
    if not hdr.get("header") or hdr.get("mode") != "run":
        raise SystemExit("not a --mode run results file")
    if any(r["status"] != "scored" for r in rows):
        raise SystemExit("the file has skipped rows; a rate over fewer than all instances is not published")
    if len(rows) != 500 and not a.allow_partial:
        raise SystemExit(f"{len(rows)} rows, expected 500 (use --allow-partial for a subset)")
    text = tables(hdr, rows)
    print(text)
    if a.out:
        a.out.write_text(text)


if __name__ == "__main__":
    main()
