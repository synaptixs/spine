"""The requirements-clarity gate: ``pattern`` and ``question_state`` checks and the strict set."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake.gaps import (
    _CORE_RULES,
    DEFAULT_GAP_RULES,
    STRICT_GAP_RULES,
    GapAnalyzer,
    GapRule,
    GapSeverity,
    blocks_approval,
    load_gap_rules,
)
from orchestrator.intake.intents import Intent, Resolution

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "gap_rules"
_CLEAR = {
    "id": "i",
    "title": "Export",
    "description": "Let finance export the ledger as CSV.",
    "scope": "CSV only.",
    "nfrs": ["fast"],
    "problem": "Finance cannot reconcile month-end.",
    "users": ["finance"],
    "outcome": "Month-end closes within one day.",
    "non_goals": ["no PDF"],
    "acceptance_criteria": ["Given a ledger, when export runs, then a CSV is written."],
}


def _intent(**over: object) -> Intent:
    return Intent(**{**_CLEAR, **over})  # type: ignore[arg-type]


def _fired(rules: tuple[GapRule, ...] | list[GapRule], intent: Intent) -> dict[str, GapSeverity]:
    return {f.rule_id: f.severity for f in GapAnalyzer(list(rules)).analyze([intent])}


def test_a_fully_stated_intent_passes_the_strict_set() -> None:
    assert _fired(STRICT_GAP_RULES, _intent()) == {}


@pytest.mark.parametrize(
    ("rule", "over", "severity"),
    [
        ("problem_stated", {"problem": ""}, GapSeverity.BLOCKER),
        ("problem_stated", {"problem": "   "}, GapSeverity.BLOCKER),
        ("users_named", {"users": []}, GapSeverity.NEEDS_INPUT),
        ("outcome_stated", {"outcome": ""}, GapSeverity.NEEDS_INPUT),
        ("outcome_observable", {"outcome": "Better."}, GapSeverity.WARNING),
        ("non_goal_named", {"non_goals": []}, GapSeverity.WARNING),
        ("criteria_testable", {"acceptance_criteria": ["Nice and fast."]}, GapSeverity.WARNING),
        # `all`: one vague criterion among testable ones is enough to fire.
        (
            "criteria_testable",
            {"acceptance_criteria": ["The export shall write a CSV.", "Make it pleasant."]},
            GapSeverity.WARNING,
        ),
    ],
)
def test_each_strict_rule_fires_at_its_severity(
    rule: str, over: dict[str, object], severity: GapSeverity
) -> None:
    assert _fired(STRICT_GAP_RULES, _intent(**over)).get(rule) == severity


def test_a_pattern_rule_does_not_judge_an_empty_field() -> None:
    """Whether the outcome exists is `outcome_stated`'s finding; it must not also be a vague one."""
    fired = _fired(STRICT_GAP_RULES, _intent(outcome="", acceptance_criteria=[]))
    assert "outcome_stated" in fired
    assert "outcome_observable" not in fired and "criteria_testable" not in fired


@pytest.mark.parametrize(
    "outcome",
    ["Month-end closes within one day.", "Support tickets fall by 30%.", "Users can see the balance."],
)
def test_an_observable_outcome_passes(outcome: str) -> None:
    assert "outcome_observable" not in _fired(STRICT_GAP_RULES, _intent(outcome=outcome))


def test_pattern_mode_any_needs_one_matching_item() -> None:
    rule = GapRule(
        id="r",
        description="d",
        severity=GapSeverity.WARNING,
        check="pattern",
        field="non_goals",
        pattern="^no ",
        mode="any",
    )
    assert _fired([rule], _intent(non_goals=["PDF", "no email"])) == {}
    assert _fired([rule], _intent(non_goals=["PDF"])) == {"r": GapSeverity.WARNING}


def test_a_pattern_rule_needs_a_valid_pattern() -> None:
    with pytest.raises(ValueError, match="no pattern"):
        GapRule(id="r", description="d", severity=GapSeverity.WARNING, check="pattern", field="outcome")
    with pytest.raises(ValueError, match="invalid pattern"):
        GapRule(
            id="r",
            description="d",
            severity=GapSeverity.WARNING,
            check="pattern",
            field="outcome",
            pattern="(",
        )


