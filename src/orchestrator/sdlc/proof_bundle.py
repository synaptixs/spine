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
import os
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


# ---- the bundle ------------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def requirement_text(facts: RunFacts) -> str:
    """The requirement as the run saw it: the title, the summary and the stated criteria."""
    return f"{facts.title}\n\n{facts.summary}\n\n" + "\n".join(f"- {c}" for c in facts.acceptance_criteria)


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
                "criterion": r.criterion[:_MAX_CRITERION_CHARS],
                "status": r.status,
                "evidence": r.evidence[:_MAX_EVIDENCE_CHARS],
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
        detail = "The suite passed after the change. No baseline was taken before it."
    elif base.state == "unreadable":
        gaps.append("pre-change test baseline could not be read")
        detail = "The suite passed after the change. The pre-change run could not be read."
    elif base.state == "preexisting":
        detail = (
            f"The suite passed after the change, apart from {len(base.failures)} failure(s) that predate it."
        )
    else:
        detail = "The suite passed before the change and after it."
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
        return {"status": CHECKED, "detail": "The project's required-behaviour manifest ran green."}
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
    if tests or production:
        detail = (
            "Work was withdrawn because it could not be made to pass or was not exercised: "
            "that coverage is not proven."
        )
    else:
        detail = "Nothing was withdrawn."
    return {"status": CHECKED, "detail": detail, "tests": tests, "production": production}


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
        gaps.append("blast radius not measured (the change touches no Python file)")
        radius = "Blast radius not measured: the change touches no Python file."
    else:
        radius = (
            f"Blast radius {risk.blast_radius} cross-file caller(s)"
            + (f", worst at `{risk.radius_symbol}`" if risk.radius_symbol else "")
            + "."
        )
    flag = (
        "The escalation policy flags this change for attention."
        if risk.escalate
        else ("The escalation policy raised no flag.")
    )
    return {
        "status": CHECKED,
        "detail": f"{flag} {radius}",
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


def build_bundle(
    facts: RunFacts,
    *,
    diff_sha256: str | None = None,
    spine_version: str = "",
    fixes_after_judge: int | None = None,
) -> dict[str, Any]:
    """Turn the run's facts into the bundle: a JSON-able dict, the same facts giving the same bytes.

    Nothing is inferred and nothing is blank: a fact that was not recorded is a check whose status
    is ``not_checked``, a ``null`` field, and a line in ``gaps``. ``diff_sha256`` is the hash of the
    final diff, taken by the caller at publish time (this function reads no repository).
    ``fixes_after_judge`` is how many review-loop fixes were committed after the judge looked;
    ``None`` means the caller does not know, which the bundle says.
    """
    gaps: list[str] = []
    checks = {
        "acceptance": _acceptance(facts, fixes_after_judge, gaps),
        "tests": _tests(facts, gaps),
        "required_behavior": _required_behavior(facts, gaps),
        "withdrawn": _withdrawn(facts),
        "risk": _risk(facts, gaps),
    }
    if diff_sha256 is None:
        gaps.append("diff hash not recorded")
    bundle: dict[str, Any] = {
        "schema": SCHEMA,
        "spine_version": spine_version,
        "issue_key": facts.issue_key,
        "intent_id": facts.intent_id,
        "title": facts.title,
        "requirement": {
            "criteria": [c[:_MAX_CRITERION_CHARS] for c in facts.acceptance_criteria[:_MAX_CRITERIA]],
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
        "integrity": {
            "requirement_sha256": sha256_text(requirement_text(facts)),
            "diff_sha256": diff_sha256,
            "record_sha256": "",
        },
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
}


def _cell(text: object, limit: int = _MD_CELL_CHARS) -> str:
    """One table cell: a single line, pipes escaped, bounded."""
    flat = " ".join(str(text).split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


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
        lines.append(f"| {label} | {check['status'].replace('_', ' ')} | {_cell(check['detail'])} |")
    acceptance = checks["acceptance"]
    if acceptance["criteria"]:
        shown, total = len(acceptance["criteria"]), acceptance["criteria_total"]
        lines += ["", f"**Criteria as judged** ({shown} of {total})", ""]
        lines += ["| Criterion | Judge | Evidence |", "|---|---|---|"]
        for row in acceptance["criteria"]:
            lines.append(
                f"| {_cell(row['criterion'])} | {_cell(row['status'], 20)} | {_cell(row['evidence'], 150)} |"
            )
    baseline = checks["tests"]["baseline"]
    if baseline["failures"]:
        listed = baseline["failures"][:_MD_LISTED]
        more = baseline["failures_total"] - len(listed)
        lines += [
            "",
            "**Failures that predate the change:** " + ", ".join(f"`{_cell(f, 80)}`" for f in listed),
        ]
        if more > 0:
            lines[-1] += f" (+{more} more)"
    withdrawn = checks["withdrawn"]
    for kind in ("tests", "production"):
        if withdrawn[kind]:
            lines += [
                "",
                f"**Withdrawn ({kind}):** "
                + ", ".join(f"`{_cell(f, 80)}`" for f in withdrawn[kind][:_MD_LISTED]),
            ]
    risk = checks["risk"]
    if risk["reasons"]:
        lines += ["", "**Risk reasons:** " + "; ".join(_cell(r, 160) for r in risk["reasons"])]
    change = bundle["change"]
    shown_files = change["files"][:_MD_LISTED]
    lines += [
        "",
        f"**Files changed:** {change['files_total']}"
        + (": " + ", ".join(f"`{_cell(f, 80)}`" for f in shown_files) if shown_files else "")
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
        + (f" · models: {', '.join(cost['models'])}" if cost["models"] else "")
        + ("".join(f" · {n}" for n in cost["notes"])),
    ]
    if bundle["gaps"]:
        lines += ["", "**Not recorded:**"] + [f"- {_cell(g, 240)}" for g in bundle["gaps"]]
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
    "sha256_text",
    "verify_bundle",
    "write_bundle",
]
