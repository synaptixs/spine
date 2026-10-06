"""The requirements check, with no model anywhere: skeleton -> answers -> gate, and the surgical
edits that record an answer into a change a person may have written."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake import requirements as rq
from orchestrator.intake.intents import Intent, Resolution
from orchestrator.intake.openspec_writer import render_change, write_change
from orchestrator.intake.pkg_evidence import DraftGrounding, ungrounded
from orchestrator.intake.specs import FeatureSpec
from orchestrator.sdlc.spec_file import validate_spec

_IDEA = "Let finance export invoices as CSV"
_PROBLEM_Q, _USERS_Q, _OUTCOME_Q, _NONGOAL_Q = list(rq.FIELD_QUESTIONS)
_ALL_ANSWERS = [
    rq.AnswerRequest(_PROBLEM_Q, "Finance cannot reconcile invoices at month end."),
    rq.AnswerRequest(_USERS_Q, "Finance operations; Audit"),
    rq.AnswerRequest(_OUTCOME_Q, "Month-end reconciliation completes within 1 day."),
    rq.AnswerRequest(_NONGOAL_Q, "PDF export\nEmail delivery"),
]


def _draft(tmp_path: Path, idea: str = _IDEA) -> rq.LoadedChange:
    intent = rq.skeleton(idea)
    spec = FeatureSpec(intent_id=intent.id, title=intent.title, summary=intent.description)
    write_change(tmp_path, intent, render_change(spec, intent))
    return rq.load_change(intent.id.removeprefix("intent-"), root=tmp_path)


# ---- the skeleton -----------------------------------------------------------------------------


def test_the_skeleton_asks_the_four_fixed_questions_and_nothing_else() -> None:
    intent = rq.skeleton(_IDEA)
    assert intent.open_questions == list(rq.FIELD_QUESTIONS) and len(intent.open_questions) == 4
    assert intent.description == _IDEA and intent.idea_id == "idea-let-finance-export-invoices-as-csv"
    assert (intent.problem, intent.users, intent.outcome, intent.non_goals) == ("", [], "", [])


def test_the_same_sentence_always_gives_the_same_skeleton() -> None:
    assert rq.skeleton("  Let   finance export invoices as CSV ") == rq.skeleton(_IDEA)


def test_an_empty_idea_is_refused() -> None:
    with pytest.raises(rq.RequirementsError):
        rq.skeleton("   ")


def test_a_skeleton_round_trips_through_its_files(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    assert change.intent.open_questions == list(rq.FIELD_QUESTIONS)
    assert change.intent.idea_id == "idea-let-finance-export-invoices-as-csv"
    assert "Skeleton drafted by Spine" in change.proposal_md


# ---- the check --------------------------------------------------------------------------------


def test_a_fresh_skeleton_does_not_pass_and_orders_blockers_first(tmp_path: Path) -> None:
    report = rq.check_intent(_draft(tmp_path).intent)
    assert not report.passes
    severities = [f.severity.value for f in report.findings]
    rank = {"blocker": 0, "needs_input": 1, "warning": 2}
    assert [rank[x] for x in severities] == sorted(rank[x] for x in severities)
    assert report.findings[0].rule_id == "problem_stated"
    assert len(report.unresolved) == 4


def test_the_skeletons_placeholder_criterion_is_called_out_not_counted(tmp_path: Path) -> None:
    report = rq.check_intent(_draft(tmp_path).intent)
    assert any("placeholder scenario" in n for n in report.notes)


def test_the_check_is_deterministic(tmp_path: Path) -> None:
    intent = _draft(tmp_path).intent
    assert json.dumps(rq.check_intent(intent).to_dict()) == json.dumps(rq.check_intent(intent).to_dict())


def test_an_answer_to_a_reworded_question_is_reported_as_orphaned() -> None:
    intent = Intent(
        id="i",
        title="T",
        open_questions=["Which currency?"],
        resolutions={"Which currencies?": Resolution(status="answered", answer="EUR")},
    )
    assert rq.check_intent(intent).orphaned == ("Which currencies?",)


def test_a_deferral_passes_the_question_gate_but_the_check_says_it_supplied_nothing(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, defer_to="@pm")], at="2026-10-06")
    report = rq.check_intent(rq.load_change(change.change_id, root=tmp_path).intent)
    assert "questions_resolved" in {f.rule_id for f in report.findings}  # three still open
    assert "problem_stated" in {f.rule_id for f in report.findings}  # a deferral does not state the problem
    assert any("does not supply the missing why" in n for n in report.notes)


def test_the_code_check_says_ungrounded_rather_than_nothing_found() -> None:
    result = rq.code_check(ungrounded())
    assert result.state == "ungrounded" and result.already_exist == () and result.names_not_found == ()


def test_the_code_check_reports_an_empty_graph_as_such() -> None:
    assert rq.code_check(DraftGrounding(state="empty", where="repo")).state == "empty"


# ---- recording answers ------------------------------------------------------------------------


def test_answering_every_fixed_question_fills_the_why_fields_and_passes_the_gate(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    rq.record_answers(change, _ALL_ANSWERS, at="2026-10-06")
    after = rq.load_change(change.change_id, root=tmp_path).intent
    assert after.problem == "Finance cannot reconcile invoices at month end."
    assert after.users == ["Finance operations", "Audit"]
    assert after.outcome == "Month-end reconciliation completes within 1 day."
    assert after.non_goals == ["PDF export", "Email delivery"]
    assert after.description == _IDEA  # the lead text is untouched
    report = rq.check_intent(after)
    assert report.passes and report.unresolved == ()


def test_a_recorded_answer_says_the_channel_spine_observed(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    rq.record_answers(change, [_ALL_ANSWERS[0]], channel="cli", at="2026-10-06")
    rq.record_answers(
        rq.load_change(change.change_id, root=tmp_path),
        [_ALL_ANSWERS[1]],
        channel="answers-file",
        at="2026-10-06",
    )
    rq.record_answers(
        rq.load_change(change.change_id, root=tmp_path), [_ALL_ANSWERS[2]], channel="mcp", at="2026-10-06"
    )
    res = rq.load_change(change.change_id, root=tmp_path).intent.resolutions
    assert (res[_PROBLEM_Q].origin, res[_PROBLEM_Q].channel) == ("user", "cli")
    assert (res[_USERS_Q].origin, res[_USERS_Q].channel) == ("user", "answers-file")
    # Over MCP Spine cannot see who typed it, so it never claims a person.
    assert (res[_OUTCOME_Q].origin, res[_OUTCOME_Q].channel) == ("relayed", "mcp")


def test_answering_twice_replaces_the_first_answer(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, "first")], at="2026-10-06")
    rq.record_answers(
        rq.load_change(change.change_id, root=tmp_path),
        [rq.AnswerRequest(_PROBLEM_Q, "second")],
        at="2026-10-07",
    )
    md = rq.load_change(change.change_id, root=tmp_path).proposal_md
    assert md.count("**Answer**") == 1 and "second" in md and "first" not in md
    assert md.count("### Problem") == 1


def test_a_batch_with_one_unknown_question_writes_nothing(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    before = change.proposal_path.read_text()
    with pytest.raises(rq.RequirementsError, match="no open question matches"):
        rq.record_answers(change, [_ALL_ANSWERS[0], rq.AnswerRequest("Which colour?", "blue")])
    assert change.proposal_path.read_text() == before


def test_an_error_lists_the_questions_that_are_open(tmp_path: Path) -> None:
    with pytest.raises(rq.RequirementsError) as exc:
        rq.record_answers(_draft(tmp_path), [rq.AnswerRequest("nope", "x")])
    assert _PROBLEM_Q in str(exc.value)


def test_a_question_matches_ignoring_case_and_spacing(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    rq.record_answers(change, [rq.AnswerRequest("what  problem does THIS solve?", "x")], at="2026-10-06")
    assert rq.load_change(change.change_id, root=tmp_path).intent.problem == "x"


@pytest.mark.parametrize(("answer", "defer"), [("", ""), ("a", "someone")])
def test_exactly_one_of_an_answer_or_a_deferral(tmp_path: Path, answer: str, defer: str) -> None:
    with pytest.raises(rq.RequirementsError, match="exactly one"):
        rq.record_answers(_draft(tmp_path), [rq.AnswerRequest(_PROBLEM_Q, answer, defer)])


def test_recording_an_answer_leaves_a_hand_written_change_alone(tmp_path: Path) -> None:
    """The edit is surgical: a person's prose, Impact and Grounding keep every byte."""
    change_dir = tmp_path / "changes" / "hand"
    (change_dir / "specs" / "cap").mkdir(parents=True)
    (change_dir / "specs" / "cap" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Export\nThe system SHALL export.\n\n"
        "#### Scenario: ok\n- WHEN run\n- THEN a CSV exists\n"
    )
    original = (
        "# Proposal: Hand written\n\nSome intro the author likes.\n\n"
        "## Why\nWe need it.\n\n"
        "## What Changes\nExport things. **Bold** stays.\n\n"
        "## Impact\nTouches `export.py`.\n\n"
        "## Open Questions\n- Which currencies?\n- Include voided?\n\n"
        "## Grounding\nLeft exactly as it is.\n"
    )
    (change_dir / "proposal.md").write_text(original)
    loaded = rq.load_change("hand", root=tmp_path)
    rq.record_answers(loaded, [rq.AnswerRequest("Which currencies?", "EUR")], at="2026-10-06")
    after = (change_dir / "proposal.md").read_text()
    expected = original.replace(
        "- Which currencies?\n", "- Which currencies?\n  - **Answer** (user · cli · 2026-10-06): EUR\n"
    )
    assert after == expected


