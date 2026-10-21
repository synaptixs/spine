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
import hashlib
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.core.llm.recording import TokenLedger
    from orchestrator.sdlc.baseline import BaselineCheck
    from orchestrator.sdlc.review import CriterionVerdict

logger = logging.getLogger(__name__)

_OFF_VALUES = {"0", "false", "no", "off"}

#: The bundle's schema id. Changes are additive only: a reader of ``/1`` keeps working.
SCHEMA = "spine.proof-bundle/1"
BUNDLE_FILENAME = "proof-bundle.json"

CHECKED = "checked"
NOT_CHECKED = "not_checked"
NOT_APPLICABLE = "not_applicable"

# Bounds (CLAUDE.md invariant 7): every list says "top N of M" rather than let a clipped view
# imply completeness, and the section as a whole stays far below GitHub's 65,536-character limit.
_MAX_CRITERIA = 20
_MAX_CRITERION_CHARS = 500
_MAX_EVIDENCE_CHARS = 300
_MAX_FILES = 50
_MAX_FAILURES = 30
_MAX_FINDINGS = 20
_MAX_FINDING_CHARS = 300
#: What the review loop treats as worth a fix (``reviewloop._ACTIONABLE``); a nit is advisory.
_ACTIONABLE = ("blocker", "warning")
_MD_CELL_CHARS = 200
_MD_LISTED = 10


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
class ReviewFinding:
    """One finding the automated review left unresolved."""

    severity: str
    path: str
    line: int
    message: str


