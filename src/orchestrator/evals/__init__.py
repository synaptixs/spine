"""Persona-agnostic eval harness (Bet 1).

Run any persona's tasks through an *arm* and get an honest ``Scorecard`` —
acceptance rate, cost, convergence, intervention rate, and variance across
repeats. The SWE codegen arms and the auditor persona (Bet 4) share it.
"""

from orchestrator.evals.graders import (
    HeldOutResult,
    count_semgrep_findings,
    reused_existing_symbols,
    run_held_out_tests,
    semgrep_findings,
)
from orchestrator.evals.harness import Arm, render_comparison, render_markdown, run_eval
from orchestrator.evals.models import ArmOutcome, EvalTask, Scorecard, TaskResult
from orchestrator.evals.promotion import (
    PromotionDecision,
    capability_source,
    decision_from_ab,
    promoted_capability,
    render_decisions_log,
)
from orchestrator.evals.skill_ab import (
    PROMOTION_MARGIN,
    PROVIDER_MODELS,
    Verdict,
    outcome_from_result,
    promotion_verdict,
    resolve_model,
)

__all__ = [
    "PROMOTION_MARGIN",
    "PROVIDER_MODELS",
    "Arm",
    "ArmOutcome",
    "EvalTask",
    "HeldOutResult",
    "PromotionDecision",
    "Scorecard",
    "TaskResult",
    "Verdict",
    "capability_source",
    "count_semgrep_findings",
    "decision_from_ab",
    "outcome_from_result",
    "promoted_capability",
    "promotion_verdict",
    "render_comparison",
    "render_decisions_log",
    "render_markdown",
    "resolve_model",
    "reused_existing_symbols",
    "run_eval",
    "run_held_out_tests",
    "semgrep_findings",
]