def test_a_change_with_no_open_questions_section_is_refused(tmp_path: Path) -> None:
    change_dir = tmp_path / "changes" / "bare"
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text("# P\n\n## Why\nw\n")
    loaded = rq.load_change("bare", root=tmp_path)
    loaded_intent = Intent(id="i", title="t", open_questions=["Q?"])
    assert loaded.intent.open_questions == []
    with pytest.raises(rq.RequirementsError, match="no open question matches"):
        rq.record_answers(loaded, [rq.AnswerRequest("Q?", "a")])
    assert loaded_intent  # silence "unused"


def test_a_missing_change_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(rq.RequirementsError, match="no change"):
        rq.load_change("ghost", root=tmp_path)


def test_a_change_can_be_loaded_by_directory_path(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    assert rq.load_change(str(change.directory)).change_id == change.change_id


# ---- the answers file -------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "answers.yaml"
    p.write_text(text)
    return p


def test_an_answers_file_is_read(tmp_path: Path) -> None:
    reqs = rq.load_answers_file(
        _write(
            tmp_path, "answers:\n  - question: Q1?\n    answer: A1\n  - question: Q2?\n    defer_to: '@bob'\n"
        )
    )
    assert reqs == [rq.AnswerRequest("Q1?", "A1"), rq.AnswerRequest("Q2?", defer_to="@bob")]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "answers: []\n",
        "- question: x\n",
        "answers:\n  - answer: no question\n",
        "answers:\n  - question: q\n    ansswer: typo\n",
    ],
)
def test_a_malformed_answers_file_is_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(rq.RequirementsError):
        rq.load_answers_file(_write(tmp_path, text))


