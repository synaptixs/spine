#!/usr/bin/env python3
"""Settle a benchmark run's books from the official grader's report, and print the six buckets.

Run in Spine's own venv, after ``swebench eval`` has graded ``predictions.jsonl``::

    python scripts/swebench_report.py --instances instances.jsonl --workdir run --report <report>.json

It reads ``<workdir>/ledger.jsonl`` (which stays append-only and is not changed), settles every
``submitted`` row from the grader's report, and writes ``<workdir>/results.json`` — the summary and the
settled rows, with no patch text. The denominator is every instance in ``--instances``.

It **refuses** to produce a number from a ledger with a missing instance or a row still ``submitted``
after grading: a bad number is published as bad or the claim is dropped, never rounded into a clean one
by leaving instances out. A submitted instance the report does not mention is a ``grader-error``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from orchestrator.evals.swebench.instances import load_instances
from orchestrator.evals.swebench.ledger import Ledger, finalize
from orchestrator.evals.swebench.outcome import BUCKETS


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--instances", type=Path, required=True, help="the instances.jsonl that was run")
    parser.add_argument(
        "--workdir", type=Path, required=True, help="the run's directory (holds ledger.jsonl)"
    )
    parser.add_argument("--report", type=Path, required=True, help="the grader's report JSON")
    args = parser.parse_args(argv)

    expected = [i.generation.instance_id for i in load_instances(args.instances)]
    rows = Ledger(args.workdir / "ledger.jsonl").rows()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        final, summary = finalize(rows, report, expected=expected)
    except ValueError as exc:
        raise SystemExit(f"cannot summarise: {exc}") from exc

    total = summary["total"]
    print(f"{total} instances")
    for bucket in BUCKETS:
        count = summary["counts"][bucket]
        print(f"  {bucket:16} {count:4}  {100.0 * count / total:5.1f}%")
    print(f"resolved: {summary['resolved_pct']:.1f}%   recorded spend: ${summary['spent_usd']:.2f}")

    out = args.workdir / "results.json"
    out.write_text(
        json.dumps({"summary": summary, "rows": [asdict(r) for r in final]}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
