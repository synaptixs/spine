#!/usr/bin/env python3
"""Run Spine's pipeline over SWE-bench instances and write the ledger and predictions.

Run in Spine's own venv. It needs ``docker``, a model key in the environment, and the
``instances.jsonl`` that ``scripts/swebench_prep.py`` wrote. Output goes under ``--workdir``, which must
be **outside** the repository checkout (a checkout of a benchmark repo inside Spine's tree would be
walked into Spine's own graph):

* ``ledger.jsonl`` — one row per instance, the latest wins; the six-bucket book;
* ``patches/<id>.diff`` and ``predictions.jsonl`` — what the official grader takes;
* ``<id>/`` — the issue, the pipeline's log and artifacts.

The total spend cap (plan D3) is a **projection over recorded spend**: before an instance starts, the run
stops if what is booked plus ``--max-cost`` would pass ``--max-total``, and lists what was not run. What
is booked is each run's recorded build and review spend; the pipeline's intake and design calls are small
and not under its own cap, so this bounds recorded spend and is not an invoice.

Running the command again continues from the ledger. A ``generator-error`` row is *not* retried unless
``--retry-generator-errors`` is given (retrying only the failures would bias the result upward); a park,
a budget stop or an unresolved run is a result and is never re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from orchestrator.evals.swebench.adapter import CliDocker, Config, subprocess_launcher
from orchestrator.evals.swebench.instances import load_instances
from orchestrator.evals.swebench.ledger import Ledger, predictions
from orchestrator.evals.swebench.sweep import sweep

REPO = Path(__file__).resolve().parent.parent


def _spine_sha() -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--ids", help="run only these instance ids (comma-separated)")
    parser.add_argument("--max-cost", type=float, required=True, help="spend cap per instance, USD")
    parser.add_argument(
        "--max-total",
        type=float,
        required=True,
        help="total cap, USD, as a projection over recorded spend (see above)",
    )
    parser.add_argument("--wall-clock", type=int, default=1800, help="seconds per instance")
    parser.add_argument("--model", default="claude-opus-5", help="recorded in the ledger")
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument(
        "--retry-generator-errors",
        action="store_true",
        help="run again the instances whose last row is a generator-error (after an infrastructure fix)",
    )
    parser.add_argument(
        "--pass-env",
        action="append",
        default=[],
        metavar="NAME",
        help="also pass this environment variable to the pipeline (repeatable; named, never a prefix)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO / ".env",
        help="take the model key from this file (only ANTHROPIC_API_KEY / OPENAI_API_KEY are read)",
    )
    args = parser.parse_args(argv)

    workdir = args.workdir.resolve()
    if REPO in (workdir, *workdir.parents) or workdir in REPO.parents:
        raise SystemExit(f"--workdir must be outside the checkout ({REPO}); got {workdir}")

    instances = load_instances(args.instances)
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        instances = [i for i in instances if i.generation.instance_id in wanted]
        missing = wanted - {i.generation.instance_id for i in instances}
        if missing:
            raise SystemExit(f"not in {args.instances}: {', '.join(sorted(missing))}")

    cfg = Config(
        workdir=workdir,
        max_cost_usd=args.max_cost,
        wall_clock_s=args.wall_clock,
        model=args.model,
        spine_sha=_spine_sha(),
        keep_workspaces=args.keep_workspaces,
        env_file=args.env_file,
        pass_env=tuple(args.pass_env),
    )
    book = Ledger(workdir / "ledger.jsonl")
    result = asyncio.run(
        sweep(
            instances,
            cfg=cfg,
            ledger=book,
            docker=CliDocker(),
            launcher=subprocess_launcher,
            max_total_usd=args.max_total,
            retry_generator_errors=args.retry_generator_errors,
        )
    )

    workdir.mkdir(parents=True, exist_ok=True)  # a run that stopped before its first instance made none
    rows = book.rows()
    preds = predictions(rows, workdir / "patches", model_name=args.model)
    (workdir / "predictions.jsonl").write_text("".join(json.dumps(p) + "\n" for p in preds), encoding="utf-8")
    print(
        f"ran {result.ran}, skipped {result.skipped}, spent ${result.spent_usd:.2f}"
        + (f"  STOPPED at the cap; not run: {', '.join(result.not_run)}" if result.stopped_by_cap else "")
    )
    for row in rows:
        print(f"  {row.instance_id:40} {row.bucket:16} ${row.spent_usd:6.2f}  {row.reason[:70]}")
    print(f"predictions: {len(preds)} -> {workdir / 'predictions.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
