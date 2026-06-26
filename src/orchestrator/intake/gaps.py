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
Four check kinds cover the common cases:

  - ``field_present``  — a field is non-empty.
  - ``min_length``     — a string field is at least N chars.
  - ``min_items``      — a list field has at least N entries.
  - ``max_items``      — a list field has at most N entries.

Built-in defaults ship in code (so it works out of the box) and as a
copyable ``examples/gap_rules/intent_gaps.yaml``. The grey-zone LLM-judge
the plan mentions is a future extension; the default set is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from orchestrator.intake.intents import Intent

_LIST_FIELDS = {"acceptance_criteria", "dependencies", "nfrs", "open_questions", "source_doc_ids"}
_STR_FIELDS = {"id", "title", "description", "scope"}
_CHECK_KINDS = {"field_present", "min_length", "min_items", "max_items"}


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

    def model_post_init(self, _ctx: Any) -> None:
        if self.check not in _CHECK_KINDS:
            raise ValueError(f"unknown gap check {self.check!r}; expected one of {sorted(_CHECK_KINDS)}")
        if self.field not in (_LIST_FIELDS | _STR_FIELDS):
            raise ValueError(f"gap rule {self.id!r} targets unknown Intent field {self.field!r}")


@dataclass(frozen=True)
class GapFinding:
    rule_id: str
    intent_id: str
    severity: GapSeverity
    message: str


# Built-in defaults — work without any YAML file present.
DEFAULT_GAP_RULES: tuple[GapRule, ...] = (
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
                    findings.append(
                        GapFinding(
                            rule_id=rule.id,
                            intent_id=intent.id,
                            severity=rule.severity,
                            message=rule.description,
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
        return True  # unknown check (validated out at construction) → no finding


def blocks_approval(findings: list[GapFinding]) -> bool:
    """True when any finding gates the intent-approval bookend."""
    return any(f.severity.gates_approval for f in findings)
