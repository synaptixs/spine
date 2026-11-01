"""SSPN-126: a change ``openspec draft --source`` wrote, or one a person wrote, gets its why
through ``openspec answer`` — the four fixed why-questions are accepted even when not open — and
``check`` names the command that fixes each failing why-rule. Every placement is checked by
reading the file back through ``load_change``, the reader, never by looking at the editor alone."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake import requirements as rq
from orchestrator.intake.gaps import GapRule, GapSeverity

_PROBLEM_Q, _USERS_Q, _OUTCOME_Q, _NONGOAL_Q = list(rq.FIELD_QUESTIONS)
_WHY_ANSWERS = [
    rq.AnswerRequest(_PROBLEM_Q, "Finance cannot close on time."),
    rq.AnswerRequest(_USERS_Q, "Finance operations"),
    rq.AnswerRequest(_OUTCOME_Q, "Month-end closes within 1 day."),
    rq.AnswerRequest(_NONGOAL_Q, "PDF export"),
]
_WHY_RULES = {"problem_stated", "users_named", "outcome_stated", "non_goal_named"}

# Run 3: a hand-written legacy change — a bare `## Why`, one question of its own.
_LEGACY = (
    "# Proposal: Slow export\n\n"
    "## Why\nThe export is slow.\n\n"
    "## What Changes\nMake it faster.\n\n"
    "## Open Questions\n- Is the 40 minute figure measured or estimated?\n"
)
# Run 2: what `draft --source` writes when the source raised no questions — a prose `## Why`,
# no Open Questions section, and the Grounding section last.
_DRAFTED = (
    "# Proposal: Export invoices\n\n"
    "## Why\nFinance needs invoices as CSV for the month-end close.\n\n"
    "## What Changes\nAn export command.\n\n"
    "## Grounding\nungrounded — no repository was given.\n"
)


def _change(tmp_path: Path, proposal: str, name: str = "c", *, newline: str | None = None) -> rq.LoadedChange:
    directory = tmp_path / "changes" / name
    directory.mkdir(parents=True)
    with (directory / "proposal.md").open("w", encoding="utf-8", newline=newline or "") as handle:
        handle.write(proposal)
    return rq.load_change(name, root=tmp_path)


def _reread(change: rq.LoadedChange) -> rq.LoadedChange:
    return rq.load_change(str(change.directory))


def _rule_ids(change: rq.LoadedChange) -> set[str]:
    return {f.rule_id for f in rq.check_intent(_reread(change).intent).findings}


# ---- P1: a fixed why-question that is not open is ensured, then answered -----------------------


def test_the_run_3_repro_now_records_the_problem(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY)
    assert "problem_stated" in _rule_ids(change)
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.problem == "Finance cannot close on time."
    assert after.intent.open_questions == ["Is the 40 minute figure measured or estimated?", _PROBLEM_Q]
    assert after.intent.resolutions[_PROBLEM_Q].answer == "Finance cannot close on time."
    assert "problem_stated" not in _rule_ids(change)


def test_the_question_is_appended_after_the_last_one_and_nothing_else_moves(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY)
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    md = change.proposal_path.read_text()
    assert md.endswith(
        "## Open Questions\n- Is the 40 minute figure measured or estimated?\n"
        f"- {_PROBLEM_Q}\n  - **Answer** (user · cli · 2026-11-01): Finance cannot close on time.\n"
    )
    assert "## Why\nThe export is slow.\n" in md  # the lead text stays; D3: never promoted


def test_a_drafted_change_with_no_questions_section_gets_one_before_grounding(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    md = change.proposal_path.read_text()
    questions, grounding = md.index("## Open Questions"), md.index("## Grounding")
    assert questions < grounding
    assert md.endswith("## Grounding\nungrounded — no repository was given.\n")  # byte-for-byte
    assert "\n\n## Open Questions\n" in md and "\n\n## Grounding\n" in md
    assert _reread(change).intent.problem == "Finance cannot close on time."


def test_a_change_with_neither_questions_nor_grounding_gets_them_at_the_end(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n")
    rq.record_answers(change, [_WHY_ANSWERS[2]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.outcome == "Month-end closes within 1 day."
    assert after.intent.open_questions == [_OUTCOME_Q]
    assert after.proposal_md.count("## Open Questions") == 1


def test_a_questions_section_is_used_when_that_is_what_the_reader_reads(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Questions\n- Which currencies?\n")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.open_questions == ["Which currencies?", _PROBLEM_Q]
    assert "## Open Questions" not in after.proposal_md


def test_an_empty_open_questions_heading_is_filled_not_duplicated(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n\n## Grounding\ng\n")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.proposal_md.count("## Open Questions") == 1
    assert after.intent.open_questions == [_PROBLEM_Q] and after.intent.problem
    assert after.proposal_md.endswith("## Grounding\ng\n")


def test_an_empty_open_questions_heading_is_filled_ahead_of_a_questions_section(tmp_path: Path) -> None:
    """Both empty: the reader reads `open questions` first once it has a line, so that one is filled."""
    change = _change(tmp_path, "# P\n\n## Questions\n\n## Open Questions\n")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.open_questions == [_PROBLEM_Q]
    assert after.proposal_md.count("## Open Questions") == 1


def test_all_four_why_questions_take_a_drafted_change_to_a_passing_gate(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    assert not rq.check_intent(change.intent).passes
    rq.record_answers(change, _WHY_ANSWERS, at="2026-11-01")
    after = _reread(change)
    assert (after.intent.problem, after.intent.users, after.intent.non_goals) == (
        "Finance cannot close on time.",
        ["Finance operations"],
        ["PDF export"],
    )
    assert after.intent.open_questions == list(rq.FIELD_QUESTIONS)
    assert rq.check_intent(after.intent).passes


def test_a_not_open_why_question_matches_ignoring_case_and_spacing(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY)
    rq.record_answers(change, [rq.AnswerRequest("what  problem does THIS solve?", "x")], at="2026-11-01")
    after = _reread(change)
    assert after.intent.problem == "x"
    assert _PROBLEM_Q in after.intent.open_questions  # appended in the fixed wording


def test_an_open_why_question_is_answered_in_place_not_appended_again(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY.replace("estimated?\n", f"estimated?\n- {_PROBLEM_Q}\n"))
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    assert change.proposal_path.read_text().count(_PROBLEM_Q) == 1


def test_answering_a_why_question_twice_in_one_batch_appends_it_once(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY)
    rq.record_answers(
        change,
        [rq.AnswerRequest(_PROBLEM_Q, "first"), rq.AnswerRequest(_PROBLEM_Q, "second")],
        at="2026-11-01",
    )
    after = _reread(change)
    assert after.proposal_md.count(_PROBLEM_Q) == 1 and after.intent.problem == "second"


def test_a_deferred_why_question_is_appended_and_deferred(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, [rq.AnswerRequest(_NONGOAL_Q, defer_to="@pm")], at="2026-11-01")
    after = _reread(change)
    assert after.intent.resolutions[_NONGOAL_Q].status == "deferred"
    assert after.intent.non_goals == []


def test_any_other_unknown_question_still_errors_and_names_the_four(tmp_path: Path) -> None:
    change = _change(tmp_path, _LEGACY)
    with pytest.raises(rq.RequirementsError, match="no open question matches") as exc:
        rq.record_answers(change, [rq.AnswerRequest("Which colour?", "blue")])
    assert "always accepted" in str(exc.value) and _PROBLEM_Q in str(exc.value)


def test_a_batch_with_a_why_question_and_an_unknown_one_writes_nothing(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError):
        rq.record_answers(change, [_WHY_ANSWERS[0], rq.AnswerRequest("Which colour?", "blue")])
    assert change.proposal_path.read_bytes() == before


def test_a_heading_answer_to_a_not_open_why_question_writes_nothing(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError, match="heading"):
        rq.record_answers(change, [rq.AnswerRequest(_NONGOAL_Q, "# not a goal")])
    assert change.proposal_path.read_bytes() == before


def test_crlf_is_kept_when_a_question_is_appended(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED.replace("\n", "\r\n"))
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    raw = change.proposal_path.read_bytes()
    assert b"\r\n## Open Questions\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert _reread(change).intent.problem == "Finance cannot close on time."


def test_no_trailing_newline_is_kept_when_a_section_is_added_at_the_end(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    md = change.proposal_path.read_text()
    assert not md.endswith("\n")
    assert _reread(change).intent.problem == "Finance cannot close on time."


def test_the_files_mode_is_kept_when_a_question_is_appended(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    os.chmod(change.proposal_path, 0o640)  # not mkstemp's 0o600, so a lost mode shows
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    assert change.proposal_path.stat().st_mode & 0o777 == 0o640


# ---- P2: the pointer from a failing why-rule to the command that fixes it -----------------------


def _items(tmp_path: Path, proposal: str = _DRAFTED, **kwargs: Any) -> dict[str, dict[str, object]]:
    change = _change(tmp_path, proposal)
    report = rq.check_intent(change.intent, change_id=change.change_id, **kwargs)
    return {item["rule"]: item for item in report.to_dict()["open_items"]}


def test_each_failing_why_rule_carries_a_fix_naming_its_question(tmp_path: Path) -> None:
    items = _items(tmp_path, change_ref="c", root=str(tmp_path))
    assert set(items) >= _WHY_RULES
    for rule, question in zip(
        ("problem_stated", "users_named", "outcome_stated", "non_goal_named"), rq.FIELD_QUESTIONS, strict=True
    ):
        fix = items[rule]["fix"]
        assert isinstance(fix, dict)
        assert fix["question"] == question
        assert fix["mcp"] == {"tool": "requirements_answer", "question": question}


def test_the_cli_line_is_runnable_as_printed(tmp_path: Path) -> None:
    fix = _items(tmp_path, change_ref="c", root=str(tmp_path))["problem_stated"]["fix"]
    assert isinstance(fix, dict)
    argv = shlex.split(str(fix["cli"]))
    assert argv[:4] == ["orchestrator", "openspec", "answer", "c"]
    assert argv[argv.index("--root") + 1] == str(tmp_path)
    assert argv[argv.index("--question") + 1] == _PROBLEM_Q
    assert "--answer" in argv


def test_the_default_root_is_not_spelled_out(tmp_path: Path) -> None:
    fix = _items(tmp_path, change_ref="c", root="openspec")["problem_stated"]["fix"]
    assert isinstance(fix, dict) and "--root" not in shlex.split(str(fix["cli"]))


def test_without_a_change_reference_the_cli_line_uses_the_change_id(tmp_path: Path) -> None:
    fix = _items(tmp_path)["problem_stated"]["fix"]
    assert isinstance(fix, dict) and shlex.split(str(fix["cli"]))[3] == "c"


def test_other_rules_carry_no_fix(tmp_path: Path) -> None:
    items = _items(tmp_path, _LEGACY)
    assert "questions_resolved" in items
    assert all("fix" not in item for rule, item in items.items() if rule not in _WHY_RULES)


def test_a_custom_rule_set_without_the_why_rules_gets_no_fix(tmp_path: Path) -> None:
    rule = GapRule(
        id="problem_named_differently",
        description="d",
        severity=GapSeverity.BLOCKER,
        check="field_present",
        field="problem",
    )
    items = _items(tmp_path, rules=(rule,))
    assert set(items) == {"problem_named_differently"} and "fix" not in items["problem_named_differently"]


def test_a_why_that_came_by_relay_is_named_in_the_notes(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, [_WHY_ANSWERS[0]], channel="mcp", at="2026-11-01")
    rq.record_answers(_reread(change), [_WHY_ANSWERS[1]], channel="cli", at="2026-11-01")
    notes = rq.check_intent(_reread(change).intent).notes
    relayed = [n for n in notes if "relay" in n]
    assert len(relayed) == 1 and "problem" in relayed[0] and "users" not in relayed[0]


def test_no_relay_note_when_every_why_was_given_on_the_command_line(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, _WHY_ANSWERS, channel="cli", at="2026-11-01")
    assert not [n for n in rq.check_intent(_reread(change).intent).notes if "relay" in n]


# ---- review round 1 (2026-11-01) --------------------------------------------------------------


@pytest.mark.parametrize("heading", ["Intent", "Purpose"])
def test_a_change_described_outside_why_keeps_its_description(tmp_path: Path, heading: str) -> None:
    """B1: answering creates `## Why`, which the reader reads first; the description it had must
    survive, not be replaced by the problem."""
    change = _change(tmp_path, f"# T\n\n## {heading}\nLet finance export ledgers as CSV.\n\n## Impact\nx\n")
    assert change.intent.description == "Let finance export ledgers as CSV."
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change).intent
    assert after.description == "Let finance export ledgers as CSV."
    assert after.problem == "Finance cannot close on time."


def test_a_change_described_only_by_what_changes_keeps_that_text_as_its_scope(tmp_path: Path) -> None:
    """The writer's own layout — `## Why` holding only subsections — reads back with the problem as
    the description, so a What Changes text is not promoted to it; it stays the scope."""
    change = _change(tmp_path, "# T\n\n## What Changes\nAn export command.\n")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change).intent
    assert after.problem == "Finance cannot close on time." and "An export command." in after.scope


@pytest.mark.parametrize("placeholder", ["<your answer>", "  <your answer> ", "<the problem>"])
def test_the_fix_lines_placeholder_is_refused_and_writes_nothing(tmp_path: Path, placeholder: str) -> None:
    """B2: the printed line runs, but its placeholder must never pass the gate as a person's answer."""
    change = _change(tmp_path, _DRAFTED)
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError, match="placeholder"):
        rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, placeholder)])
    assert change.proposal_path.read_bytes() == before


