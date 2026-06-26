"""Eval harness aggregation + variance + rendering — deterministic, no LLM."""

from __future__ import annotations

from orchestrator.evals import Arm, ArmOutcome, EvalTask, render_markdown, run_eval


def _task(tid: str, category: str = "create") -> EvalTask:
    return EvalTask(id=tid, category=category)


def _scripted_arm(plan: dict[str, list[ArmOutcome]]) -> Arm:
    """Return an arm that pops the next scripted outcome per task id."""
    state = {k: list(v) for k, v in plan.items()}

    async def _arm(task: EvalTask) -> ArmOutcome:
        return state[task.id].pop(0)

    return _arm


async def test_acceptance_rate_and_by_category() -> None:
    arm = _scripted_arm(
        {
            "a": [ArmOutcome(accepted=True, cost_usd=0.10, iterations=1)],
            "b": [ArmOutcome(accepted=False, failure_mode="test")],
            "c": [ArmOutcome(accepted=True, cost_usd=0.20, iterations=2)],
        }
    )
    tasks = [_task("a", "create"), _task("b", "edit"), _task("c", "edit")]
    card = await run_eval(tasks, arm, arm_name="single-shot", model="gpt-4o", repeats=1)
    m = card.metrics()
    assert m["accepted"] == 2 and m["runs"] == 3
    assert m["acceptance_rate"] == round(2 / 3, 3)
    assert m["by_category"] == {"create": "1/1", "edit": "1/2"}
    assert m["failure_modes"] == {"test": 1}
    assert m["total_cost_usd"] == 0.30


async def test_variance_flags_flaky_tasks() -> None:
    # Task "x" accepts 2 of 3 repeats → flaky; "y" accepts 0/3 → not flaky.
    arm = _scripted_arm(
        {
            "x": [
                ArmOutcome(accepted=True),
                ArmOutcome(accepted=False, failure_mode="anchor"),
                ArmOutcome(accepted=True),
            ],
            "y": [ArmOutcome(accepted=False), ArmOutcome(accepted=False), ArmOutcome(accepted=False)],
        }
    )
    card = await run_eval([_task("x"), _task("y")], arm, arm_name="agentic", repeats=3)
    m = card.metrics()
    assert m["flaky_tasks"] == 1
    by_id = {r.task.id: r for r in card.results}
    assert by_id["x"].flaky is True and abs(by_id["x"].acceptance_rate - 2 / 3) < 1e-9
    assert by_id["y"].flaky is False


async def test_intervention_rate_and_mean_iterations() -> None:
    arm = _scripted_arm(
        {
            "a": [ArmOutcome(accepted=True, iterations=0, intervened=False)],
            "b": [ArmOutcome(accepted=True, iterations=4, intervened=True)],
        }
    )
    card = await run_eval([_task("a"), _task("b")], arm, arm_name="x", repeats=1)
    m = card.metrics()
    assert m["intervention_rate"] == 0.5
    assert m["mean_iterations"] == 2.0


async def test_arm_exception_becomes_a_crash_outcome() -> None:
    async def _boom(_task: EvalTask) -> ArmOutcome:
        raise RuntimeError("kaboom")

    card = await run_eval([_task("a")], _boom, arm_name="x", repeats=1)
    m = card.metrics()
    assert m["accepted"] == 0
    assert m["failure_modes"] == {"crash": 1}
    assert "kaboom" in card.results[0].outcomes[0].detail


async def test_render_markdown_has_headline_and_rows() -> None:
    arm = _scripted_arm({"a": [ArmOutcome(accepted=True, cost_usd=0.1)]})
    card = await run_eval([_task("a")], arm, arm_name="single-shot", model="gpt-4o", repeats=1)
    md = render_markdown(card, title="SWE baseline")
    assert "# SWE baseline" in md
    assert "100%" in md  # acceptance headline
    assert "| a | create |" in md  # task row
