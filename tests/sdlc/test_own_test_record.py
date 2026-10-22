"""P4b (SSPN-228): one record per answer a person gave about the tests a run wrote, and a per-cause report.

The measure of the whole mechanism is interventions per run falling; the report is what shows it. A recurring
answer is surfaced as a *candidate* rule for a person to review, never applied.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.sdlc.own_test_record import (
    InterventionRecord,
    append_outcome,
    append_record,
    load_records,
    render_report,
)


def _rec(**over: object) -> InterventionRecord:
    base: dict[str, object] = {
        "run_id": "r1",
        "approval_id": "r1-1",
        "issue_key": "SSPN-1",
        "repo": "widget",
        "cause": "import_error",
        "evidence": "No module named 'pytest'",
        "files": ("tests/test_x.py",),
        "names": ("test_a",),
        "choice": "explain",
        "note": "use unittest",
        "by": "alice",
        "waited_seconds": 42.0,
    }
    base.update(over)
    return InterventionRecord(**base)  # type: ignore[arg-type]


def test_a_record_round_trips_through_the_file(tmp_path: Path) -> None:
    path = tmp_path / "interventions.jsonl"

    append_record(path, _rec())

    [loaded] = load_records(path)
    assert loaded.run_id == "r1" and loaded.cause == "import_error" and loaded.choice == "explain"
    assert loaded.files == ("tests/test_x.py",) and loaded.note == "use unittest" and loaded.passed is None


def test_an_outcome_line_is_joined_to_its_run_when_read(tmp_path: Path) -> None:
    path = tmp_path / "i.jsonl"
    append_record(path, _rec())
    append_outcome(path, run_id="r1", passed=True, label="own_tests_repaired")

    [loaded] = load_records(path)

    assert loaded.passed is True and loaded.result == "own_tests_repaired"


def test_a_missing_file_is_no_records_and_a_damaged_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "i.jsonl"
    assert load_records(path) == []
    append_record(path, _rec())
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write(json.dumps({"kind": "record", "run_id": "r2"}) + "\n")  # missing fields

    assert [r.run_id for r in load_records(path)] == ["r1"]


def test_a_no_answer_is_a_record_too_and_is_counted_apart(tmp_path: Path) -> None:
    path = tmp_path / "i.jsonl"
    append_record(path, _rec(choice="", by="", note=""))

    [loaded] = load_records(path)

    assert loaded.answered is False
    assert "no answer" in render_report([loaded])


def test_the_report_counts_choices_per_cause_and_how_many_runs_each_resolved() -> None:
    records = [
        _rec(run_id="a", cause="import_error", choice="explain", passed=True),
        _rec(run_id="b", cause="import_error", choice="delete", passed=True),
        _rec(run_id="c", cause="wrong_expectation", choice="delete", passed=False),
    ]

    report = render_report(records)

    assert "import_error" in report and "wrong_expectation" in report
    assert "3 question(s)" in report
    row = next(ln for ln in report.splitlines() if ln.startswith("| import_error"))
    assert "explain 1" in row and "delete 1" in row and "2 of 2" in row  # passed of those with an outcome


def test_a_choice_given_at_least_three_times_for_one_cause_is_a_candidate_rule_for_review() -> None:
    records = [_rec(run_id=str(n), cause="missing_app", choice="delete") for n in range(3)]
    records.append(_rec(run_id="x", cause="import_error", choice="delete"))

    report = render_report(records)

    assert "Candidate rules" in report
    assert "missing_app" in report.split("Candidate rules", 1)[1]
    assert "import_error" not in report.split("Candidate rules", 1)[1]
    assert "review" in report.lower() and "not applied" in report.lower()


def test_two_answers_for_a_cause_that_disagree_make_no_candidate() -> None:
    records = [
        _rec(run_id=str(n), cause="missing_app", choice=c)
        for n, c in enumerate(["delete", "delete", "release"])
    ]

    assert "Candidate rules" not in render_report(records)


def test_an_empty_report_says_so() -> None:
    assert "No questions" in render_report([])


def test_notes_with_markup_cannot_break_the_report_table() -> None:
    report = render_report([_rec(note="a | b\n| c", evidence="x | y")])

    assert all(ln.count("|") >= 2 for ln in report.splitlines() if ln.startswith("|"))
    assert "a | b" not in report


def test_two_matching_answers_are_not_yet_a_candidate() -> None:
    records = [_rec(run_id=str(n), cause="missing_app", choice="delete") for n in range(2)]

    assert "Candidate rules" not in render_report(records)


def test_one_dissenting_answer_withdraws_a_candidate_however_many_agree() -> None:
    records = [_rec(run_id=str(n), cause="missing_app", choice="delete") for n in range(4)]
    records.append(_rec(run_id="z", cause="missing_app", choice="release"))

    assert "Candidate rules" not in render_report(records)
