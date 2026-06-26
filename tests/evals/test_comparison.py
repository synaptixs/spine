"""render_comparison — the single-shot-vs-agentic side-by-side (1b)."""

from __future__ import annotations

from orchestrator.evals import Arm, ArmOutcome, EvalTask, render_comparison, run_eval


def _arm(accepted: bool, cost: float, iters: int) -> Arm:
    async def _run(_task: EvalTask) -> ArmOutcome:
        return ArmOutcome(accepted=accepted, cost_usd=cost, iterations=iters)

    return _run


async def test_comparison_table_has_a_column_per_arm() -> None:
    tasks = [EvalTask(id="a", category="edit")]
    single = await run_eval(tasks, _arm(False, 0.05, 0), arm_name="single-shot", model="gpt-4o")
    agentic = await run_eval(tasks, _arm(True, 0.30, 3), arm_name="agentic", model="gpt-4o")
    md = render_comparison([single, agentic], title="single-shot vs agentic")

    assert "# single-shot vs agentic" in md
    # one column header per arm
    assert "| metric | single-shot | agentic |" in md
    # acceptance row reflects each arm
    assert "| acceptance | 0/1 (0%) | 1/1 (100%) |" in md
    # the agentic arm's higher cost + iterations surface
    assert "$0.30" in md and "mean iterations | 0.0 | 3.0" in md


async def test_comparison_handles_empty() -> None:
    assert "no arms" in render_comparison([], title="x")
