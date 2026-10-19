"""Score stored intake outputs on the intake-quality contract (SSPN-204; plan: intake-quality, P0).

usage:
  uv run python scripts/intake_quality.py split --runs DIR --seed N --out split.json
  uv run python scripts/intake_quality.py score --runs DIR --split split.json --half dev [--json out.json]

``DIR`` holds one directory per instance with ``issue.md``, ``intake-cache/*.json`` (the extracted
specs) and optionally ``out/case.json`` (the binder's rows, for the names the graph did not hold).
``split`` shuffles the sorted ids with ``random.Random(seed)``: the first half is **dev**, the rest
**sealed**. The sealed half is refused by ``score`` unless ``--unseal`` is given, because the plan reads
it once, at the end (P3), and a half that has been looked at cannot test what was tuned on the other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from orchestrator.intake.quality import IntakeScore, merge, score_intake


def _ids(runs: Path) -> list[str]:
    return sorted(
        d.name
        for d in runs.iterdir()
        if (d / "issue.md").is_file() and any((d / "intake-cache").glob("*.json"))
    )


def _sha(ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()


def cmd_split(args: argparse.Namespace) -> int:
    ids = _ids(Path(args.runs))
    order = list(ids)
    random.Random(args.seed).shuffle(order)  # noqa: S311 — a published, repeatable split; nothing here is secret
    half = len(order) // 2
    dev, sealed = sorted(order[:half]), sorted(order[half:])
    out = {
        "seed": args.seed,
        "rule": "random.Random(seed).shuffle(sorted ids); first len//2 = dev, the rest = sealed",
        "n": len(ids),
        "dev": dev,
        "sealed": sealed,
        "sha256": {"all": _sha(ids), "dev": _sha(dev), "sealed": _sha(sealed)},
    }
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(
        f"{len(ids)} instances -> dev {len(dev)}, sealed {len(sealed)}; "
        f"sha256 dev {out['sha256']['dev'][:12]}…, sealed {out['sha256']['sealed'][:12]}…"
    )
    return 0


def _load(run: Path) -> tuple[str, list[str], list[str], dict[str, list[str]]]:
    source = (run / "issue.md").read_text(encoding="utf-8")
    cache = json.loads(next((run / "intake-cache").glob("*.json")).read_text(encoding="utf-8"))
    spec: dict[str, Any] = (cache.get("specs") or [{}])[0]
    stated = [str(c) for c in spec.get("acceptance_criteria") or []]
    proposed = [str(c) for c in spec.get("proposed_criteria") or []]
    unresolved: dict[str, list[str]] = {}
    case = run / "out" / "case.json"
    if case.is_file():
        for row in json.loads(case.read_text(encoding="utf-8")).get("criteria", {}).get("rows", []):
            if row.get("status") == "unbound":
                unresolved[str(row["text"])] = [str(n) for n in row.get("claims") or []]
    return source, stated, proposed, unresolved


def cmd_score(args: argparse.Namespace) -> int:
    split = json.loads(Path(args.split).read_text())
    if args.half == "sealed" and not args.unseal:
        print(
            "refused: the sealed half is read once, at P3 of the plan; pass --unseal to read it",
            file=sys.stderr,
        )
        return 2
    ids: list[str] = split[args.half]
    runs = Path(args.runs)
    scores: dict[str, IntakeScore] = {}
    for iid in ids:
        source, stated, proposed, unresolved = _load(runs / iid)
        scores[iid] = score_intake(source, stated, proposed, unresolved)
    total = merge(scores.values())
    print(f"{args.half}: {len(ids)} issues (split sha256 {split['sha256'][args.half][:12]}…)")
    print("counts:", {k: v for k, v in total.__dict__.items()})
    for key, value in total.rates().items():
        print(f"  {key:22} {value:6.1%}")
    empty = [i for i, s in scores.items() if s.stated == 0]
    print(f"  issues with no stated criteria: {len(empty)}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "half": args.half,
                    "n": len(ids),
                    "total": total.__dict__,
                    "rates": total.rates(),
                    "per_issue": {i: s.__dict__ for i, s in scores.items()},
                },
                indent=1,
            )
            + "\n"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("split")
    sp.add_argument("--runs", required=True)
    sp.add_argument("--seed", type=int, required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_split)
    sc = sub.add_parser("score")
    sc.add_argument("--runs", required=True)
    sc.add_argument("--split", required=True)
    sc.add_argument("--half", choices=["dev", "sealed"], required=True)
    sc.add_argument("--unseal", action="store_true")
    sc.add_argument("--json")
    sc.set_defaults(fn=cmd_score)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
