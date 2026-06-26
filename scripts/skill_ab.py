"""Persona-skill measurement P2 — run the baseline-vs-treatment A/B.

For each candidate skill, run its signal-bearing task set (P1) twice on the same
tickets and seeds: a **baseline** arm (no skill) and a **treatment** arm (the
skill conditioning its declared phase). The headline metric is independent
(held-out) acceptance; the pre-registered bar (+10pp, ``evals.skill_ab``) decides
whether the skill earns promotion into the capability catalog.

Model is chosen by ``--provider``:
  * ``claude``  → claude-sonnet-4-6 (Anthropic; the codegen default)
  * ``openai``  → gpt-4o (OpenAI)
  * ``local``   → ollama/openllama (a local Ollama server; set OLLAMA_API_BASE,
                  default http://localhost:11434)
Override the exact model with ``--model`` or the per-provider env var
(CLAUDE_MODEL / OPENAI_MODEL / LOCAL_MODEL). Provider creds come from the usual
env (ANTHROPIC_API_KEY / OPENAI_API_KEY); local needs no key.

This SPENDS on the commercial providers, so it dry-runs by default: it prints the
plan (arms × tickets × repeats = call budget) and exits. Pass ``--live`` to
actually run. Results — per-arm scorecards, the side-by-side comparison, and the
verdict — are written under ``docs/evals/`` (git-tracked).

Usage:
    uv run python scripts/skill_ab.py --provider local --skill test-strategy --live
    uv run python scripts/skill_ab.py --provider claude --repeats 3 --live        # all skills
    uv run python scripts/skill_ab.py --provider openai --skill convention-digest # dry-run plan
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO / "src"), str(REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import codegen_benchmark as bench  # noqa: E402

from orchestrator.core.env import load_local_env  # noqa: E402
from orchestrator.core.llm import LiteLLMClient, RecordingLLMClient  # noqa: E402
from orchestrator.evals import (  # noqa: E402
    EvalTask,
    Scorecard,
    outcome_from_result,
    promotion_verdict,
    render_comparison,
    render_markdown,
    resolve_model,
)
from orchestrator.evals.skill_ab import PROMOTION_MARGIN, PROVIDER_MODELS  # noqa: E402
from orchestrator.sdlc.grounding import PKGCodegenGrounder  # noqa: E402

SKILLS = tuple(bench.SKILL_TASKSETS)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Persona-skill A/B (baseline vs treatment).")
    p.add_argument("--skill", choices=(*SKILLS, "all"), default="all")
    p.add_argument("--provider", choices=tuple(PROVIDER_MODELS), default="claude")
    p.add_argument("--model", default=None, help="exact model id, overrides --provider default")
    p.add_argument("--repeats", type=int, default=3, help="repeats per ticket per arm (variance)")
    p.add_argument("--tickets", default="", help="comma-separated ticket-key subset")
    p.add_argument("--margin", type=float, default=PROMOTION_MARGIN, help="promotion bar (default 0.10)")
    p.add_argument("--out", default=str(REPO / "docs" / "evals"), help="output dir for scorecards")
    p.add_argument("--live", action="store_true", help="actually call the LLM (default: dry-run plan)")
    p.add_argument("--agentic", action="store_true", help="run the agentic codegen loop arm")
    return p.parse_args(argv)


def _tickets_for(skill: str, subset: set[str]) -> list[bench.Ticket]:
    tickets = bench.taskset(skill)
    return [t for t in tickets if t.key in subset] if subset else tickets


def _plan(skills: list[str], subset: set[str], repeats: int, model: str, provider: str) -> int:
    """Print the dry-run plan and return the total LLM-driven ticket runs."""
    print(f"plan · provider={provider} · model={model} · repeats={repeats}")
    total = 0
    for skill in skills:
        tickets = _tickets_for(skill, subset)
        runs = len(tickets) * repeats * 2  # baseline + treatment
        total += runs
        print(f"  {skill:<22} {len(tickets)} tickets × {repeats} repeats × 2 arms = {runs} ticket-runs")
    print(f"  TOTAL: {total} ticket-runs (each = implement + tests + up to refine cycles)")
    return total


async def _run_arm(
    tickets: list[bench.Ticket],
    llm: RecordingLLMClient,
    grounder: PKGCodegenGrounder,
    *,
    arm_name: str,
    eval_skill: str | None,
    model: str,
    repeats: int,
    agentic: bool,
) -> Scorecard:
    """Run one arm (baseline or treatment) over the task set → a Scorecard."""
    from orchestrator.evals import run_eval

    by_key = {t.key: t for t in tickets}
    tasks = [EvalTask(id=t.key, category=t.kind, payload={}) for t in tickets]

    async def arm(task: EvalTask) -> Any:
        ticket = by_key[task.id]
        result = await bench.run_ticket(
            ticket, llm, grounder, agentic=agentic, model=model, eval_skill=eval_skill
        )
        return outcome_from_result(result)

    return await run_eval(
        tasks,
        arm,
        arm_name=arm_name,
        model=model,
        repeats=repeats,
        on_progress=lambda m: print(f"    [{arm_name}] {m}"),
    )


def _write_report(
    out_dir: Path,
    *,
    skill: str,
    provider: str,
    model: str,
    baseline: Scorecard,
    treatment: Scorecard,
    margin: float,
    stamp: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    verdict = promotion_verdict(skill, baseline, treatment, margin=margin)
    body = [
        f"# Skill A/B — {skill} ({provider})",
        "",
        f"_Run {stamp} · model `{model}` · independent (held-out) acceptance is the headline._",
        "",
        f"**Verdict:** {verdict.summary()}",
        "",
        render_comparison([baseline, treatment], title="Baseline vs treatment"),
        "",
        render_markdown(baseline, title="Baseline scorecard"),
        "",
        render_markdown(treatment, title="Treatment scorecard"),
    ]
    md_path = out_dir / f"{stamp}-skill-ab-{skill}-{provider}.md"
    md_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    json_path = out_dir / f"{stamp}-skill-ab-{skill}-{provider}.json"
    json_path.write_text(
        json.dumps(
            {
                "skill": skill,
                "provider": provider,
                "model": model,
                "verdict": {
                    "promote": verdict.promote,
                    "baseline_rate": verdict.baseline_rate,
                    "treatment_rate": verdict.treatment_rate,
                    "delta": verdict.delta,
                    "margin": verdict.margin,
                },
                "baseline": baseline.to_dict(),
                "treatment": treatment.to_dict(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    load_local_env(str(REPO / ".env"))
    model = resolve_model(args.provider, override=args.model, env=dict(os.environ))
    skills = list(SKILLS) if args.skill == "all" else [args.skill]
    subset = {k.strip() for k in args.tickets.split(",") if k.strip()}

    _plan(skills, subset, args.repeats, model, args.provider)
    if not args.live:
        print("\n(dry-run — pass --live to actually run and spend)")
        return
    if args.provider == "local" and not os.getenv("OLLAMA_API_BASE"):
        print("note: OLLAMA_API_BASE unset — litellm will default to http://localhost:11434")

    stamp = datetime.date.today().isoformat()
    out_dir = Path(args.out)
    llm = RecordingLLMClient(LiteLLMClient(request_timeout_seconds=300.0))
    print("\nbuilding PKG …")
    grounder = PKGCodegenGrounder.from_repo(REPO)

    verdicts = []
    for skill in skills:
        tickets = _tickets_for(skill, subset)
        if not tickets:
            print(f"skip {skill}: no tickets")
            continue
        print(f"\n=== {skill}: baseline arm ===")
        baseline = await _run_arm(
            tickets,
            llm,
            grounder,
            arm_name="baseline",
            eval_skill=None,
            model=model,
            repeats=args.repeats,
            agentic=args.agentic,
        )
        print(f"\n=== {skill}: treatment arm (skill on) ===")
        treatment = await _run_arm(
            tickets,
            llm,
            grounder,
            arm_name="treatment",
            eval_skill=skill,
            model=model,
            repeats=args.repeats,
            agentic=args.agentic,
        )
        md = _write_report(
            out_dir,
            skill=skill,
            provider=args.provider,
            model=model,
            baseline=baseline,
            treatment=treatment,
            margin=args.margin,
            stamp=stamp,
        )
        verdict = promotion_verdict(skill, baseline, treatment, margin=args.margin)
        verdicts.append(verdict)
        print(f"\n  → {verdict.summary()}")
        print(f"  wrote {md.relative_to(REPO)}")

    print("\n=== verdicts ===")
    for v in verdicts:
        print(f"  {v.summary()}")
    total = llm.ledger.total()
    print(f"\n  total cost: ${total.cost_usd:.2f} · {total.calls} calls · {total.total_tokens} tokens")


if __name__ == "__main__":
    asyncio.run(main())
