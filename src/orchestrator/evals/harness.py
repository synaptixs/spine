"""The eval runner + scorecard rendering (Bet 1, phase 1a).

``run_eval`` runs each task through an *arm* K times and aggregates into a
``Scorecard``. An arm is any ``async (EvalTask) -> ArmOutcome`` — the SWE
single-shot/agentic arms and the future auditor arm all satisfy it, so the
runner stays persona-agnostic. Arm exceptions become a rejected outcome (a
crash is data, not a harness failure).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.evals.models import ArmOutcome, EvalTask, Scorecard, TaskResult

Arm = Callable[[EvalTask], Awaitable[ArmOutcome]]
ProgressFn = Callable[[str], None]


async def run_eval(
    tasks: list[EvalTask],
    arm: Arm,
    *,
    arm_name: str,
    model: str = "",
    repeats: int = 1,
    on_progress: ProgressFn | None = None,
) -> Scorecard:
    """Run every task ``repeats`` times through ``arm`` → a Scorecard."""
    emit = on_progress or (lambda _m: None)
    results: list[TaskResult] = []
    for task in tasks:
        outcomes: list[ArmOutcome] = []
        for i in range(max(1, repeats)):
            emit(f"{task.id} [{i + 1}/{repeats}] …")
            started = time.perf_counter()
            try:
                outcome = await arm(task)
            except Exception as exc:  # noqa: BLE001 — a crash is a rejected outcome, not a harness failure
                outcome = ArmOutcome(
                    accepted=False,
                    wall_clock_s=round(time.perf_counter() - started, 2),
                    failure_mode="crash",
                    detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
            outcomes.append(outcome)
            emit(f"{task.id} [{i + 1}/{repeats}] → {'ACCEPT' if outcome.accepted else 'REJECT'}")
        results.append(TaskResult(task=task, outcomes=outcomes))
    return Scorecard(arm=arm_name, model=model, results=results)


def render_markdown(scorecard: Scorecard, *, title: str = "Eval scorecard") -> str:
    """A compact, honest scorecard table for `docs/evals/`."""
    m = scorecard.metrics()
    lines = [
        f"# {title}",
        "",
        f"- **arm:** {m['arm']} · **model:** {m['model'] or '(unset)'}",
        f"- **acceptance:** {m['accepted']}/{m['runs']} = **{m['acceptance_rate']:.0%}** "
        f"({m['tasks']} tasks × {m['repeats']} repeats)",
        f"- **by category:** {', '.join(f'{k} {v}' for k, v in m['by_category'].items()) or '—'}",
        f"- **flaky tasks:** {m['flaky_tasks']} · **intervention rate:** {m['intervention_rate']:.0%}",
        f"- **cost:** ${m['total_cost_usd']:.2f} total, ${m['mean_cost_usd']:.4f}/run · "
        f"**mean iterations:** {m['mean_iterations']}",
        f"- **failure modes:** {m['failure_modes'] or '—'}",
        "",
        "| task | category | acceptance | flaky | mean cost | mean iters |",
        "|---|---|---|---|---|---|",
    ]
    for r in scorecard.results:
        lines.append(
            f"| {r.task.id} | {r.task.category} | {r.accepted_count}/{r.repeats} | "
            f"{'yes' if r.flaky else ''} | ${r.mean_cost:.4f} | {r.mean_iterations:.1f} |"
        )
    return "\n".join(lines) + "\n"


def render_comparison(scorecards: list[Scorecard], *, title: str = "Eval comparison") -> str:
    """Side-by-side arms (e.g. single-shot vs agentic) — 1b's headline artifact."""
    if not scorecards:
        return f"# {title}\n\n(no arms)\n"
    metrics = [(c.arm, c.metrics()) for c in scorecards]
    lines = [
        f"# {title}",
        "",
        f"- **model:** {scorecards[0].model or '(unset)'} · "
        f"**tasks:** {metrics[0][1]['tasks']} × {metrics[0][1]['repeats']} repeats",
        "",
        "| metric | " + " | ".join(arm for arm, _ in metrics) + " |",
        "|---|" + "---|" * len(metrics),
    ]
    rows: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        ("acceptance", lambda m: f"{m['accepted']}/{m['runs']} ({m['acceptance_rate']:.0%})"),
        ("flaky tasks", lambda m: str(m["flaky_tasks"])),
        ("intervention rate", lambda m: f"{m['intervention_rate']:.0%}"),
        ("mean iterations", lambda m: str(m["mean_iterations"])),
        ("total cost", lambda m: f"${m['total_cost_usd']:.2f}"),
        ("mean cost/run", lambda m: f"${m['mean_cost_usd']:.4f}"),
    ]
    for label, fmt in rows:
        lines.append(f"| {label} | " + " | ".join(fmt(m) for _, m in metrics) + " |")
    return "\n".join(lines) + "\n"


__all__ = ["Arm", "render_comparison", "render_markdown", "run_eval"]
