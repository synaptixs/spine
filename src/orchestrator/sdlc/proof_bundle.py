"""The facts a Spine PR's proof bundle may state, and what the run recorded to supply them.

**Why this exists.** A Spine PR carries the ticket and its criteria. What the run actually
established — what failed before the change, what the acceptance judge said about each
criterion, how risky the change is, what it cost — lives in log lines and a Jira worklog, not
on the PR the reviewer reads. The bundle puts it there. This module is its first half: the
*facts*, gathered while the run happens. Rendering them is a later step and lives beside them.

**Every fact has a state for "not recorded".** A reviewer reads absence as a clean bill, so
each fact says which of its states it is in: the baseline was *not taken* (not "clean"), it was
*unreadable* (not "no failures"), the blast radius was *not measured* (not 0). Four of these are
not recorded at all on the publish path today, and a blank or a zero would be a claim.

**Pure, no model.** Nothing here calls an LLM, the network or the clock: the same run state
yields the same facts. The only I/O is ``assess_risk``'s one measurement, run in a thread,
and it is allowed to fail — a measurement that cannot be made is a state, never an error that
ends the run (the same rule as the worklog: telemetry never fails the work).

``SDLC_PROOF_BUNDLE=0`` (or ``false``, ``off``, ``no``) turns the whole thing off.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.core.llm.recording import TokenLedger
    from orchestrator.sdlc.baseline import BaselineCheck
    from orchestrator.sdlc.review import CriterionVerdict

_OFF_VALUES = {"0", "false", "no", "off"}


def enabled() -> bool:
    """On unless ``SDLC_PROOF_BUNDLE`` is ``0``, ``false``, ``no`` or ``off``."""
    return (os.getenv("SDLC_PROOF_BUNDLE") or "1").strip().lower() not in _OFF_VALUES


@dataclass(frozen=True)
class JudgeFacts:
    """What the acceptance judge concluded, and how many corrections it took to get there."""

    verdict: str
    summary: str = ""
    criteria: tuple[CriterionVerdict, ...] = ()
    blockers: tuple[str, ...] = ()
    uncertain: tuple[str, ...] = ()
    #: Corrections the judge asked for before it approved (0 = approved the first time).
    revisions: int = 0

    @classmethod
    def from_verdict(cls, verdict: Any, *, revisions: int) -> JudgeFacts:
        """Read a verdict tolerantly: a stub or third-party reviewer predates ``criteria``."""
        return cls(
            verdict=str(getattr(verdict, "verdict", "")),
            summary=str(getattr(verdict, "summary", "") or ""),
            criteria=tuple(getattr(verdict, "criteria", None) or ()),
            blockers=tuple(str(b) for b in (getattr(verdict, "blockers", None) or ())),
            uncertain=tuple(str(u) for u in (getattr(verdict, "uncertain", None) or ())),
            revisions=revisions,
        )


@dataclass(frozen=True)
class BaselineFacts:
    """What the suite did on the untouched tree, before any change.

    ``state`` is one of ``not_taken`` (no baseline was run: the runner names no failures, or
    ``SDLC_TEST_BASELINE=0``), ``clean`` (it ran and passed), ``preexisting`` (it ran and these
    failures predate the change) or ``unreadable`` (it ran and could not be read).
    """

    state: str
    failures: tuple[str, ...] = ()

    @classmethod
    def not_taken(cls) -> BaselineFacts:
        return cls(state="not_taken")

    @classmethod
    def from_check(cls, check: BaselineCheck) -> BaselineFacts:
        if check.problems is None:
            return cls(state="unreadable")
        if not check.problems:
            return cls(state="clean")
        return cls(state="preexisting", failures=tuple(sorted(check.problems)))


@dataclass(frozen=True)
class RiskFacts:
    """The escalation policy's reading of the change — annotation only, it blocks nothing.

    ``state`` is ``computed`` or ``not_computed`` (``detail`` says why). ``blast_radius`` is
    ``None`` when it was *not measured*, which is not the same as 0: the measurement reads
    Python files only, and a Java or Markdown change would otherwise report "nothing depends
    on this" about code it never looked at.
    """

    state: str
    detail: str = ""
    escalate: bool = False
    reasons: tuple[str, ...] = ()
    blast_radius: int | None = None
    radius_symbol: str = ""
    iterations: int = 0

    @classmethod
    def not_computed(cls, detail: str) -> RiskFacts:
        return cls(state="not_computed", detail=detail)


@dataclass(frozen=True)
class CostFacts:
    """What the run's model calls cost, as the ledger saw them — a list-price estimate.

    The ledger is not an invoice: on the P6 benchmark it ran 2.8% under the usage proxy's true
    spend. ``unpriced_calls`` and ``unreported_calls`` count calls whose figure is 0 because it
    is *unknown*, not because nothing was used.
    """

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    models: tuple[str, ...] = ()
    unpriced_calls: int = 0
    unreported_calls: int = 0

    @classmethod
    def from_ledger(cls, ledger: TokenLedger) -> CostFacts:
        total = ledger.total()
        return cls(
            calls=total.calls,
            prompt_tokens=total.prompt_tokens,
            completion_tokens=total.completion_tokens,
            cost_usd=total.cost_usd,
            models=tuple(total.models),
            unpriced_calls=total.unpriced_calls,
            unreported_calls=total.unreported_calls,
        )


@dataclass(frozen=True)
class RunFacts:
    """Everything the bundle may state about one run, as recorded when it passed."""

    issue_key: str
    intent_id: str
    title: str
    summary: str
    acceptance_criteria: tuple[str, ...]
    files: tuple[str, ...]
    #: ``None`` when no judge verdict was recorded.
    judge: JudgeFacts | None
    baseline: BaselineFacts
    risk: RiskFacts
    #: ``no_manifest`` (the project has none; the check self-skipped), ``passed`` (a manifest
    #: ran green) or ``not_reached`` (an earlier gate ended the loop first).
    required_behavior: str
    #: Test runs this build took (the escalation policy's "refinement effort").
    iterations: int
    cost: CostFacts = field(default_factory=CostFacts)
    coverage_withdrawn: tuple[str, ...] = ()
    coverage_withdrawn_production: tuple[str, ...] = ()


async def assess_risk(path: str, files: list[str], *, uncertain: list[str], iterations: int) -> RiskFacts:
    """Run the escalation policy over this change; never raises.

    The blast radius is measured only when the change touches a Python file, in a thread
    (it re-extracts the worktree, which is seconds of CPU on a large repository). The policy
    then weighs it with the judge's doubts and the refine cycles — and those two still count
    when the radius could not be measured.
    """
    from orchestrator.sdlc import escalation

    measured = any(f.endswith(".py") for f in files)
    try:
        radius, symbol = await asyncio.to_thread(escalation.blast_radius, path) if measured else (0, "")
        decision = escalation.EscalationPolicy().decide(
            escalation.EscalationSignals(
                uncertain_criteria=list(uncertain),
                iterations=iterations,
                blast_radius=radius,
                radius_symbol=symbol,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a failed measurement is a state, not a failed run
        return RiskFacts.not_computed(f"measurement failed: {type(exc).__name__}")
    return RiskFacts(
        state="computed",
        escalate=decision.escalate,
        reasons=tuple(decision.reasons),
        blast_radius=radius if measured else None,
        radius_symbol=symbol,
        iterations=iterations,
    )


__all__ = [
    "BaselineFacts",
    "CostFacts",
    "JudgeFacts",
    "RiskFacts",
    "RunFacts",
    "assess_risk",
    "enabled",
]
