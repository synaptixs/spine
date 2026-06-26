"""Block B.3 unit tests: gap rules + analyzer + approval gating."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake.gaps import (
    DEFAULT_GAP_RULES,
    GapAnalyzer,
    GapRule,
    GapSeverity,
    blocks_approval,
    load_gap_rules,
)
from orchestrator.intake.intents import Intent


def _intent(**overrides: object) -> Intent:
    base: dict[str, object] = {
        "id": "intent-x",
        "title": "Add export",
        "description": "A sufficiently long description of the capability.",
        "scope": "CSV only.",
        "dependencies": [],
        "nfrs": ["fast"],
        "open_questions": [],
    }
    base.update(overrides)
    return Intent(**base)  # type: ignore[arg-type]


# ---- rule validation ------------------------------------------------------


def test_rule_rejects_unknown_check() -> None:
    with pytest.raises(ValueError, match="unknown gap check"):
        GapRule(id="r", description="d", severity=GapSeverity.WARNING, check="bogus", field="scope")


def test_rule_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="unknown Intent field"):
        GapRule(id="r", description="d", severity=GapSeverity.WARNING, check="field_present", field="nope")


# ---- analyzer with defaults -----------------------------------------------


def test_complete_intent_has_no_findings() -> None:
    assert GapAnalyzer().analyze([_intent()]) == []


def test_missing_description_is_blocker() -> None:
    findings = GapAnalyzer().analyze([_intent(description="short")])
    blockers = [f for f in findings if f.severity is GapSeverity.BLOCKER]
    assert any(f.rule_id == "description_present" for f in blockers)
    assert blocks_approval(findings) is True


def test_open_questions_need_input() -> None:
    findings = GapAnalyzer().analyze([_intent(open_questions=["Which columns?"])])
    nq = [f for f in findings if f.rule_id == "open_questions_unresolved"]
    assert nq and nq[0].severity is GapSeverity.NEEDS_INPUT
    assert blocks_approval(findings) is True  # needs_input gates approval


def test_missing_nfrs_and_scope_are_warnings_only() -> None:
    findings = GapAnalyzer().analyze([_intent(nfrs=[], scope="")])
    rules = {f.rule_id: f.severity for f in findings}
    assert rules["nfrs_missing"] is GapSeverity.WARNING
    assert rules["scope_declared"] is GapSeverity.WARNING
    # warnings alone do not gate approval
    assert blocks_approval(findings) is False


def test_analyzer_runs_all_intents() -> None:
    findings = GapAnalyzer().analyze([_intent(description="x"), _intent(open_questions=["q"])])
    intent_ids = {f.intent_id for f in findings}
    assert intent_ids == {"intent-x"}  # both share id in this fixture
    assert len(findings) >= 2


# ---- YAML loading ---------------------------------------------------------


def test_load_default_yaml_matches_builtin(tmp_path: Path) -> None:
    rules_path = Path(__file__).resolve().parents[2] / "examples" / "gap_rules" / "intent_gaps.yaml"
    loaded = load_gap_rules(rules_path)
    assert {r.id for r in loaded} == {r.id for r in DEFAULT_GAP_RULES}


def test_custom_rules_override_defaults() -> None:
    # A stricter rule set: require >=2 NFRs as a blocker.
    rules = [
        GapRule(
            id="two_nfrs",
            description="Need at least two NFRs.",
            severity=GapSeverity.BLOCKER,
            check="min_items",
            field="nfrs",
            count=2,
        )
    ]
    findings = GapAnalyzer(rules).analyze([_intent(nfrs=["only one"])])
    assert findings and findings[0].rule_id == "two_nfrs"
    assert blocks_approval(findings) is True