def test_question_state_only_checks_open_questions() -> None:
    with pytest.raises(ValueError, match="open_questions"):
        GapRule(id="r", description="d", severity=GapSeverity.WARNING, check="question_state", field="scope")


_Q = "Which currencies?"


@pytest.mark.parametrize(
    ("resolution", "resolved"),
    [
        (None, False),
        (Resolution(status="answered", answer="EUR", origin="user", channel="cli"), True),
        (Resolution(status="answered", answer="EUR", origin="stated", channel="source"), True),
        (Resolution(status="answered", answer="EUR", origin="relayed", channel="mcp"), True),
        # A model's proposal is not an answer until someone confirms it.
        (Resolution(status="proposed", answer="EUR", origin="proposed", channel="elicitation"), False),
        (Resolution(status="answered", answer="EUR", origin="proposed", channel="elicitation"), False),
        (Resolution(status="deferred", owner="finance-lead"), True),
        (Resolution(status="deferred", owner="  "), False),
    ],
)
def test_question_state(resolution: Resolution | None, resolved: bool) -> None:
    intent = _intent(open_questions=[_Q], resolutions={_Q: resolution} if resolution else {})
    fired = _fired(STRICT_GAP_RULES, intent)
    assert ("questions_resolved" not in fired) is resolved
    if not resolved:
        assert fired["questions_resolved"] == GapSeverity.NEEDS_INPUT


def test_an_answer_to_a_reworded_question_does_not_count() -> None:
    """D24: resolutions key on the question's exact text, so an orphaned answer resolves nothing."""
    intent = _intent(
        open_questions=[_Q], resolutions={"Which currency?": Resolution(status="answered", answer="EUR")}
    )
    assert "questions_resolved" in _fired(STRICT_GAP_RULES, intent)


def test_the_finding_says_how_many_questions_are_unresolved() -> None:
    intent = _intent(
        open_questions=["a?", "b?"], resolutions={"a?": Resolution(status="answered", answer="x")}
    )
    (finding,) = [
        f for f in GapAnalyzer(list(STRICT_GAP_RULES)).analyze([intent]) if f.rule_id == "questions_resolved"
    ]
    assert "1 of 2 unresolved" in finding.message


def test_the_strict_set_swaps_the_old_open_question_rule_for_the_new_one() -> None:
    ids = {r.id for r in STRICT_GAP_RULES}
    assert "questions_resolved" in ids and "open_questions_unresolved" not in ids
    assert {r.id for r in _CORE_RULES} - {"open_questions_unresolved"} <= ids


# ---- D14: ingest is never gated by a rule it did not have ----------------------------------


def test_the_default_set_gains_the_clarity_rules_as_warnings_only() -> None:
    new = [r for r in DEFAULT_GAP_RULES if r not in _CORE_RULES]
    assert {r.id for r in new} == {
        "problem_stated",
        "users_named",
        "outcome_stated",
        "outcome_observable",
        "non_goal_named",
        "criteria_testable",
    }
    assert {r.severity for r in new} == {GapSeverity.WARNING}
    assert [r.id for r in DEFAULT_GAP_RULES][: len(_CORE_RULES)] == [r.id for r in _CORE_RULES]


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"description": "short"},
        {"open_questions": ["Which currencies?"]},
        {"problem": "", "users": [], "outcome": "", "non_goals": [], "scope": ""},
    ],
)
def test_ingest_gates_exactly_as_before(over: dict[str, object]) -> None:
    intent = _intent(**over)
    before = GapAnalyzer(list(_CORE_RULES)).analyze([intent])
    after = GapAnalyzer().analyze([intent])
    assert blocks_approval(after) == blocks_approval(before)
    assert [f for f in after if f.severity.gates_approval] == [f for f in before if f.severity.gates_approval]


# ---- the shipped YAML cannot drift from the code ----------------------------------------------


@pytest.mark.parametrize(
    ("name", "built_in"),
    [("requirements_gaps.yaml", STRICT_GAP_RULES), ("intent_gaps.yaml", DEFAULT_GAP_RULES)],
)
def test_the_example_yaml_mirrors_the_built_in_set(name: str, built_in: tuple[GapRule, ...]) -> None:
    assert load_gap_rules(_EXAMPLES / name) == list(built_in)
