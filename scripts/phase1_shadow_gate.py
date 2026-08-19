"""Phase 1 divergence gate — does the SDLC graph reproduce the pipeline it shadows?

`docs/specs/graphir-sdlc-workflow.md` closes Phase 1 on **zero divergence over >=20 runs across
>=5 commits**. This is that check.

**No model runs, and that is not a shortcut.** Only two nodes are compared — `n_investigate` and
`n_validity` — and both are deterministic. The three model stages (intake, implement, review)
have no bearing on whether the graph reproduces them, so driving full `autorun` runs would spend
real money to exercise code the gate does not measure. What it does drive is the *real*
`_shadow_pass`, `_stage_investigate` and `_stage_validity` out of `autorun`, not a
reimplementation of them: a gate that re-derives the thing it is checking checks itself.

`n_rca` and `n_blast_radius` are deliberately uncompared. RCA does not run in `autorun` at all,
and the blast radius is computed inside `design.py` from the design's own proposal — there is no
imperative twin to disagree with. Reporting "4 nodes verified" would be the summary-line
overstatement this project keeps catching in itself.

Usage — pass one `commit=path` pair per tree. Worktrees are the caller's to make and remove, so
the gate never mutates the repository it is measuring:

    git worktree add --detach /tmp/wt/<sha> <sha>
    python scripts/phase1_shadow_gate.py /tmp/gate-artifacts \\
        "<sha>=/tmp/wt/<sha>" "HEAD=$PWD"

Exit code is 0 only when every run diverged zero times **and** the run/commit floor is met — a
green exit on 3 runs would be the same silent-truncation failure the spec's "bound honestly"
invariant exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore, load_or_extract
from orchestrator.sdlc.autorun import (
    AutorunError,
    RunContext,
    _shadow_pass,
    _stage_investigate,
    _stage_validity,
    _write_shadow_report,
)

MIN_RUNS = 20
MIN_COMMITS = 5

# Four tickets shaped like the ones this pipeline actually receives: two bugs that name symbols,
# two stories that name behaviour. They are the *defects in the spec*, which keeps them honest —
# each one lands on code that exists, so an empty landing set would be a finding, not a fixture.
TICKETS: tuple[tuple[str, str, str, str], ...] = (
    (
        "SPN-1",
        "Bug",
        "blast radius uses the design proposal",
        "impact blast_radius is computed from design files_to_touch instead of landing sites",
    ),
    (
        "SPN-2",
        "Bug",
        "rca never runs in autorun",
        "build_rca localize_trace regression surface is unreachable from the autorun pipeline",
    ),
    (
        "SPN-3",
        "Story",
        "evidence is discarded between stages",
        "landing investigate autorun stage keeps only the filename",
    ),
    (
        "SPN-4",
        "Story",
        "acceptance criteria are not bound to evidence",
        "spec acceptance_criteria codegen grounding design are read straight through",
    ),
)


async def _run_one(
    *, root: Path, store: FactStore, commit: str, ticket: tuple[str, str, str, str], artifacts: Path
) -> dict[str, Any]:
    tid, issue_type, title, summary = ticket
    ctx = RunContext(
        run_id=f"{commit}-{tid}",
        source="file://gate",
        live=False,
        root=root,
        artifacts_dir=artifacts / commit / tid,
        issue_key=tid,
        spec={"title": title, "summary": summary, "issue_type": issue_type, "acceptance_criteria": []},
        approvals_dir=artifacts / "approvals",
    )
    quiet: Any = lambda _s: None  # noqa: E731 — the gate reports; the stages need not
    await _shadow_pass(ctx, store=store, issue_type=issue_type, emit=quiet)
    _stage_investigate(ctx, store=store, emit=quiet)
    verdict = "PROCEED"
    try:
        _stage_validity(ctx, store=store, issue_type=issue_type, emit=quiet)
    except AutorunError:
        # A refused ticket is a *pass* for this gate: the comparison still ran, and a parked run
        # is the path that most needs its Evidence written.
        verdict = ctx.verdict or "PARKED"
    _write_shadow_report(ctx)
    investigate = (ctx.shadow.get("nodes") or {}).get("sdlc.investigate") or {}
    return {
        "commit": commit,
        "ticket": tid,
        "verdict": verdict,
        "landing": len((investigate.get("value") or {}).get("landing") or []),
        "divergences": ctx.shadow.get("divergence_count", -1),
        "evidence_digest": (ctx.shadow.get("evidence_digest") or "")[:12],
    }


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    artifacts = Path(argv[0])
    trees = [(c, Path(p)) for c, p in (arg.split("=", 1) for arg in argv[1:])]

    rows: list[dict[str, Any]] = []
    for commit, root in trees:
        started = time.monotonic()
        store = FactStore(load_or_extract(root))
        print(f"  {commit}: graph in {time.monotonic() - started:.1f}s", file=sys.stderr)
        for ticket in TICKETS:
            rows.append(
                await _run_one(root=root, store=store, commit=commit, ticket=ticket, artifacts=artifacts)
            )

    divergent = [r for r in rows if r["divergences"] != 0]
    commits = len({r["commit"] for r in rows})
    summary = {
        "runs": len(rows),
        "commits": commits,
        "divergent_runs": len(divergent),
        "compared_nodes": ["n_investigate", "n_validity"],
        "uncompared_nodes": ["n_rca", "n_blast_radius"],
        "verdicts": dict(Counter(r["verdict"] for r in rows)),
        "distinct_evidence_digests": len({r["evidence_digest"] for r in rows}),
        "floor_met": len(rows) >= MIN_RUNS and commits >= MIN_COMMITS,
        "rows": rows,
    }
    print(json.dumps(summary, indent=2))

    if divergent:
        print(f"\nFAIL — {len(divergent)} run(s) diverged", file=sys.stderr)
        return 1
    if not summary["floor_met"]:
        print(
            f"\nFAIL — {len(rows)} run(s) across {commits} commit(s); "
            f"the gate needs {MIN_RUNS} across {MIN_COMMITS}",
            file=sys.stderr,
        )
        return 1
    print(f"\nPASS — {len(rows)} runs, {commits} commits, 0 divergences", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
