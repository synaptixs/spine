"""Block B.3: gap analysis over extracted intents.

Before a human approves a batch of intents (the first SDLC bookend), the
gap analyzer checks each one against a rule set and surfaces what's
incomplete or ambiguous. Findings carry a severity that drives the gate:

  - ``blocker``      — too incomplete to build (no real description). Must
                       fix before approval.
  - ``needs_input``  — the intent has open questions a human must answer.
  - ``warning``      — advisory (missing NFRs, thin scope).

Rules are declarative and live in YAML so adopters tune them without code
(the adoption lever: no-code config for the gate). Each rule is a
predicate over an ``Intent``; when the predicate *fails*, a finding fires.
Six check kinds cover the common cases:

  - ``field_present``  — a field is non-empty.
  - ``min_length``     — a string field is at least N chars.
  - ``min_items``      — a list field has at least N entries.
  - ``max_items``      — a list field has at most N entries.
  - ``pattern``        — a regex matches a string field, or (``mode``) any / every item of a
                         list field. An empty field is not judged — whether it must be there
                         is a ``field_present`` / ``min_items`` rule's business. A regex can
                         nudge, never judge, so the shipped pattern rules are warnings (D13).
  - ``question_state`` — every ``open_questions`` entry has a recorded ``Resolution``: answered
                         (not by a model's proposal), or deferred to a named owner.

Built-in defaults ship in code (so it works out of the box) and as a
copyable ``examples/gap_rules/intent_gaps.yaml``. The grey-zone LLM-judge
the plan mentions is a future extension; the default set is deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from orchestrator.intake.intents import Intent, question_states

_LIST_FIELDS = {
    "acceptance_criteria",
    "dependencies",
    "nfrs",
    "open_questions",
    "source_doc_ids",
    "users",
    "non_goals",
}
_STR_FIELDS = {"id", "title", "description", "scope", "problem", "outcome"}
_CHECK_KINDS = {"field_present", "min_length", "min_items", "max_items", "pattern", "question_state"}


class GapSeverity(str, Enum):
    BLOCKER = "blocker"  # gates approval — intent too incomplete to build
    NEEDS_INPUT = "needs_input"  # gates approval — human must resolve
    WARNING = "warning"  # advisory

    @property
    def gates_approval(self) -> bool:
        return self in (GapSeverity.BLOCKER, GapSeverity.NEEDS_INPUT)


class GapRule(BaseModel):
    """One declarative gap check. Fires a finding when its predicate fails."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    severity: GapSeverity
    check: str
    field: str
    count: int = 0
    length: int = 0
    # ``pattern`` only: the regex (case-insensitive), and whether a list field needs ``any``
    # item to match or ``all`` of them.
    pattern: str = ""
    mode: Literal["any", "all"] = "any"

    def model_post_init(self, _ctx: Any) -> None:
        if self.check not in _CHECK_KINDS:
            raise ValueError(f"unknown gap check {self.check!r}; expected one of {sorted(_CHECK_KINDS)}")
        if self.field not in (_LIST_FIELDS | _STR_FIELDS):
            raise ValueError(f"gap rule {self.id!r} targets unknown Intent field {self.field!r}")
        if self.check == "pattern":
            if not self.pattern:
                raise ValueError(f"gap rule {self.id!r} is a pattern check with no pattern")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"gap rule {self.id!r} has an invalid pattern: {exc}") from exc
        if self.check == "question_state" and self.field != "open_questions":
            raise ValueError(
                f"gap rule {self.id!r}: question_state checks open_questions, not {self.field!r}"
            )


@dataclass(frozen=True)
class GapFinding:
    rule_id: str
    intent_id: str
    severity: GapSeverity
    message: str


# What a regex can honestly say about an outcome or a criterion. These nudge; they do not judge
# (D13) — a miss is a warning a human can wave through.
_OBSERVABLE = (
    r"\d|%|\b(within|under|below|above|at least|at most|fewer|more than|less than|zero|no longer|"
    r"reduces?|increases?|decreases?|faster|slower|completes?|receives?|sees?|can|cannot|stops?|"
    r"returns?|appears?|closes?|reconciles?)\b"
)
_TESTABLE = (
    r"\b(given|when|then|shall|must|should|returns?|rejects?|displays?|shows?|creates?|sends?|logs?|"
    r"fails?|exits?|prints?|writes?|emits?|records?|stops?|allows?|prevents?|raises?|responds?|"
    r"redirects?|accepts?|refuses?)\b"
)

# The four rules every ``ingest`` has always run. Unchanged.
_CORE_RULES: tuple[GapRule, ...] = (
    GapRule(
        id="description_present",
        description="Intent must have a real description.",
        severity=GapSeverity.BLOCKER,
        check="min_length",
        field="description",
        length=10,
    ),
    GapRule(
        id="scope_declared",
        description="Intent should declare what is in / out of scope.",
        severity=GapSeverity.WARNING,
        check="field_present",
        field="scope",
    ),
    GapRule(
        id="open_questions_unresolved",
        description="Intent has open questions a human must resolve.",
        severity=GapSeverity.NEEDS_INPUT,
        check="max_items",
        field="open_questions",
        count=0,
    ),
    GapRule(
        id="nfrs_missing",
        description="Intent should list at least one non-functional requirement.",
        severity=GapSeverity.WARNING,
        check="min_items",
        field="nfrs",
        count=1,
    ),
)


