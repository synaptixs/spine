"""Sprint 10.7: on-failure dispatch for the verifier chain.

Sprint 8 already defined ``FailureAction`` (`continue_with_warning`,
`terminate`, `replan`, `escalate_to_human`, `insert_verifier`). This
module turns a verifier-chain ``ChainResult`` plus a per-edge policy
into a concrete next-step decision.

Routing:

- ``outcome == pass``        → ``CONTINUE``, no action.
- ``outcome == warn``        → ``CONTINUE`` with the on_warn action's
                               intent recorded (warn-only today).
- ``outcome == fail``        → action drives:
  - ``terminate``               → ``TERMINATE``
  - ``continue_with_warning``   → ``CONTINUE`` (downgraded)
  - ``replan``                  → ``CONTINUE`` (Sprint 12 wires the
                                  real replan loop; intent audited).
  - ``escalate_to_human``       → ``CONTINUE`` (Sprint 14 wires the
                                  approval queue; intent audited).
  - ``insert_verifier``         → ``CONTINUE`` (no-op; chain already
                                  ran the verifiers configured).

Every dispatch returns a ``DispatchDecision`` that the runtime serialises
into ``node_outputs`` so the audit log captures the intended next step
even when the eventual handler isn't online yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orchestrator.runtime.post_conditions import FailureAction
from orchestrator.runtime.verifiers.base import VerifierOutcome
from orchestrator.runtime.verifiers.chain import ChainResult


class NextStep(str, Enum):
    CONTINUE = "continue"
    TERMINATE = "terminate"
    REPLAN = "replan"


@dataclass(frozen=True)
class FailurePolicy:
    """Per-edge handler policy. Defaults mirror the Sprint 5 contract."""

    on_fail: FailureAction = FailureAction.TERMINATE
    on_warn: FailureAction = FailureAction.CONTINUE_WITH_WARNING
    on_low_confidence: FailureAction = FailureAction.CONTINUE_WITH_WARNING


@dataclass(frozen=True)
class DispatchDecision:
    next_step: NextStep
    action: FailureAction
    outcome: VerifierOutcome
    rationale: str
    intent: str | None = None

    def to_state_value(self) -> dict[str, str | None]:
        return {
            "next_step": self.next_step.value,
            "action": self.action.value,
            "outcome": self.outcome.value,
            "rationale": self.rationale,
            "intent": self.intent,
        }


# Actions whose dispatchers can't fire at this layer. They reduce to CONTINUE
# today and record their intent on the audit log so reviewers see what *would*
# have happened. Each entry explains why; flipped to real behaviour when the
# corresponding sprint lands the handler (or the action moves to a different
# dispatch surface entirely).
_NOT_YET_IMPLEMENTED: dict[FailureAction, str] = {
    # Sprint 14 will land the approval queue: a worker pulls the failing task,
    # surfaces it to a human, and a signal resumes execution after approval.
    # Until then, the audit-log intent is the user-visible breadcrumb.
    FailureAction.ESCALATE_TO_HUMAN: "Sprint 14 wires the approval queue",
    # Architecturally INSERT_VERIFIER belongs at graph-build time (insert an
    # extra verifier in the chain before re-running the failing node), not at
    # post-chain dispatch. By the time we're here, the chain has already run.
    # Keeping the enum value live so post_conditions can record the intent,
    # but the actual handler — when it lands — needs to live in graphs.py /
    # chain_factory wiring, not here.
    FailureAction.INSERT_VERIFIER: (
        "INSERT_VERIFIER applies at graph-build time, not at post-chain dispatch; "
        "intent recorded for audit until graph-build wiring lands"
    ),
}


def dispatch(result: ChainResult, *, policy: FailurePolicy) -> DispatchDecision:
    """Apply the policy to a chain result. Pure function — no side effects."""
    outcome = result.outcome
    if outcome is VerifierOutcome.PASS:
        return DispatchDecision(
            next_step=NextStep.CONTINUE,
            action=FailureAction.CONTINUE_WITH_WARNING,
            outcome=outcome,
            rationale="all verifiers passed",
        )

    action = policy.on_warn if outcome is VerifierOutcome.WARN else policy.on_fail

    if action is FailureAction.TERMINATE:
        return DispatchDecision(
            next_step=NextStep.TERMINATE,
            action=action,
            outcome=outcome,
            rationale=f"verifier chain returned {outcome.value}; policy=terminate",
        )

    if action is FailureAction.CONTINUE_WITH_WARNING:
        return DispatchDecision(
            next_step=NextStep.CONTINUE,
            action=action,
            outcome=outcome,
            rationale=f"verifier chain returned {outcome.value}; policy=continue_with_warning",
        )

    if action is FailureAction.REPLAN:
        # Sprint 12: the orchestration layer reads this signal off the chain's
        # node_outputs slot, asks the planner for a revised IR, and resumes.
        return DispatchDecision(
            next_step=NextStep.REPLAN,
            action=action,
            outcome=outcome,
            rationale=f"verifier chain returned {outcome.value}; policy=replan",
        )

    if action in _NOT_YET_IMPLEMENTED:
        return DispatchDecision(
            next_step=NextStep.CONTINUE,
            action=action,
            outcome=outcome,
            rationale=(f"verifier chain returned {outcome.value}; policy={action.value} warn-only today"),
            intent=_NOT_YET_IMPLEMENTED[action],
        )

    # Defensive: every FailureAction value should be handled by one of the
    # branches above (or land in _NOT_YET_IMPLEMENTED with a documented reason).
    # If a new enum value gets added without a dispatcher branch, raise so the
    # gap surfaces in tests instead of degrading silently to CONTINUE.
    raise ValueError(
        f"failure_dispatch.dispatch: no branch for FailureAction.{action.name}. "
        f"Add a handler above or register it in _NOT_YET_IMPLEMENTED with a "
        f"documented reason."
    )