@dataclass(frozen=True)
class ReviewFacts:
    """How the automated review loop ended — the outcome that makes a PR a draft.

    Read from the loop's own result, never inferred. ``clean`` is only true when the loop said
    so *and* left no blocker or warning: the loop's flag is what makes a PR a draft, and it ends
    clean with nits left (they are ``advisory``). A flag and a list that disagree about a blocker or
    a warning are read the safer way.
    """

    clean: bool
    rounds: int = 0
    #: Why the loop stopped, in its own words ("a fix broke the tests", "the review crashed").
    stopped: str = ""
    unresolved: tuple[ReviewFinding, ...] = ()
    #: Findings left on a review that ended clean: advisory (a nit), for a human to weigh.
    advisory: tuple[ReviewFinding, ...] = ()
    #: Findings the loop set aside as not the fixer's to decide; counted, not listed.
    deferred_total: int = 0

    @classmethod
    def from_loop(cls, result: Any) -> ReviewFacts:
        """Read a ``reviewloop.LoopResult`` tolerantly (this module imports nothing from the loop)."""
        left = tuple(
            ReviewFinding(
                severity=str(getattr(getattr(f, "severity", ""), "value", getattr(f, "severity", ""))),
                path=str(getattr(f, "path", "")),
                line=int(getattr(f, "line", 0) or 0),
                message=str(getattr(f, "message", "")),
            )
            for f in (getattr(result, "remaining", None) or ())
        )
        actionable = tuple(f for f in left if f.severity in _ACTIONABLE)
        flagged_clean = bool(getattr(result, "clean", False))
        clean = flagged_clean and not actionable
        return cls(
            clean=clean,
            rounds=len(getattr(result, "rounds", None) or ()),
            stopped=str(getattr(result, "stopped", "") or ""),
            unresolved=actionable if flagged_clean else left,
            advisory=tuple(f for f in left if f not in actionable) if flagged_clean else (),
            deferred_total=len(getattr(result, "deferred", None) or ()),
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
    #: Test files the run wrote and withdrew because they were the only red tests (SSPN-227): the change
    #: was released unproven by its own tests.
    own_tests_withdrawn: tuple[str, ...] = ()


#: What the measuring process runs. Kept apart from the parent so that it can be killed: a thread
#: cannot be, and the interpreter waits for one at exit.
_PROBE = (
    "import json, sys\n"
    "from orchestrator.sdlc.escalation import blast_radius, changed_python_files\n"
    "changed = changed_python_files(sys.argv[1])\n"
    "if changed is None:\n"
    "    answer = 'git'\n"
    "elif not changed:\n"
    "    answer = 'nothing'\n"
    "else:\n"
    "    answer = blast_radius(sys.argv[1])\n"
    "print(json.dumps(answer))\n"
)


class _UnmeasurableError(Exception):
    """The measurement could not tell what changed, so it has no answer (not an answer of 0)."""


_DEFAULT_RISK_TIMEOUT = 120.0


def risk_timeout() -> float:
    """Seconds the blast-radius measurement may take: ``SDLC_PROOF_BUNDLE_RISK_TIMEOUT``, else 120.

    The measurement re-extracts the worktree. It took a median of 14 s and a worst of 64 s on the 35
    patches P6 released, and grows with the repository, so it is bounded. An unusable value (not a
    positive finite number) is the default, not an error.
    """
    try:
        value = float(os.getenv("SDLC_PROOF_BUNDLE_RISK_TIMEOUT", ""))
    except ValueError:
        return _DEFAULT_RISK_TIMEOUT
    return value if math.isfinite(value) and value > 0 else _DEFAULT_RISK_TIMEOUT


async def _measure_blast(path: str, timeout: float) -> tuple[int, str] | None:
    """``escalation.blast_radius(path)``, run in a child process that is killed after ``timeout``.

    ``None`` when git lists no added or modified Python file (a deleted file leaves nothing to
    extract, and ``blast_radius`` would answer 0 for it: not the same as "nothing depends on it").
    Raises ``_UnmeasurableError`` when git could not list the changes at all, ``TimeoutError`` when the
    limit passes (the child is killed and reaped first) and anything else when the child fails or
    prints something unreadable.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _PROBE,
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:  # the limit, or this task being cancelled: either way, leave no child behind
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"the measurement exited {proc.returncode}")
    answer = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
    if answer == "git":
        raise _UnmeasurableError("git could not list the changed files")
    if answer == "nothing":
        return None
    radius, symbol = answer
    return int(radius), str(symbol)


async def assess_risk(
    path: str,
    files: list[str],
    *,
    uncertain: list[str],
    iterations: int,
    timeout: float | None = None,
) -> RiskFacts:
    """Run the escalation policy over this change; never raises.

    The blast radius is measured only when the change touches a Python file, in a child process
    killed after ``timeout`` seconds (default ``risk_timeout()``): it re-extracts the worktree,
    which is seconds of CPU on a large repository and has no other bound. A measurement that
    times out or fails is a ``not_computed`` state, never an error. The policy then weighs the
    radius with the judge's doubts and the refine cycles — and those two still count when the
    radius could not be measured.
    """
    from orchestrator.sdlc import escalation

    candidate = any(f.endswith(".py") for f in files)
    limit = timeout if timeout is not None else risk_timeout()
    detail = ""
    try:
        outcome = await _measure_blast(path, limit) if candidate else None
        measured = outcome is not None
        radius, symbol = outcome if outcome is not None else (0, "")
        if candidate and not measured:
            detail = (
                "no added or modified Python file to measure "
                "(the dependents of a deleted file are not counted)"
            )
        decision = escalation.EscalationPolicy().decide(
            escalation.EscalationSignals(
                uncertain_criteria=list(uncertain),
                iterations=iterations,
                blast_radius=radius,
                radius_symbol=symbol,
            )
        )
    except _UnmeasurableError as exc:
        return RiskFacts.not_computed(str(exc))
    except TimeoutError:
        return RiskFacts.not_computed(f"timed out after {limit:g} s")
    except Exception as exc:  # noqa: BLE001 — a failed measurement is a state, not a failed run
        return RiskFacts.not_computed(f"measurement failed: {type(exc).__name__}")
    return RiskFacts(
        state="computed",
        detail=detail,
        escalate=decision.escalate,
        reasons=tuple(decision.reasons),
        blast_radius=radius if measured else None,
        radius_symbol=symbol,
        iterations=iterations,
    )


# ---- the bundle ------------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def requirement_text(facts: RunFacts) -> str:
    """The requirement as the run saw it: the title, the summary and the stated criteria."""
    return f"{facts.title}\n\n{facts.summary}\n\n" + "\n".join(f"- {c}" for c in facts.acceptance_criteria)


def _capped(text: str, limit: int) -> str:
    """``text`` redacted, then cut to ``limit``. The other order lets a credential that straddles
    the cut escape the redaction as a fragment."""
    from orchestrator.sdlc.attempt_record import scrub

    return scrub(text)[:limit]


def _scrubbed(obj: Any) -> Any:
    """``obj`` with the value of every credential-named environment variable replaced by ``[redacted]``.

    The same rule as the attempt record (``attempt_record.scrub``), applied to every string the bundle
    copies from the run: the ticket, the judge's evidence, file and test names, reasons. A bundle goes
    into a PR body and, in CI, an uploaded artifact, and either outlives the run.
    """
    from orchestrator.sdlc.attempt_record import scrub

    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [_scrubbed(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrubbed(v) for k, v in obj.items()}
    return obj


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(bundle: dict[str, Any]) -> str:
    """Hash of the bundle's own content, with its hash cell blanked: the record, not its seal."""
    body = _without_record_hash(bundle)
    return sha256_text(_canonical(body))


def _without_record_hash(bundle: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(json.dumps(bundle))
    integrity = body.get("integrity")
    if isinstance(integrity, dict):
        integrity.pop("record_sha256", None)
    return body  # type: ignore[no-any-return]


def verify_bundle(bundle: dict[str, Any]) -> bool:
    """True when the bundle's content still matches the hash it carries."""
    try:
        return bool(bundle["integrity"]["record_sha256"] == _record_hash(bundle))
    except (KeyError, TypeError, ValueError):
        return False


def _acceptance(facts: RunFacts, fixes_after_judge: int | None, gaps: list[str]) -> dict[str, Any]:
    judge = facts.judge
    if fixes_after_judge is None:
        gaps.append("whether review-loop fixes followed the judge is not recorded")
        later = "Whether review-loop fixes followed the judge is not recorded."
    elif fixes_after_judge:
        later = f"The judge reviewed the build; {fixes_after_judge} review-loop fix(es) came after it."
    else:
        later = "No review-loop fixes followed the judge."
    if judge is None:
        gaps.append("acceptance judge verdict not recorded")
        return {
            "status": NOT_CHECKED,
            "detail": "No acceptance judge verdict was recorded.",
            "verdict": None,
            "summary": "",
            "revisions": None,
            "rows_recorded": False,
            "counts": None,
            "criteria": [],
            "criteria_total": 0,
            "uncertain": [],
            "blockers": [],
            "fixes_after_judge": fixes_after_judge,
        }
    rows = list(judge.criteria)
    rows_recorded = bool(rows)
    counts: dict[str, int] | None = None
    if rows_recorded:
        counts = {"met": 0, "unmet": 0, "uncertain": 0, "other": 0}
        for row in rows:
            counts[row.status if row.status in counts else "other"] += 1
        detail = (
            f"Verdict {judge.verdict}: {counts['met']} met, {counts['unmet']} unmet, "
            f"{counts['uncertain']} uncertain"
            + (f", {counts['other']} other" if counts["other"] else "")
            + f". {later}"
        )
    else:
        gaps.append("per-criterion judge rows not recorded")
        detail = f"Verdict {judge.verdict}; the judge recorded no per-criterion rows. {later}"
    return {
        "status": CHECKED,
        "detail": detail,
        "verdict": judge.verdict,
        "summary": judge.summary,
        "revisions": judge.revisions,
        "rows_recorded": rows_recorded,
        "counts": counts,
        "criteria": [
            {
                "criterion": _capped(r.criterion, _MAX_CRITERION_CHARS),
                "status": r.status,
                "evidence": _capped(r.evidence, _MAX_EVIDENCE_CHARS),
            }
            for r in rows[:_MAX_CRITERIA]
        ],
        "criteria_total": len(rows),
        "uncertain": list(judge.uncertain),
        "blockers": list(judge.blockers),
        "fixes_after_judge": fixes_after_judge,
    }


def _tests(facts: RunFacts, gaps: list[str]) -> dict[str, Any]:
    base = facts.baseline
    if base.state == "not_taken":
        gaps.append(
            "pre-change test baseline not taken: failures that predate the change cannot be told apart"
        )
        detail = "The suite passed after the build. No baseline was taken before the change."
    elif base.state == "unreadable":
        gaps.append("pre-change test baseline could not be read")
        detail = "The suite passed after the build. The pre-change run could not be read."
    elif base.state == "preexisting":
        detail = (
            f"The suite passed after the build, apart from {len(base.failures)} "
            "failure(s) that predate the change."
        )
    else:
        detail = "The suite passed before the change and after the build."
    gaps.append("test pass/fail counts not recorded")
    return {
        "status": CHECKED,
        "detail": detail,
        "baseline": {
            "state": base.state,
            "failures": list(base.failures[:_MAX_FAILURES]),
            "failures_total": len(base.failures),
        },
        "counts": None,
        "test_runs": facts.iterations,
    }


def _required_behavior(facts: RunFacts, gaps: list[str]) -> dict[str, Any]:
    state = facts.required_behavior
    if state == "passed":
        return {
            "status": CHECKED,
            "detail": "The project's required-behaviour manifest ran green on the build.",
        }
    if state == "no_manifest":
        return {
            "status": NOT_APPLICABLE,
            "detail": "The project has no required-behaviour manifest.",
        }
    gaps.append("required-behaviour check not reached")
    return {
        "status": NOT_CHECKED,
        "detail": "The required-behaviour check was not reached.",
    }


def _withdrawn(facts: RunFacts) -> dict[str, Any]:
    tests = list(facts.coverage_withdrawn)
    production = list(facts.coverage_withdrawn_production)
    own = list(facts.own_tests_withdrawn)
    if own:
        detail = (
            "The tests this run wrote were withdrawn because they were the only red tests and no edit made "
            "them pass: the change is unproven by its own tests (its own tests do not pass)."
        )
    elif tests or production:
        detail = (
            "Work was withdrawn because it could not be made to pass or was not exercised: "
            "that coverage is not proven."
        )
    else:
        detail = "Nothing was withdrawn."
    return {
        "status": CHECKED,
        "detail": detail,
        "tests": tests,
        "production": production,
        "own_tests": own,
    }


def _risk(facts: RunFacts, gaps: list[str]) -> dict[str, Any]:
    risk = facts.risk
    if risk.state != "computed":
        gaps.append(f"risk not computed: {risk.detail}")
        return {
            "status": NOT_CHECKED,
            "detail": risk.detail,
            "escalate": False,
            "reasons": [],
            "blast_radius": None,
            "radius_symbol": "",
            "iterations": risk.iterations,
        }
    if risk.blast_radius is None:
        reason = risk.detail or "the change touches no Python file"
        gaps.append(f"blast radius not measured ({reason})")
        radius = f"Blast radius not measured: {reason}."
    else:
        radius = (
            f"Blast radius {risk.blast_radius} cross-file caller(s)"
            + (f", worst at `{risk.radius_symbol}`" if risk.radius_symbol else "")
            + " (measured on the build)."
        )
    return {
        "status": CHECKED,
        "detail": radius,
        "escalate": risk.escalate,
        "reasons": list(risk.reasons),
        "blast_radius": risk.blast_radius,
        "radius_symbol": risk.radius_symbol,
        "iterations": risk.iterations,
    }


def _cost(facts: RunFacts) -> dict[str, Any]:
    cost = facts.cost
    notes: list[str] = []
    if cost.unpriced_calls:
        notes.append(f"not priced on {cost.unpriced_calls} of {cost.calls} calls")
    if cost.unreported_calls:
        notes.append(f"usage not reported on {cost.unreported_calls} of {cost.calls} calls")
    return {
        "basis": "API list-price estimate, not an invoice",
        "usd": round(cost.cost_usd, 4),
        "calls": cost.calls,
        "prompt_tokens": cost.prompt_tokens,
        "completion_tokens": cost.completion_tokens,
        "models": list(cost.models),
        "unpriced_calls": cost.unpriced_calls,
        "unreported_calls": cost.unreported_calls,
        "notes": notes,
    }


def _finding_rows(findings: tuple[ReviewFinding, ...]) -> list[dict[str, Any]]:
    return [
        {
            "severity": f.severity,
            "path": f.path,
            "line": f.line,
            "message": _capped(f.message, _MAX_FINDING_CHARS),
        }
        for f in findings[:_MAX_FINDINGS]
    ]


def _review(review: ReviewFacts | None, expected: bool, gaps: list[str]) -> dict[str, Any]:
    """The review loop's outcome. ``expected`` is a path that reviews before it opens the PR."""
    empty: dict[str, Any] = {
        "outcome": "none",
        "rounds": None,
        "stopped": "",
        "unresolved": [],
        "unresolved_total": 0,
        "advisory": [],
        "advisory_total": 0,
        "deferred_total": 0,
    }
    if review is None:
        if expected:
            gaps.append("no automated review ran before this PR opened")
            return {
                "status": NOT_CHECKED,
                "detail": "No automated review ran before this PR opened.",
                **empty,
            }
        return {"status": NOT_APPLICABLE, "detail": "This path has no automated review loop.", **empty}
    if review.clean:
        detail = "Automated review: clean" + (f" after {review.rounds} round(s)." if review.rounds else ".")
    else:
        detail = (
            f"Automated review did not finish clean: {review.stopped or 'no reason recorded'}"
            f" ({len(review.unresolved)} unresolved finding(s))."
        )
    if review.advisory:
        detail += f" {len(review.advisory)} advisory finding(s) left for a human."
    if review.deferred_total:
        detail += f" {review.deferred_total} set aside for a human (not the fixer's to decide)."
    return {
        "status": CHECKED,
        "detail": detail,
        "outcome": "clean" if review.clean else "unresolved",
        "rounds": review.rounds,
        "stopped": review.stopped,
        "unresolved": _finding_rows(review.unresolved),
        "unresolved_total": len(review.unresolved),
        "advisory": _finding_rows(review.advisory),
        "advisory_total": len(review.advisory),
        "deferred_total": review.deferred_total,
    }


def build_bundle(
    facts: RunFacts,
    *,
    diff_sha256: str | None = None,
    spine_version: str = "",
    fixes_after_judge: int | None = None,
    review: ReviewFacts | None = None,
    review_expected: bool = False,
) -> dict[str, Any]:
    """Turn the run's facts into the bundle: a JSON-able dict, the same facts giving the same bytes.

    Nothing is inferred and nothing is blank: a fact that was not recorded is a check whose status
    is ``not_checked``, a ``null`` field, and a line in ``gaps``. ``diff_sha256`` is the hash of the
    final diff, taken by the caller at publish time (this function reads no repository).
    ``fixes_after_judge`` is how many review-loop fixes were committed after the judge looked;
    ``None`` means the caller does not know, which the bundle says. ``review`` is how the automated
    review loop ended; ``review_expected`` says the path reviews before it opens the PR, which makes
    a missing ``review`` a gap ("not checked") rather than "not applicable".
    """
    gaps: list[str] = []
    checks = {
        "acceptance": _acceptance(facts, fixes_after_judge, gaps),
        "tests": _tests(facts, gaps),
        "required_behavior": _required_behavior(facts, gaps),
        "withdrawn": _withdrawn(facts),
        "risk": _risk(facts, gaps),
        "review": _review(review, review_expected, gaps),
    }
    if diff_sha256 is None:
        gaps.append("diff hash not recorded")
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "spine_version": spine_version,
        "issue_key": facts.issue_key,
        "intent_id": facts.intent_id,
        "title": facts.title,
        "requirement": {
            "criteria": [_capped(c, _MAX_CRITERION_CHARS) for c in facts.acceptance_criteria[:_MAX_CRITERIA]],
            "criteria_total": len(facts.acceptance_criteria),
        },
        "change": {
            "files": list(facts.files[:_MAX_FILES]),
            "files_total": len(facts.files),
        },
        "checks": checks,
        "cost": _cost(facts),
        "gaps": gaps,
        "not_recorded": [name for name, check in checks.items() if check["status"] == NOT_CHECKED],
    }
    # Redact, then seal: the hashes are of the requirement as the run saw it and of the record as
    # it is written, and they are the only strings here that are not copied from the run.
    bundle: dict[str, Any] = _scrubbed(body)
    bundle["integrity"] = {
        "requirement_sha256": sha256_text(requirement_text(facts)),
        "diff_sha256": diff_sha256,
        "record_sha256": "",
    }
    bundle["integrity"]["record_sha256"] = _record_hash(bundle)
    return bundle


# ---- the PR-body section -----------------------------------------------------------------------

_LABELS = {
    "acceptance": "Acceptance criteria (judge)",
    "tests": "Tests",
    "required_behavior": "Required behaviours",
    "withdrawn": "Withdrawn work",
    "risk": "Risk",
    "review": "Automated review",
}


# Text the run copied in (criteria, evidence, finding messages, names, reasons) reaches a PR body, where
# GitHub renders it. It strips script, iframe, style and event attributes itself, but not the text's own
# HTML and Markdown (a remote image is a viewer-side beacon), and it links ``@name`` (and, once the body
# is posted, notifies that person), ``#N`` and ``org/repo#N``. The PR copy therefore neutralises every
# untrusted string; ``proof-bundle.json`` keeps the text as recorded and its hashes do not change.
#
# The characters that reorder or hide text, as code-point ranges: controls (not tab and newline), DEL and C1,
# zero-width and bidi marks, the word-joiner block, bidi isolates and the byte-order mark. A set, not a regex
# character class, so no range is spelled out in a pattern.
_HIDDEN_RANGES = (
    (0x00, 0x08),
    (0x0B, 0x1F),
    (0x7F, 0x9F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
)
_HIDDEN = frozenset(chr(code) for low, high in _HIDDEN_RANGES for code in range(low, high + 1))


def _strip_hidden(text: str) -> str:
    return "".join(ch for ch in text if ch not in _HIDDEN)


_WWW = re.compile(r"(?i)www\.")
_ZWSP = "\u200b"  # breaks a mention, a reference or an autolink; inserted only here, stripped from input
_ESCAPED = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "|": "\\|",
    "\\": "\\\\",
    **{ch: "\\" + ch for ch in "`*_~[]!$"},
    "@": "@" + _ZWSP,
    "#": "#" + _ZWSP,
}


def _flat(text: object) -> str:
    """One line, with the characters that reorder or hide text (bidi, zero-width, control) dropped."""
    return _strip_hidden(" ".join(str(text).split()))


def _inert(text: object, limit: int = _MD_CELL_CHARS) -> str:
    """Run text made safe to place in a table cell or a sentence of the PR body.

    HTML, emphasis, links and images show as the characters they are; a mention, an issue reference and a
    bare URL are broken so GitHub neither links nor notifies. ``limit`` counts the characters written, and
    an escape is never cut in half.
    """
    flat = _WWW.sub(lambda m: "www" + _ZWSP + ".", _flat(text).replace("://", ":" + _ZWSP + "//"))
    pieces = [_ESCAPED.get(ch, ch) for ch in flat]
    if sum(map(len, pieces)) <= limit:
        return "".join(pieces)
    kept: list[str] = []
    used = 1  # the ellipsis
    for piece in pieces:
        if used + len(piece) > limit:
            break
        kept.append(piece)
        used += len(piece)
    return "".join(kept) + "\u2026"


def _code(text: object, limit: int = _MD_CELL_CHARS, tail: str = "") -> str:
    """A name as a code span, in which GitHub renders nothing; ``tail`` (a line number) goes inside it.

    The fence is longer than any backtick run in the name, so the name cannot close its own span. Every
    use is a line of its own, never a table cell, so a pipe needs no escape (and would show as ``\\|``).
    """
    flat = _flat(text)
    if len(flat) > limit:
        flat = flat[: limit - 1] + "\u2026"
    flat += tail
    fence = "`" * (max((len(run) for run in re.findall(r"`+", flat)), default=0) + 1)
    pad = " " if flat.startswith("`") or flat.endswith("`") else ""
    return f"{fence}{pad}{flat}{pad}{fence}"


# The ticket's own words (a summary and criteria a model copied, told to keep identifiers verbatim) are
# Markdown the author meant: code spans, lists, emphasis, links. Escaping all of it would turn every
# ``identifier`` into literal backticks, so only what acts on someone else is broken, and only outside
# code: a mention (``@dataclass`` and ``@Override`` are real GitHub accounts), raw HTML, an image, a
# cross-repository reference. A local ``#N``, a link and the ticket's formatting stay as written.
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})(.*)")
_XREF = re.compile(r"[\w.-]+/[\w.-]+#(?=\d)")