def test_an_answer_in_angle_brackets_inside_a_sentence_is_fine(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, "The <export> tag is slow.")], at="2026-11-01")
    assert _reread(change).intent.problem == "The <export> tag is slow."


def test_an_open_why_question_in_other_case_fills_its_field(tmp_path: Path) -> None:
    """B3: a fixed question the file spells in another case is still that question (D8)."""
    change = _change(
        tmp_path, "# T\n\n## Why\nBecause.\n\n## Open Questions\n- what  problem does this solve?\n"
    )
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.problem == "Finance cannot close on time."
    assert after.proposal_md.lower().count("problem does this solve") == 1
    assert "problem_stated" not in _rule_ids(change)


def test_a_relayed_why_in_other_case_is_still_named(tmp_path: Path) -> None:
    change = _change(
        tmp_path, "# T\n\n## Why\nBecause.\n\n## Open Questions\n- WHAT PROBLEM DOES THIS SOLVE?\n"
    )
    rq.record_answers(change, [_WHY_ANSWERS[0]], channel="mcp", at="2026-11-01")
    assert any("came by relay" in n for n in rq.check_intent(_reread(change).intent).notes)


def test_the_relay_note_never_names_a_field_that_is_now_empty(tmp_path: Path) -> None:
    """S1: answered over MCP, then the field was deleted by hand — the note must not say it counts."""
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, [_WHY_ANSWERS[0]], channel="mcp", at="2026-11-01")
    md = change.proposal_path.read_text().replace("### Problem\nFinance cannot close on time.\n", "")
    change.proposal_path.write_text(md)
    report = rq.check_intent(_reread(change).intent)
    assert "problem_stated" in {f.rule_id for f in report.findings}
    assert not [n for n in report.notes if "relay" in n]


