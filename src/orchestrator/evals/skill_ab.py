"""A/B plumbing for the persona-skill measurement (P2).

The pure, testable core of the skill A/B: which model a ``--provider`` resolves
to, how a benchmark result dict becomes a scored ``ArmOutcome`` (so the harness's
``Scorecard``/``render_comparison`` can do the rest), and the **pre-registered
promotion bar** the treatment arm must clear to ship a skill into ``_SEED``.

Kept free of any LLM / filesystem / subprocess so the decision logic is unit-
tested without spend; the orchestration (worktrees, real calls, writing
``docs/evals/``) lives in ``scripts/skill_ab.py`` and calls into here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.evals.models import ArmOutcome, Scorecard

# Provider → default codegen model. Local is served through Ollama (litellm
# routes ``ollama/<name>`` and reads OLLAMA_API_BASE), commercial arms hit the
# Anthropic / OpenAI APIs by model name. Override any of these with --model or
# the per-provider env var (CLAUDE_MODEL / OPENAI_MODEL / LOCAL_MODEL).
PROVIDER_MODELS: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "local": "ollama/openllama",
}
_PROVIDER_ENV: dict[str, str] = {
    "claude": "CLAUDE_MODEL",
    "openai": "OPENAI_MODEL",
    "local": "LOCAL_MODEL",
}

# The pre-registered bar (spec §4): a skill promotes only on a meaningful margin
# of held-out (independent) acceptance over baseline, not within run-to-run noise.
PROMOTION_MARGIN = 0.10  # +10 percentage points


def resolve_model(provider: str, *, override: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """The codegen model for ``provider``: explicit override > per-provider env > default.

    ``provider`` is one of ``PROVIDER_MODELS``; an unknown one is a hard error so a
    typo can't silently fall through to a default model (and a different bill).
    """
    if override:
        return override
    env = env or {}
    env_key = _PROVIDER_ENV.get(provider)
    if env_key and env.get(env_key):
        return env[env_key]
    if provider not in PROVIDER_MODELS:
        raise ValueError(f"unknown provider {provider!r}; choose one of {sorted(PROVIDER_MODELS)}")
    return PROVIDER_MODELS[provider]


def _failure_mode(result: Mapping[str, Any], accepted: bool) -> str | None:
    """Why a run was not (independently) accepted — the honest failure tally.

    Ordered from earliest pipeline stage to latest so the *first* thing that broke
    is named: the model's own tests, then the held-out judge, then preflight, then
    fit. ``None`` when accepted."""
    if accepted:
        return None
    if not result.get("tests_pass"):
        return "tests"
    if result.get("held_out_ran") and not result.get("held_out_pass"):
        return "heldout"
    if not result.get("preflight_pass"):
        return "preflight"
    if not result.get("fit"):
        return "fit"
    return "unknown"


def outcome_from_result(result: Mapping[str, Any]) -> ArmOutcome:
    """Map a ``codegen_benchmark.run_ticket`` result dict → a scored ``ArmOutcome``.

    Acceptance is the **independent (held-out)** verdict when a held-out suite ran
    — the spec's headline metric — falling back to the self-graded ``accepted``
    for stock tickets that ship none. Refine cycles become ``iterations``; a
    rejected run counts as an intervention (a human would have had to step in).
    """
    independent = result.get("independent_accepted")
    accepted = bool(independent) if independent is not None else bool(result.get("accepted"))
    findings = result.get("semgrep_findings")
    detail_bits = [f"refines={result.get('refines', 0)}"]
    if findings is not None:
        detail_bits.append(f"semgrep={findings}")
    detail_bits.append(f"reuse={'y' if result.get('reuse_ok') else 'n'}")
    return ArmOutcome(
        accepted=accepted,
        cost_usd=float(result.get("cost_usd") or 0.0),
        iterations=int(result.get("refines") or 0),
        intervened=not accepted,
        failure_mode=_failure_mode(result, accepted),
        detail=" ".join(detail_bits),
    )


@dataclass(frozen=True)
class Verdict:
    """The promotion decision for one skill against the pre-registered bar."""

    skill: str
    baseline_rate: float
    treatment_rate: float
    margin: float
    promote: bool

    @property
    def delta(self) -> float:
        return self.treatment_rate - self.baseline_rate

    def summary(self) -> str:
        decision = "PROMOTE" if self.promote else "HOLD"
        return (
            f"{self.skill}: baseline {self.baseline_rate:.0%} → treatment "
            f"{self.treatment_rate:.0%} (Δ {self.delta:+.1%}; bar +{self.margin:.0%}) → {decision}"
        )


def promotion_verdict(
    skill: str, baseline: Scorecard, treatment: Scorecard, *, margin: float = PROMOTION_MARGIN
) -> Verdict:
    """Apply the pre-registered bar to a baseline-vs-treatment pair.

    A skill clears only when its held-out-acceptance delta meets ``margin`` (a
    real margin, not run-to-run noise — size the run for power, not this fn). The
    decision is intentionally one number with a fixed threshold so it can't be
    rationalized after the fact."""
    base_rate = float(baseline.metrics()["acceptance_rate"])
    treat_rate = float(treatment.metrics()["acceptance_rate"])
    return Verdict(
        skill=skill,
        baseline_rate=base_rate,
        treatment_rate=treat_rate,
        margin=margin,
        # round so float noise (0.5 - 0.4 = 0.0999…) can't flip the verdict.
        promote=round(treat_rate - base_rate, 9) >= margin,
    )


__all__ = [
    "PROMOTION_MARGIN",
    "PROVIDER_MODELS",
    "Verdict",
    "outcome_from_result",
    "promotion_verdict",
    "resolve_model",
]
