"""Apply the hybrid KG search bars (pre-registration §5) to one harness results file.

Bars, each a paired one-sided exact sign test at p < 0.05, each gating only its own stage (D22):

* investigate (D1): ``rank_h`` vs ``rank_v`` at top-10, with top-1 and top-3 each losing at most 2 issues net;
* "beats BM25" (claim only): ``rank_h`` vs ``rank_bm25`` at top-10;
* RCA (D12): ``rank_rca1`` vs ``rank_rca0`` at top-3;
* design (D21): a fixing file among ``files_design1`` vs among ``files_design0`` ("no files" is a miss for
both).

The 38-label floor is checked apart (``pkg/scoreboard.json``). ``--sealed`` refuses a results file whose
ids file is
not the sealed pool, and refuses to read the sealed pool a second time: the read is logged, and a log entry
for
the pool's digest stops every later read. Usage::

    python hybrid_bar.py results.jsonl [--sealed --pool-sha256 <digest> --log <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from math import comb
from pathlib import Path

#: The sealed pool's file digest (``~/plans/kg-improvement-fresh-pool.json``), fixed in the pre-registration.
SEALED_POOL_SHA256 = "d83077160ef91907c83da97c6f97fa4327aecbd67f5aac6b4044de9cd10936a4"
_LOG = Path("~/.cache/spine-swebench/hybrid-sealed-reads.log").expanduser()


def sign_p_one_sided(better: int, worse: int) -> float:
    """P(X ≥ better) for X ~ Binomial(better + worse, 1/2): ties carry no information."""
    n = better + worse
    if n == 0:
        return 1.0
    return sum(comb(n, i) for i in range(better, n + 1)) / 2**n


def hit(row: dict, key: str, k: int) -> bool:
    rank = row.get(key)
    return rank is not None and rank <= k


def paired(rows: list[dict], new: str, old: str, k: int) -> dict:
    wins = sum(1 for r in rows if hit(r, new, k) and not hit(r, old, k))
    losses = sum(1 for r in rows if hit(r, old, k) and not hit(r, new, k))
    return {
        "k": k,
        "new_hits": sum(hit(r, new, k) for r in rows),
        "old_hits": sum(hit(r, old, k) for r in rows),
        "wins": wins,
        "losses": losses,
        "net": wins - losses,
        "p": round(sign_p_one_sided(wins, losses), 6),
    }


def verdicts(rows: list[dict]) -> dict:
    inv = paired(rows, "rank_h", "rank_v", 10)
    top1 = paired(rows, "rank_h", "rank_v", 1)
    top3 = paired(rows, "rank_h", "rank_v", 3)
    inv_met = inv["p"] < 0.05 and top1["net"] >= -2 and top3["net"] >= -2
    bm25 = paired(rows, "rank_h", "rank_bm25", 10)
    rca = paired(rows, "rank_rca1", "rank_rca0", 3)
    design = paired(rows, "rank_design1", "rank_design0", 10**6)  # any proposed file = membership
    return {
        "n": len(rows),
        "investigate": {"top10": inv, "top1_guard": top1, "top3_guard": top3, "met": inv_met},
        "beats_bm25": {"top10": bm25, "met": bm25["p"] < 0.05},
        "rca": {"top3": rca, "met": rca["p"] < 0.05},
        "design": {"membership": design, "met": design["p"] < 0.05},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path)
    ap.add_argument("--sealed", action="store_true", help="this is the one sealed read: check and log it")
    ap.add_argument("--pool-sha256", default=SEALED_POOL_SHA256)
    ap.add_argument("--log", type=Path, default=_LOG)
    a = ap.parse_args()
    lines = [json.loads(line) for line in a.results.read_text().splitlines() if line.strip()]
    header = next((r for r in lines if r.get("header")), {})
    rows = [r for r in lines if r.get("status") == "scored"]
    needed = ("rank_h", "rank_v", "rank_bm25", "rank_rca0", "rank_rca1", "rank_design0", "rank_design1")
    if not rows or not all(all(k in r for k in needed) for r in rows):
        raise SystemExit(f"refused: every scored row needs {', '.join(needed)}")
    if a.sealed:
        if header.get("ids_file_sha256") != a.pool_sha256:
            raise SystemExit(
                f"refused: ids file {header.get('ids_file_sha256')} is not the sealed pool {a.pool_sha256}"
            )
        if a.log.is_file() and a.pool_sha256 in a.log.read_text():
            raise SystemExit(
                f"refused: the sealed pool was already read (see {a.log}); a second read needs a new pool"
            )
        a.log.parent.mkdir(parents=True, exist_ok=True)
        with a.log.open("a") as fh:
            fh.write(
                json.dumps(
                    {"pool": a.pool_sha256, "results": str(a.results), "cell": header.get("hybrid_cell")}
                )
            )
            fh.write("\n")
    json.dump(verdicts(rows), sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