def _clarity_rules(strict: bool) -> tuple[GapRule, ...]:
    """The requirements-clarity rules. ``strict`` gives the ideation severities; otherwise every
    rule is a warning, so an existing ``ingest`` is never gated by a rule it did not have (D14)."""

    def sev(strict_severity: GapSeverity) -> GapSeverity:
        return strict_severity if strict else GapSeverity.WARNING

    return (
        GapRule(
            id="problem_stated",
            description="Intent should state the problem it solves.",
            severity=sev(GapSeverity.BLOCKER),
            check="field_present",
            field="problem",
        ),
        GapRule(
            id="users_named",
            description="Intent should name who has the problem.",
            severity=sev(GapSeverity.NEEDS_INPUT),
            check="min_items",
            field="users",
            count=1,
        ),
        GapRule(
            id="outcome_stated",
            description="Intent should state the outcome that means it worked.",
            severity=sev(GapSeverity.NEEDS_INPUT),
            check="field_present",
            field="outcome",
        ),
        GapRule(
            id="outcome_observable",
            description="The outcome should be observable: a number, a threshold, or something a user sees.",
            severity=GapSeverity.WARNING,
            check="pattern",
            field="outcome",
            pattern=_OBSERVABLE,
        ),
        GapRule(
            id="non_goal_named",
            description="Intent should name at least one thing it will not do.",
            severity=GapSeverity.WARNING,
            check="min_items",
            field="non_goals",
            count=1,
        ),
        GapRule(
            id="criteria_testable",
            description="Each acceptance criterion should be testable: Given/When/Then, or SHALL + a result.",
            severity=GapSeverity.WARNING,
            check="pattern",
            field="acceptance_criteria",
            pattern=_TESTABLE,
            mode="all",
        ),
    )


# Built-in defaults — work without any YAML file present. The clarity rules ride along as
# warnings only: they never change what an ``ingest`` gates on.
DEFAULT_GAP_RULES: tuple[GapRule, ...] = (*_CORE_RULES, *_clarity_rules(strict=False))

# The ideation flow's set: the clarity rules at their real severities, with
# ``questions_resolved`` in place of ``open_questions_unresolved`` — a question with a recorded
# answer, or deferred to a named owner, no longer blocks. Mirrored by
# ``examples/gap_rules/requirements_gaps.yaml``.
STRICT_GAP_RULES: tuple[GapRule, ...] = (
    *(r for r in _CORE_RULES if r.id != "open_questions_unresolved"),
    *_clarity_rules(strict=True),
    GapRule(
        id="questions_resolved",
        description="Every open question needs a recorded answer, or an owner it is deferred to.",
        severity=GapSeverity.NEEDS_INPUT,
        check="question_state",
        field="open_questions",
    ),
)


def load_gap_rules(path: str | Path) -> list[GapRule]:
    """Load a rule set from YAML (``{rules: [...]}``)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [GapRule.model_validate(r) for r in (data.get("rules") or [])]


class GapAnalyzer:
    """Runs a rule set over intents and emits gap findings."""

    def __init__(self, rules: list[GapRule] | None = None) -> None:
        self._rules = list(rules) if rules is not None else list(DEFAULT_GAP_RULES)

    def analyze(self, intents: list[Intent]) -> list[GapFinding]:
        findings: list[GapFinding] = []
        for intent in intents:
            for rule in self._rules:
                if not self._passes(intent, rule):
                    message = rule.description
                    if rule.check == "question_state":
                        message += f" ({len(_unresolved(intent))} of {len(intent.open_questions)} unresolved)"
                    findings.append(
                        GapFinding(
                            rule_id=rule.id,
                            intent_id=intent.id,
                            severity=rule.severity,
                            message=message,
                        )
                    )
        return findings

    def _passes(self, intent: Intent, rule: GapRule) -> bool:
        value = getattr(intent, rule.field)
        if rule.check == "field_present":
            return bool(value.strip()) if isinstance(value, str) else bool(value)
        if rule.check == "min_length":
            return isinstance(value, str) and len(value.strip()) >= rule.length
        if rule.check == "min_items":
            return isinstance(value, list) and len(value) >= rule.count
        if rule.check == "max_items":
            return isinstance(value, list) and len(value) <= rule.count
        if rule.check == "pattern":
            return _matches(value, rule)
        if rule.check == "question_state":
            return not _unresolved(intent)
        return True  # unknown check (validated out at construction) → no finding


def _matches(value: object, rule: GapRule) -> bool:
    """A ``pattern`` check. An empty field is not judged — "is it there" is another rule's job."""
    rx = re.compile(rule.pattern, re.IGNORECASE)
    items = [str(v) for v in value] if isinstance(value, list) else [str(value or "")]
    items = [i for i in items if i.strip()]
    if not items:
        return True
    hits = [bool(rx.search(i)) for i in items]
    return all(hits) if rule.mode == "all" else any(hits)


def _unresolved(intent: Intent) -> list[str]:
    """The open questions with no usable answer — see :func:`intents.question_states`."""
    return question_states(intent)[2]


def blocks_approval(findings: list[GapFinding]) -> bool:
    """True when any finding gates the intent-approval bookend."""
    return any(f.severity.gates_approval for f in findings)
