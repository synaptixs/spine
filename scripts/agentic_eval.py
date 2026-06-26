"""Agentic eval (Bet 1) — persona-agnostic harness over codegen tickets.

Phase 1b: **single-shot vs agentic** arms, side by side, plus an optional
**brownfield** dataset (your own repo + tickets, real ground truth). Scored by
``orchestrator.evals``; reuses the ticket set + grading + worktree machinery from
``codegen_benchmark.py``. One-shot, on demand — writes dated scorecards (and a
comparison) to ``docs/evals/``. No repo mutation (all writes go to /tmp).

Usage:
    EVAL_ARM=both uv run python scripts/agentic_eval.py        # single-shot + agentic (2x cost)
    EVAL_ARM=agentic uv run python scripts/agentic_eval.py     # the loop only
    EVAL_REPEATS=3 EVAL_ARM=agentic uv run python scripts/agentic_eval.py
    EVAL_BROWNFIELD_CONFIG=mytickets.json uv run python scripts/agentic_eval.py
      # brownfield config: {"repo": "/abs/path/to/checkout", "tickets":
      #   [{"key": "...", "kind": "edit|create", "must_edit": ["..."], "spec": {...}}]}
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import codegen_benchmark as bench  # noqa: E402  (sibling script, on sys.path)

from orchestrator.core.env import load_local_env  # noqa: E402
from orchestrator.core.llm import LiteLLMClient, RecordingLLMClient  # noqa: E402
from orchestrator.evals import (  # noqa: E402
    ArmOutcome,
    EvalTask,
    Scorecard,
    render_comparison,
    render_markdown,
    run_eval,
)
from orchestrator.sdlc.grounding import PKGCodegenGrounder  # noqa: E402


def load_brownfield(doc: dict) -> tuple[Path, list[EvalTask]]:
    """Parse a brownfield config → (repo_root, tasks). Pure; no clone/network."""
    repo_root = Path(doc["repo"]).expanduser().resolve()
    tasks = [
        EvalTask(
            id=str(t["key"]),
            category=str(t.get("kind", "create")),
            payload={
                "ticket": bench.Ticket(
                    key=str(t["key"]),
                    kind=str(t.get("kind", "create")),
                    spec=dict(t["spec"]),
                    must_edit=list(t.get("must_edit", [])),
                )
            },
        )
        for t in doc.get("tickets", [])
    ]
    return repo_root, tasks


def _on_repo_tasks() -> list[EvalTask]:
    selected = os.getenv("BENCH_TICKETS")
    keys = {k.strip() for k in selected.split(",")} if selected else None
    return [
        EvalTask(id=t.key, category=t.kind, payload={"ticket": t})
        for t in bench.TICKETS
        if keys is None or t.key in keys
    ]


def _make_arm(llm: RecordingLLMClient, grounder: PKGCodegenGrounder, *, agentic: bool, repo_root: Path):
    async def arm(task: EvalTask) -> ArmOutcome:
        started = time.perf_counter()
        r = await bench.run_ticket(
            task.payload["ticket"], llm, grounder, agentic=agentic, repo_root=repo_root
        )
        failure_mode = None
        if not r["accepted"]:
            failure_mode = "test" if not r["tests_pass"] else ("fit" if not r["fit"] else "preflight")
        return ArmOutcome(
            accepted=bool(r["accepted"]),
            cost_usd=float(r["cost_usd"]),
            wall_clock_s=round(time.perf_counter() - started, 2),
            iterations=int(r["refines"]),
            intervened=not r["accepted"],  # a rejected artifact is one a human gate must redo
            failure_mode=failure_mode,
        )

    return arm


async def main() -> None:
    load_local_env(str(REPO / ".env"))
    repeats = int(os.getenv("EVAL_REPEATS", "1"))
    which = os.getenv("EVAL_ARM", "both").strip().lower()
    arms = ["single-shot", "agentic"] if which == "both" else [which]

    config = os.getenv("EVAL_BROWNFIELD_CONFIG")
    if config:
        repo_root, tasks = load_brownfield(json.loads(Path(config).read_text(encoding="utf-8")))
        label = f"brownfield ({repo_root.name})"
    else:
        repo_root, tasks = REPO, _on_repo_tasks()
        label = "on-repo"
    print(f"dataset: {label} · arms: {arms} · model: {bench.MODEL} · repeats: {repeats}")
    print("building PKG …")
    grounder = PKGCodegenGrounder.from_repo(repo_root)

    llm = RecordingLLMClient(LiteLLMClient(request_timeout_seconds=300.0))
    cards: list[Scorecard] = []
    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    out_dir = REPO / "docs" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "brownfield" if config else "on-repo"

    for arm_name in arms:
        card = await run_eval(
            tasks,
            _make_arm(llm, grounder, agentic=(arm_name == "agentic"), repo_root=repo_root),
            arm_name=arm_name,
            model=bench.MODEL,
            repeats=repeats,
            on_progress=lambda m: print(f"  {m}"),
        )
        cards.append(card)
        md = render_markdown(card, title=f"{arm_name} ({label}) — {stamp}")
        base = out_dir / f"{stamp}-{slug}-{arm_name}"
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        base.with_suffix(".json").write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
        print("\n" + md)

    if len(cards) > 1:
        comp = render_comparison(cards, title=f"single-shot vs agentic ({label}) — {stamp}")
        (out_dir / f"{stamp}-{slug}-comparison.md").write_text(comp, encoding="utf-8")
        print("\n" + comp)
    print(f"scorecards → docs/evals/{stamp}-{slug}-*.{{md,json}}")


if __name__ == "__main__":
    asyncio.run(main())
