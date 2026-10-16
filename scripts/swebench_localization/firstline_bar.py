"""The pre-registered bar for the first-line ranking (SSPN-192): ``python firstline_bar.py run.jsonl``.

PREREGISTRATION-FIRSTLINE.md fixes it before anything is scored: on the arm being read, V (first-line
ranking) against B (current Spine, the flat query), top-3, paired one-sided exact sign test p < 0.05;
and the net change at top-10 (instances B hits and V does not, minus the reverse) no worse than -2.
The 38-label floor and the 40 further labels are checked separately and are not in this script.

``--ids-file p2-split.json --half sealed`` restricts a Verified results file to a half of the seed-17
split; that arm is NOT blind for this hypothesis and the output says so. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def hit(r: dict, key: str, k: int) -> bool:
    return r[key] is not None and r[key] <= k


def sign_p_one_sided(better: int, worse: int) -> float:
    """P(at least `better` of `better+worse` discordant pairs favour V if favouring either way were 50/50)."""
    n = better + worse
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(better, n + 1)) / 2**n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("--ids-file", type=Path, help="split file with tuning/sealed id lists")
    ap.add_argument("--half", choices=["tuning", "sealed"], help="which half of --ids-file to keep")
    ap.add_argument("--expect-rows", type=int, help="refuse unless exactly this many rows are scored")
    a = ap.parse_args()
    lines = [json.loads(line) for line in a.results.read_text().splitlines() if line.strip()]
    hdr, rows = lines[0], lines[1:]
    if not hdr.get("header") or hdr.get("mode") != "run":
        raise SystemExit("not a --mode run results file")
    if any(r["status"] != "scored" for r in rows):
        raise SystemExit(
            "skipped rows present: report them, do not replace them (pre-registration section 3)"
        )
    if a.half:
        keep = set(json.loads(a.ids_file.read_text())[a.half])
        rows = [r for r in rows if r["instance_id"] in keep]
        print(f"ARM S ({a.half} half of Verified, seed 17): NOT BLIND for this hypothesis; secondary.")
    if a.expect_rows is not None and len(rows) != a.expect_rows:
        raise SystemExit(f"{len(rows)} rows, expected {a.expect_rows}")
    if not all("rank_v" in r for r in rows):
        raise SystemExit("rows have no rank_v: this file was not produced with the first-line harness")
    n = len(rows)
    v3 = sum(1 for r in rows if hit(r, "rank_v", 3) and not hit(r, "rank_b", 3))
    b3 = sum(1 for r in rows if hit(r, "rank_b", 3) and not hit(r, "rank_v", 3))
    p = sign_p_one_sided(v3, b3)
    top10_v = sum(hit(r, "rank_v", 10) for r in rows)
    top10_b = sum(hit(r, "rank_b", 10) for r in rows)
    net10 = top10_b - top10_v  # positive = V lost instances at top-10
    ok_p = p < 0.05
    ok_10 = net10 <= 2
    print(f"n = {n}  Spine {hdr['spine_sha'][:8]}  harness {hdr['harness_sha256'][:8]}")
    hv3 = sum(hit(r, "rank_v", 3) for r in rows)
    hb3 = sum(hit(r, "rank_b", 3) for r in rows)
    print(f"top-3:  V hits {hv3}  B hits {hb3}")
    verdict_p = "PASS" if ok_p else "FAIL"
    verdict_10 = "PASS" if ok_10 else "FAIL"
    print(
        f"        only V {v3}  only B {b3}  one-sided exact sign p = {p:.4g}  -> {verdict_p} (needs < 0.05)"
    )
    print(f"top-10: V hits {top10_v}  B hits {top10_b}  net loss {net10}  -> {verdict_10} (needs <= 2)")
    print("BAR (this arm): " + ("MET" if ok_p and ok_10 else "NOT MET"))
    print("Guards not computed here: the 38-label floor (12/18/22/27) and the 40 further labels.")


if __name__ == "__main__":
    main()