def test_the_relay_note_joins_two_fields_with_and(tmp_path: Path) -> None:
    change = _change(tmp_path, _DRAFTED)
    rq.record_answers(change, _WHY_ANSWERS[:2], channel="mcp", at="2026-11-01")
    (note,) = [n for n in rq.check_intent(_reread(change).intent).notes if "relay" in n]
    assert note.startswith("The problem and users came by relay")


def test_a_duplicate_why_section_gets_the_answer_where_the_reader_reads(tmp_path: Path) -> None:
    """S5: the reader keeps the last `## Why`; the editor must write into that one."""
    change = _change(tmp_path, "# T\n\n## Why\nFirst.\n\n## Impact\nx\n\n## Why\nSecond.\n")
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    assert _reread(change).intent.problem == "Finance cannot close on time."
    assert "problem_stated" not in _rule_ids(change)


def test_two_empty_open_questions_headings_fill_the_one_the_reader_keeps(tmp_path: Path) -> None:
    """S6: the reader keeps the last section of a name, so the last empty one is filled."""
    change = _change(
        tmp_path, "# T\n\n## Open Questions\n\n## Why\nw\n\n## Open Questions\n\n## Grounding\ng\n"
    )
    rq.record_answers(change, [_WHY_ANSWERS[0]], at="2026-11-01")
    after = _reread(change)
    assert after.intent.resolutions[_PROBLEM_Q].answer == "Finance cannot close on time."
    md = after.proposal_md
    assert md.index(f"- {_PROBLEM_Q}") > md.index("## Why")


@pytest.mark.parametrize("answer", ["```TypeError```", "~~~ fenced", "x\n```\ny"])
def test_a_why_answer_cannot_open_a_code_fence(tmp_path: Path, answer: str) -> None:
    change = _change(tmp_path, _DRAFTED)
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError, match="fence"):
        rq.record_answers(change, [rq.AnswerRequest(_NONGOAL_Q, answer)])
    assert change.proposal_path.read_bytes() == before


def test_an_ambiguous_match_is_an_error_not_a_third_copy(tmp_path: Path) -> None:
    change = _change(tmp_path, f"# T\n\n## Open Questions\n- {_PROBLEM_Q}\n- {_PROBLEM_Q.upper()}\n")
    with pytest.raises(rq.RequirementsError, match="no open question matches"):
        rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q.lower(), "x")])