# ---- end to end, no model ---------------------------------------------------------------------


def test_idea_to_skeleton_to_answers_to_a_gate_that_passes_to_a_spec_the_plan_accepts(tmp_path: Path) -> None:
    change = _draft(tmp_path)
    assert not rq.check_intent(change.intent).passes
    answers = tmp_path / "a.yaml"
    answers.write_text(
        "answers:\n"
        f"  - question: {_PROBLEM_Q}\n    answer: Finance cannot reconcile invoices.\n"
        f"  - question: {_USERS_Q}\n    answer: Finance operations\n"
        f"  - question: {_OUTCOME_Q}\n    answer: Month-end closes within 1 day.\n"
        f"  - question: {_NONGOAL_Q}\n    defer_to: '@finance-lead'\n"
    )
    rq.record_answers(change, rq.load_answers_file(answers), channel="answers-file", at="2026-10-06")
    intent = rq.load_change(change.change_id, root=tmp_path).intent
    assert rq.check_intent(intent).passes
    # `sdlc plan --spec` takes a spec file; the why the gate checked is what the spec carries.
    spec: dict[str, Any] = FeatureSpec(
        intent_id=intent.id,
        title=intent.title,
        description=intent.description,
        problem=intent.problem,
        users=intent.users,
        outcome=intent.outcome,
        non_goals=intent.non_goals,
        acceptance_criteria=intent.acceptance_criteria,
    ).model_dump()
    assert validate_spec(spec)["problem"] == "Finance cannot reconcile invoices."


def test_a_change_written_elsewhere_checks_the_same_way(tmp_path: Path) -> None:
    """No `idea:`, no skeleton — a hand-written proposal with the layout of §3.3."""
    change_dir = tmp_path / "changes" / "elsewhere"
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        "# Proposal: Elsewhere\n\n## Why\n### Problem\nFinance cannot reconcile.\n### Users\n- U\n"
        "### Outcome\nCloses within 1 day.\n\n"
        "## What Changes\nDo it.\n### Non-goals\n- N\n\n"
        "## Open Questions\n- Q?\n  - **Answer** (user · cli · 2026-10-06): yes\n"
    )
    report = rq.check_intent(rq.load_change("elsewhere", root=tmp_path).intent)
    assert report.passes and report.answered == ("Q?",)
