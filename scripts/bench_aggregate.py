"""Aggregate repeated `codegen_benchmark.py` passes into a result with an interval.

Why this exists: **the benchmark cannot be made deterministic.** `claude-opus-5` accepts
only its own fixed temperature and rejects every other value, so a pinned temperature is
impossible across the model set (see `litellm_client`'s retry path). On 2026-08-15 two
identical passes of the same arm scored 5/10 and 8/10 — a 30% swing on the headline number.

A single pass is therefore not a result, and reporting one as if it were is the mistake this
script exists to prevent. Run each arm N times and report the interval.

Reads the per-arm logs written by the matrix runner (one file per arm-pass) and prints, per
arm: passes, per-pass scores, the pooled proportion, and a **Wilson score interval** — which
is used rather than the normal approximation because it stays sensible at small n and near
0 or 1, exactly the regime a 10-ticket arm lives in.

**Interpretation caveat, stated because it is easy to get wrong:** tickets within a pass are
not independent (a model that misreads the repo tends to miss several), so the pooled
interval is optimistic. Treat it as a lower bound on the true spread, and prefer the observed
per-pass range when the two disagree.

Usage:
    uv run python scripts/bench_aggregate.py <log-dir>
    uv run python scripts/bench_aggregate.py <log-dir> --json
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# `<model>-<arm>-p<pass>.log`, e.g. `claude-opus-5-grounded-p2.log`
_NAME = re.compile(r"^(?P<model>.+?)-(?P<arm>grounded|ungrounded)-p(?P<pass>\d+)\.log$")

_ACCEPT = re.compile(r"overall acceptance:\s*(\d+)/(\d+)")
_HELD = re.compile(r"independent \(held-out\) acceptance:\s*(\d+)/(\d+)")
_ABORT = re.compile(r"aborted \(NOT measured\):\s*(\d+)/(\d+)")
_COST = re.compile(r"total cost:\s*\$([0-9.]+)")
_SERVED = re.compile(r"served model\(s\):\s*(.+?)\s{2,}\[")


def wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Chosen over the normal approximation because that one produces intervals that run past
    0 or 1 — and reports zero width at exactly 0/n or n/n, which is precisely the answer a
    small benchmark must not give.
    """
    if trials == 0:
        return (0.0, 0.0)
    p = successes / trials
    d = 1 + z**2 / trials
    centre = (p + z**2 / (2 * trials)) / d
    half = z * math.sqrt(p * (1 - p) / trials + z**2 / (4 * trials**2)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def parse(path: Path) -> dict[str, Any] | None:
    m = _NAME.match(path.name)
    if not m:
        return None
    text = path.read_text(errors="replace")
    acc = _ACCEPT.search(text)
    if not acc:
        return None
    held = _HELD.search(text)
    abort = _ABORT.search(text)
    cost = _COST.search(text)
    served = _SERVED.search(text)
    return {
        "model": m["model"],
        "arm": m["arm"],
        "pass": int(m["pass"]),
        "accepted": int(acc[1]),
        "tickets": int(acc[2]),
        "held_out": int(held[1]) if held else None,
        "held_out_of": int(held[2]) if held else None,
        # No abort line at all means the log predates that reporting — unknown, not zero.
        "aborted": int(abort[1]) if abort else None,
        "cost": float(cost[1]) if cost else 0.0,
        "served": served[1].strip() if served else "(unrecorded)",
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        return 2

    runs = [r for p in sorted(Path(args[0]).glob("*.log")) if (r := parse(p))]
    if not runs:
        print(f"no parsable arm logs in {args[0]}")
        return 2

    arms: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        arms[(r["model"], r["arm"])].append(r)

    report: list[dict[str, Any]] = []
    for (model, arm), rs in sorted(arms.items()):
        rs.sort(key=lambda r: r["pass"])
        # An arm containing an aborted ticket is not a measurement; say so and exclude it
        # rather than folding "never reached the model" into an acceptance rate.
        bad = [r for r in rs if r["aborted"]]
        good = [r for r in rs if not r["aborted"]]
        unknown = [r for r in rs if r["aborted"] is None]
        acc = sum(r["accepted"] for r in good)
        tot = sum(r["tickets"] for r in good)
        lo, hi = wilson(acc, tot)
        scores = [f"{r['accepted']}/{r['tickets']}" for r in good]
        held_pairs = [(r["held_out"], r["held_out_of"]) for r in good if r["held_out"] is not None]
        report.append(
            {
                "model": model,
                "arm": arm,
                "passes": len(good),
                "excluded_aborted_passes": len(bad),
                "unknown_abort_status": len(unknown),
                "per_pass": scores,
                "accepted": acc,
                "trials": tot,
                "rate": (acc / tot) if tot else 0.0,
                "ci95": [round(lo, 3), round(hi, 3)],
                "observed_range": [
                    min((r["accepted"] for r in good), default=0),
                    max((r["accepted"] for r in good), default=0),
                ],
                "held_out": (
                    f"{sum(a for a, _ in held_pairs)}/{sum(b for _, b in held_pairs)}" if held_pairs else None
                ),
                "cost": round(sum(r["cost"] for r in good), 2),
                "served": sorted({r["served"] for r in good}),
            }
        )

    if as_json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\n=== aggregated over {len(runs)} arm-logs ===\n")
    hdr = (
        f"  {'model':<16} {'arm':<11} {'n':>2} {'per-pass':<18} "
        f"{'rate':>6} {'95% CI':<16} {'held-out':<9} cost"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in report:
        ci = f"[{r['ci95'][0]:.2f}, {r['ci95'][1]:.2f}]"
        print(
            f"  {r['model']:<16} {r['arm']:<11} {r['passes']:>2} {','.join(r['per_pass']):<18} "
            f"{r['rate']:>6.2f} {ci:<16} {str(r['held_out'] or '-'):<9} ${r['cost']:.2f}"
        )
        if r["excluded_aborted_passes"]:
            print(f"      !! {r['excluded_aborted_passes']} pass(es) EXCLUDED — contained aborted tickets")
        if r["unknown_abort_status"]:
            print(f"      ?  {r['unknown_abort_status']} pass(es) predate abort reporting — status unknown")

    thin = [r for r in report if r["passes"] < 5]
    if thin:
        print(
            f"\n  NOTE: {len(thin)} arm(s) have fewer than 5 passes. Determinism is not available\n"
            "  (temperature cannot be pinned on this model set), so a 1-2 pass arm reports a\n"
            "  point estimate the next run may not reproduce. Treat those CIs as indicative."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
