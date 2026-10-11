"""The books: every instance in exactly one bucket, and no headline from an incomplete ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.evals.swebench import ledger as lg
from orchestrator.evals.swebench.outcome import BUCKETS


def _row(instance_id: str, bucket: str = "submitted", **kw: Any) -> lg.LedgerRow:
    base: dict[str, Any] = {
        "instance_id": instance_id,
        "repo": "psf/requests",
        "bucket": bucket,
        "reason": "passed",
        "verdict": "",
        "spent_usd": 1.5,
        "seconds": 120.0,
        "run_id": "r",
        "image_digest": "sha256:" + "a" * 64,
        "spine_sha": "56335503",
        "model": "claude-opus-5",
        "patch_sha256": "f" * 64,
        "patch_files": ["a.py"],
        "dropped": [],
    }
    base.update(kw)
    return lg.LedgerRow(**base)


def test_rows_round_trip_through_the_file(tmp_path: Path) -> None:
    book = lg.Ledger(tmp_path / "ledger.jsonl")
    book.append(_row("a__a-1"))
    book.append(_row("b__b-2", "parked", reason="UNLOCALIZED"))

    again = lg.Ledger(tmp_path / "ledger.jsonl").rows()

    assert [r.instance_id for r in again] == ["a__a-1", "b__b-2"]
    assert again[1].bucket == "parked" and again[1].reason == "UNLOCALIZED"


def test_a_retry_replaces_the_earlier_row_but_the_history_stays_in_the_file(tmp_path: Path) -> None:
    book = lg.Ledger(tmp_path / "ledger.jsonl")
    book.append(_row("a__a-1", "generator-error", reason="docker"))
    book.append(_row("a__a-1", "submitted"))

    assert [r.bucket for r in book.rows()] == ["submitted"]
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 2


def test_an_unknown_bucket_is_refused_at_the_door(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bucket"):
        lg.Ledger(tmp_path / "l.jsonl").append(_row("a__a-1", "mystery"))


# --- predictions --------------------------------------------------------------------------


def test_predictions_are_only_for_submitted_instances_in_the_graders_format(tmp_path: Path) -> None:
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "a__a-1.diff").write_text("diff --git a/x b/x\n", encoding="utf-8")
    rows = [_row("a__a-1"), _row("b__b-2", "parked"), _row("c__c-3", "generator-error")]

    preds = lg.predictions(rows, patches, model_name="claude-opus-5")

    assert preds == [
        {
            "instance_id": "a__a-1",
            "model_name_or_path": "claude-opus-5",
            "model_patch": "diff --git a/x b/x\n",
        }
    ]


def test_a_submitted_row_without_its_patch_file_is_an_error_not_an_empty_prediction(tmp_path: Path) -> None:
    (tmp_path / "patches").mkdir()
    with pytest.raises(FileNotFoundError):
        lg.predictions([_row("a__a-1")], tmp_path / "patches", model_name="m")


# --- grading ------------------------------------------------------------------------------

REPORT = {
    "resolved_ids": ["a__a-1"],
    "unresolved_ids": ["b__b-2"],
    "ambiguous_failure_ids": ["e__e-5"],
    "error_ids": ["c__c-3"],
    "infra_failure_ids": ["d__d-4"],
    "incomplete_ids": [],
    "empty_patch_ids": [],
}


def test_the_graders_report_decides_the_submitted_instances() -> None:
    rows = [_row(i) for i in ("a__a-1", "b__b-2", "c__c-3", "d__d-4", "e__e-5")]

    final = {r.instance_id: r.bucket for r in lg.apply_grading(rows, REPORT)}

    assert final == {
        "a__a-1": "resolved",
        "b__b-2": "unresolved",
        "c__c-3": "grader-error",
        "d__d-4": "grader-error",
        "e__e-5": "unresolved",  # ambiguous: counted in unresolved, flagged in the reason
    }


def test_a_submitted_instance_the_report_never_mentions_is_a_grader_error() -> None:
    final = lg.apply_grading([_row("z__z-9")], REPORT)
    assert final[0].bucket == "grader-error" and "missing from the report" in final[0].reason


def test_rows_not_submitted_keep_their_generation_bucket() -> None:
    final = lg.apply_grading([_row("p__p-1", "parked"), _row("g__g-1", "generator-error")], REPORT)
    assert [r.bucket for r in final] == ["parked", "generator-error"]


def test_applying_grading_does_not_mutate_the_input() -> None:
    rows = [_row("a__a-1")]
    lg.apply_grading(rows, REPORT)
    assert rows[0].bucket == "submitted"


# --- the headline -------------------------------------------------------------------------


def test_the_summary_counts_every_bucket_and_the_whole_denominator() -> None:
    final = lg.apply_grading([_row("a__a-1"), _row("b__b-2"), _row("p__p-1", "parked")], REPORT)

    s = lg.summary(final, expected=["a__a-1", "b__b-2", "p__p-1"])

    assert s["counts"] == {b: 0 for b in BUCKETS} | {"resolved": 1, "unresolved": 1, "parked": 1}
    assert s["total"] == 3 and s["resolved_pct"] == pytest.approx(100 / 3)
    assert s["spent_usd"] == pytest.approx(4.5)


def test_a_summary_over_an_incomplete_ledger_is_refused() -> None:
    """A bad number is published or the claim is dropped — an instance silently missing is neither."""
    final = lg.apply_grading([_row("a__a-1")], REPORT)
    with pytest.raises(ValueError, match="b__b-2"):
        lg.summary(final, expected=["a__a-1", "b__b-2"])


def test_a_provisional_row_cannot_be_summarised() -> None:
    with pytest.raises(ValueError, match="submitted"):
        lg.summary([_row("a__a-1")], expected=["a__a-1"])


def test_the_json_a_row_writes_has_no_patch_text(tmp_path: Path) -> None:
    """The ledger is published beside the number; the patch lives in its own file."""
    book = lg.Ledger(tmp_path / "l.jsonl")
    book.append(_row("a__a-1"))
    stored = json.loads((tmp_path / "l.jsonl").read_text().splitlines()[0])
    assert "patch" not in stored and "model_patch" not in stored and stored["patch_sha256"]


# --- finalize: the grader's report to the headline in one step -----------------------------


def test_finalize_settles_the_submitted_rows_and_summarises_the_whole_denominator() -> None:
    rows = [_row("a__a-1"), _row("b__b-2"), _row("p__p-1", "parked")]

    final, s = lg.finalize(rows, REPORT, expected=["a__a-1", "b__b-2", "p__p-1"])

    assert [r.bucket for r in final] == ["resolved", "unresolved", "parked"]
    assert s["counts"]["resolved"] == 1 and s["total"] == 3


def test_finalize_refuses_a_ledger_with_a_hole_rather_than_summarising_part_of_it() -> None:
    with pytest.raises(ValueError, match="b__b-2"):
        lg.finalize([_row("a__a-1")], REPORT, expected=["a__a-1", "b__b-2"])


def test_finalize_only_counts_the_instances_it_was_asked_about() -> None:
    """A ledger that also holds rows from another draw does not change this draw's denominator."""
    final, s = lg.finalize([_row("a__a-1"), _row("x__x-9", "parked")], REPORT, expected=["a__a-1"])

    assert s["total"] == 1 and s["counts"]["parked"] == 0
    assert len(final) == 2  # nothing is dropped from the book