def _break_markup(piece: str) -> str:
    """Prose with the parts that link, notify or load something broken; the rest as written."""
    piece = piece.replace("<", "&lt;").replace("@", "@" + _ZWSP).replace("![", "!\\[")
    return _XREF.sub(lambda m: m.group(0) + _ZWSP, piece)


def _break_line(line: str) -> str:
    """One line of prose: its code spans kept, everything between them broken.

    A span is a backtick run closed, on the same line, by a run of exactly the same length (CommonMark),
    and a run whose first backtick is backslash-escaped opens nothing. Anything else is text, so a doubt
    escapes too much and never too little.
    """
    out: list[str] = []
    prose: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i] != "`":
            prose.append(line[i])
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        run = j - i
        before = line[:i]
        escaped = (len(before) - len(before.rstrip("\\"))) % 2 == 1
        close = -1
        k = j
        while not escaped and close < 0 and k < n:
            if line[k] != "`":
                k += 1
                continue
            e = k
            while e < n and line[e] == "`":
                e += 1
            close = k if e - k == run else -1
            k = e
        if close < 0:
            prose.append(line[i : i + 1] if escaped else line[i:j])
            i += 1 if escaped else run
            continue
        out.append(_break_markup("".join(prose)))
        prose = []
        out.append(line[i : close + run])
        i = close + run
    out.append(_break_markup("".join(prose)))
    return "".join(out)


