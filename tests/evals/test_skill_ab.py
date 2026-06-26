"""Pure A/B core for the persona-skill measurement (P2).

Model resolution, result→outcome scoring, and the promotion bar — the decisions
the live runner rests on — verified without any LLM call.
"""

from __future__ import annotations

import pytest

from orchestrator.evals import EvalTask, Scorecard, TaskResult
from orchestrator.evals.skill_ab import (
    PROMOTION_MARGIN,
    PROVIDER_MODELS,
    outcome_from_result,
    promotion_verdict,
    resolve_model,
)


class TestResolveModel:
    def test_provider_defaults(self) -> None:
        assert resolve_model("claude") == PROVIDER_MODELS["claude"]
        assert resolve_model("openai") == "gpt-4o"
        assert resolve_model("local").startswith("ollama/")

    def test_override_wins_over_everything(self) -> None:
        assert resolve_model("claude", override="x", env={"CLAUDE_MODEL": "y"}) == "x"

    def test_per_provider_env_beats_default(self) -> None:
        assert resolve_model("local", env={"LOCAL_MODEL": "ollama/llama3"}) == "ollama/llama3"
        assert resolve_model("openai", env={"OPENAI_MODEL": "gpt-5"}) == "gpt-5"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_model("gemini")


class TestOutcomeFromResult:
    def test_held_out_pass_is_independent_acceptance(self) -> None:
        out = outcome_from_result(
            {"independent_accepted": True, "accepted": False, "refines": 1, "cost_usd": 0.3}
        )
        assert out.accepted is True  # independent verdict, NOT the self-graded one
        assert out.iterations == 1
        assert out.cost_usd == 0.3
        assert out.intervened is False
        assert out.failure_mode is None

    def test_held_out_fail_overrides_self_grade(self) -> None:
        out = outcome_from_result(
            {
                "independent_accepted": False,
                "accepted": True,  # the model's own tests passed …
                "tests_pass": True,
                "held_out_ran": True,
                "held_out_pass": False,  # … but the held-out judge failed it
                "preflight_pass": True,
                "fit": True,
            }
        )
        assert out.accepted is False
        assert out.failure_mode == "heldout"
        assert out.intervened is True

    def test_falls_back_to_self_grade_without_held_out(self) -> None:
        out = outcome_from_result({"independent_accepted": None, "accepted": True})
        assert out.accepted is True

    def test_failure_mode_names_earliest_stage(self) -> None:
        assert outcome_from_result({"accepted": False, "tests_pass": False}).failure_mode == "tests"
        assert (
            outcome_from_result({"accepted": False, "tests_pass": True, "preflight_pass": False}).failure_mode
            == "preflight"
        )
        assert (
            outcome_from_result(
                {"accepted": False, "tests_pass": True, "preflight_pass": True, "fit": False}
            ).failure_mode
            == "fit"
        )

    def test_detail_carries_supporting_signals(self) -> None:
        out = outcome_from_result(
            {"independent_accepted": True, "refines": 2, "semgrep_findings": 0, "reuse_ok": True}
        )
        assert "refines=2" in out.detail
        assert "semgrep=0" in out.detail
        assert "reuse=y" in out.detail


def _scorecard(arm: str, accepts: list[bool]) -> Scorecard:
    """A scorecard of one task per bool, each run once (rate = mean of accepts)."""
    from orchestrator.evals import ArmOutcome

    results = [
        TaskResult(task=EvalTask(id=f"t{i}", category="create"), outcomes=[ArmOutcome(accepted=a)])
        for i, a in enumerate(accepts)
    ]
    return Scorecard(arm=arm, model="m", results=results)


class TestPromotionVerdict:
    def test_clears_bar_promotes(self) -> None:
        baseline = _scorecard("baseline", [False, False, False, False])  # 0%
        treatment = _scorecard("treatment", [True, True, True, False])  # 75%
        v = promotion_verdict("test-strategy", baseline, treatment)
        assert v.promote is True
        assert v.delta == pytest.approx(0.75)
        assert "PROMOTE" in v.summary()

    def test_sub_margin_gain_holds(self) -> None:
        # +5pp is below the +10pp bar → HOLD (don't ship within run-to-run noise).
        baseline = _scorecard("baseline", [True] * 10 + [False] * 10)  # 50%
        treatment = _scorecard("treatment", [True] * 11 + [False] * 9)  # 55%
        v = promotion_verdict("convention-digest", baseline, treatment)
        assert v.delta == pytest.approx(0.05)
        assert v.promote is False
        assert "HOLD" in v.summary()

    def test_margin_is_configurable_and_default_is_ten_points(self) -> None:
        assert PROMOTION_MARGIN == 0.10
        baseline = _scorecard("baseline", [False])  # 0%
        treatment = _scorecard("treatment", [True])  # 100%
        assert promotion_verdict("s", baseline, treatment, margin=1.5).promote is False
