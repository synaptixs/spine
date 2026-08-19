"""Phase 2a verdict-parity gate — does the graph-driven run reach the same answer?

`docs/specs/graphir-sdlc-workflow.md` closes Phase 2a on **verdict parity over >=20 runs across
>=5 commits**, plus every acceptance criterion bound or explicitly reported unbound.

This supersedes `phase1_shadow_gate.py`, which compared a shadow execution against the
imperative stages. Those nodes now execute for real, so there is no shadow left to compare —
what replaces it is stronger: run the pipeline **both ways** and compare the verdict a human
would act on.

**No model runs.** Everything 2a changes is deterministic, which is the whole reason the phase
was split from 2b: research, binding and the validity gate can be checked by running both paths,
where promoting `design` to a model node cannot.

**Parity alone is not the bar.** A binding rule that parked every ticket would agree with
itself perfectly and be useless, so the gate also reports the **parking-rate delta**: which
tickets park under binding that did not before, and the criterion that could not be bound in
each. That number is the one to read.

Usage — one `commit=path` pair per tree; worktrees are the caller's to make and remove:

    git worktree add --detach /tmp/wt/<sha> <sha>
    python scripts/phase2a_parity_gate.py /tmp/gate-artifacts "<sha>=/tmp/wt/<sha>" "HEAD=$PWD"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore, load_or_extract
from orchestrator.sdlc.autorun import (
    AutorunError,
    RunContext,
    _research_pass,
    _stage_investigate,
    _stage_validity,
    _write_case,
)

MIN_RUNS = 20
MIN_COMMITS = 5

# The four defects in the spec, as tickets. Each names code that exists, so an empty landing set
# is a finding rather than a fixture artefact. Criteria are deliberately mixed: some bind, some
# are prose, and one names a symbol no repository has.
TICKETS: tuple[dict[str, Any], ...] = (
    {
        "id": "SPN-1",
        "issue_type": "Bug",
        "title": "blast radius uses the design proposal",
        "summary": "impact blast_radius is computed from design files_to_touch instead of landing sites",
        "acceptance_criteria": [
            "`blast_radius` is called with the landing sites",
            "the report should stay readable",
        ],
    },
    {
        "id": "SPN-2",
        "issue_type": "Bug",
        "title": "rca never runs in autorun",
        "summary": "build_rca localize_trace regression surface is unreachable from the autorun pipeline",
        "acceptance_criteria": ["`build_rca` runs on every ticket"],
    },
    {
        "id": "SPN-3",
        "issue_type": "Story",
        "title": "evidence is discarded between stages",
        "summary": "landing investigate autorun stage keeps only the filename",
        "acceptance_criteria": ["`build_investigation` output survives the stage boundary"],
    },
    {
        "id": "SPN-4",
        "issue_type": "Story",
        "title": "acceptance criteria are not bound to evidence",
        "summary": "spec acceptance_criteria codegen grounding design are read straight through",
        "acceptance_criteria": ["`NonexistentBinderWidget` is removed"],
    },
)


async def _run(
    *, root: Path, store: FactStore, commit: str, ticket: dict[str, Any], artifacts: Path, imperative: bool
) -> dict[str, Any]:
    mode = "imperative" if imperative else "graph"
    ctx = RunContext(
        run_id=f"{commit}-{ticket['id']}-{mode}",
        source="file://gate",
        live=False,
        root=root,
        artifacts_dir=artifacts / commit / f"{ticket['id']}-{mode}",
        issue_key=ticket["id"],
        spec={
            "title": ticket["title"],
            "summary": ticket["summary"],
            "issue_type": ticket["issue_type"],
            "acceptance_criteria": list(ticket["acceptance_criteria"]),
        },
        approvals_dir=artifacts / "approvals",
    )
    quiet: Any = lambda _s: None  # noqa: E731
    await _research_pass(ctx, store=store, issue_type=ticket["issue_type"], emit=quiet)
    _stage_investigate(ctx, store=store, emit=quiet)
    verdict = "PROCEED"
    try:
        _stage_validity(ctx, store=store, issue_type=ticket["issue_type"], emit=quiet)
    except AutorunError:
        verdict = ctx.verdict or "PARKED"
    _write_case(ctx)
    binding = ctx.criteria
    return {
        "commit": commit,
        "ticket": ticket["id"],
        "mode": mode,
        "verdict": verdict,
        "parked": verdict != "PROCEED",
        "criteria": len(binding.rows) if binding is not None else 0,
        "bound": len(binding.bound) if binding is not None else 0,
        "unbound": [r.text for r in (binding.unbound if binding is not None else ())],
        "unreported": 0
        if binding is None
        else sum(1 for r in binding.rows if r.status not in {"bound", "unbound", "no-claim"}),
        "case_digest": ctx.case.digest()[:12] if ctx.case is not None else "",
    }


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    artifacts = Path(argv[0])
    trees = [(c, Path(p)) for c, p in (arg.split("=", 1) for arg in argv[1:])]

    graph_rows: list[dict[str, Any]] = []
    imperative_rows: list[dict[str, Any]] = []
    for commit, root in trees:
        started = time.monotonic()
        store = FactStore(load_or_extract(root))
        print(f"  {commit}: graph in {time.monotonic() - started:.1f}s", file=sys.stderr)
        for ticket in TICKETS:
            os.environ.pop("SPINE_SDLC_IMPERATIVE", None)
            graph_rows.append(
                await _run(
                    root=root,
                    store=store,
                    commit=commit,
                    ticket=ticket,
                    artifacts=artifacts,
                    imperative=False,
                )
            )
            os.environ["SPINE_SDLC_IMPERATIVE"] = "1"
            try:
                imperative_rows.append(
                    await _run(
                        root=root,
                        store=store,
                        commit=commit,
                        ticket=ticket,
                        artifacts=artifacts,
                        imperative=True,
                    )
                )
            finally:
                os.environ.pop("SPINE_SDLC_IMPERATIVE", None)

    pairs = list(zip(graph_rows, imperative_rows, strict=True))
    # Parity is judged on the verdicts the two paths reach for reasons *other* than binding.
    # Binding is new, so a ticket that parks only because a criterion is unbound is not a
    # disagreement — it is the feature. Those are counted separately and reported in full.
    binding_parks = [g for g, i in pairs if g["verdict"] != i["verdict"] and g["unbound"] and not i["parked"]]
    mismatches = [
        {"commit": g["commit"], "ticket": g["ticket"], "graph": g["verdict"], "imperative": i["verdict"]}
        for g, i in pairs
        if g["verdict"] != i["verdict"] and g not in binding_parks
    ]
    unreported = sum(r["unreported"] for r in graph_rows)
    commits = len({r["commit"] for r in graph_rows})

    summary = {
        "runs": len(graph_rows),
        "commits": commits,
        "verdict_mismatches": len(mismatches),
        "mismatches": mismatches,
        "parking_rate_delta": {
            "graph_parks": sum(1 for r in graph_rows if r["parked"]),
            "imperative_parks": sum(1 for r in imperative_rows if r["parked"]),
            "parked_only_by_binding": [
                {"commit": r["commit"], "ticket": r["ticket"], "unbound": r["unbound"]} for r in binding_parks
            ],
        },
        "criteria_unreported": unreported,
        "verdicts": dict(Counter(r["verdict"] for r in graph_rows)),
        "distinct_case_digests": len({r["case_digest"] for r in graph_rows}),
        "floor_met": len(graph_rows) >= MIN_RUNS and commits >= MIN_COMMITS,
    }
    print(json.dumps({**summary, "rows": graph_rows}, indent=2))

    if mismatches:
        print(f"\nFAIL — {len(mismatches)} verdict mismatch(es) unrelated to binding", file=sys.stderr)
        return 1
    if unreported:
        print(f"\nFAIL — {unreported} criterion/criteria neither bound nor reported", file=sys.stderr)
        return 1
    if not summary["floor_met"]:
        print(
            f"\nFAIL — {len(graph_rows)} run(s) across {commits} commit(s); "
            f"the gate needs {MIN_RUNS} across {MIN_COMMITS}",
            file=sys.stderr,
        )
        return 1
    print(
        f"\nPASS — {len(graph_rows)} runs, {commits} commits, 0 unexplained mismatches, "
        f"{len(binding_parks)} park(s) newly caused by criterion binding",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