def neutralise_ticket_text(text: object) -> str:
    """The ticket's text for the PR body: its Markdown kept, what links, notifies or loads broken.

    Fenced blocks are kept whole, and one left open is closed so it cannot swallow the rest of the body.
    Characters that reorder or hide text are dropped. Pure, so the same text gives the same bytes, and a
    second pass changes nothing.
    """
    raw = _strip_hidden(str(text).replace("\r\n", "\n").replace("\r", "\n"))
    out: list[str] = []
    fence = ""  # the opening run while inside a fenced block
    for line in raw.split("\n"):
        if fence:
            out.append(line)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                fence = ""
            continue
        opened = _FENCE_OPEN.fullmatch(line)
        if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):
            fence = opened.group(1)
            out.append(line)
        else:
            out.append(_break_line(line))
    if fence:
        out.append(fence)
    return "\n".join(out)


# Public names for the two helpers other modules need, so they do not import a private one.
inert_text = _inert
code_span = _code


def _short(digest: str | None) -> str:
    return digest[:12] if digest else "not recorded"


def render_markdown(bundle: dict[str, Any]) -> str:
    """The section a reviewer reads in the PR body. Plain Markdown, nothing fetched."""
    lines = [
        "### Spine proof bundle",
        "",
        "*This lists what the run checked and what it did not. It is not a guarantee.*",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    checks = bundle["checks"]
    for name, label in _LABELS.items():
        check = checks[name]
        lines.append(f"| {label} | {check['status'].replace('_', ' ')} | {_inert(check['detail'])} |")
    acceptance = checks["acceptance"]
    if acceptance["criteria"]:
        shown, total = len(acceptance["criteria"]), acceptance["criteria_total"]
        lines += ["", f"**Criteria as judged** ({shown} of {total})", ""]
        lines += ["| Criterion | Judge | Evidence |", "|---|---|---|"]
        for row in acceptance["criteria"]:
            criterion, status, evidence = (
                _inert(row["criterion"]),
                _inert(row["status"], 20),
                _inert(row["evidence"], 150),
            )
            lines.append(f"| {criterion} | {status} | {evidence} |")
    baseline = checks["tests"]["baseline"]
    if baseline["failures"]:
        listed = baseline["failures"][:_MD_LISTED]
        more = baseline["failures_total"] - len(listed)
        lines += [
            "",
            "**Failures that predate the change:** " + ", ".join(_code(f, 80) for f in listed),
        ]
        if more > 0:
            lines[-1] += f" (+{more} more)"
    withdrawn = checks["withdrawn"]
    for kind in ("tests", "production"):
        if withdrawn[kind]:
            shown = withdrawn[kind][:_MD_LISTED]
            left_out = len(withdrawn[kind]) - len(shown)
            lines += [
                "",
                f"**Withdrawn ({kind}):** "
                + ", ".join(_code(f, 80) for f in shown)
                + (f" (+{left_out} more)" if left_out > 0 else ""),
            ]
    review = checks["review"]
    for key, heading in (
        ("unresolved", "Unresolved review findings"),
        ("advisory", "Advisory review findings"),
    ):
        if review[key]:
            listed = review[key][:_MD_LISTED]
            more = review[f"{key}_total"] - len(listed)
            lines += [
                "",
                f"**{heading}:** "
                + "; ".join(
                    _code(f["path"], 80, ":" + str(f["line"])) + " " + _inert(f["message"], 120)
                    for f in listed
                )
                + (f" (+{more} more)" if more > 0 else ""),
            ]
    risk = checks["risk"]
    if risk["reasons"]:
        lines += ["", "**Risk reasons:** " + "; ".join(_inert(r, 160) for r in risk["reasons"])]
    change = bundle["change"]
    shown_files = change["files"][:_MD_LISTED]
    lines += [
        "",
        f"**Files changed by the build:** {change['files_total']}"
        + (": " + ", ".join(_code(f, 80) for f in shown_files) if shown_files else "")
        + (
            f" (+{change['files_total'] - len(shown_files)} more)"
            if change["files_total"] > len(shown_files)
            else ""
        ),
    ]
    cost = bundle["cost"]
    lines += [
        "",
        f"**Cost:** ${cost['usd']:.4f} ({cost['basis']}) · {cost['calls']} call(s) · "
        f"{cost['prompt_tokens']:,} in / {cost['completion_tokens']:,} out tokens"
        + (f" · models: {', '.join(_inert(m, 80) for m in cost['models'])}" if cost["models"] else "")
        + ("".join(f" · {n}" for n in cost["notes"])),
    ]
    if bundle["gaps"]:
        lines += ["", "**Not recorded:**"] + [f"- {_inert(g, 240)}" for g in bundle["gaps"]]
    integrity = bundle["integrity"]
    lines += [
        "",
        f"<sub>{bundle['schema']} · requirement sha256 {_short(integrity['requirement_sha256'])} · "
        f"diff sha256 {_short(integrity['diff_sha256'])} · "
        f"record sha256 {_short(integrity['record_sha256'])}</sub>",
    ]
    return "\n".join(lines) + "\n"


# ---- the file ------------------------------------------------------------------------------------


def write_bundle(record_dir: Path | None, bundle: dict[str, Any]) -> Path | None:
    """Write ``proof-bundle.json`` under the run's artifacts directory; never raises.

    ``None`` means nothing was written: no directory was given, or it could not be written. A
    bundle that cannot be saved is one log line, because recording must never change how a run
    ends (the same rule as the attempt record).
    """
    if record_dir is None:
        return None
    try:
        record_dir = Path(record_dir)
        record_dir.mkdir(parents=True, exist_ok=True)
        path = record_dir / BUNDLE_FILENAME
        path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("sdlc.proof_bundle.write_failed: %s", exc)
        return None
    return path


__all__ = [
    "BUNDLE_FILENAME",
    "SCHEMA",
    "BaselineFacts",
    "CostFacts",
    "JudgeFacts",
    "RiskFacts",
    "RunFacts",
    "assess_risk",
    "build_bundle",
    "enabled",
    "render_markdown",
    "requirement_text",
    "risk_timeout",
    "sha256_text",
    "verify_bundle",
    "write_bundle",
]
