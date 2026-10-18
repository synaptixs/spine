"""The facts a PR's proof bundle may state — what the run recorded, and what it did not."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult
from orchestrator.core.llm.recording import TokenLedger
from orchestrator.sdlc.baseline import BaselineCheck
from orchestrator.sdlc.proof_bundle import (
    BUNDLE_FILENAME,
    SCHEMA,
    BaselineFacts,
    CostFacts,
    JudgeFacts,
    RiskFacts,
    RunFacts,
    assess_risk,
    build_bundle,
    enabled,
    render_markdown,
    verify_bundle,
    write_bundle,
)
from orchestrator.sdlc.review import CriterionVerdict, ReviewResult

# ---- the switch ------------------------------------------------------------------------------


def test_it_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_PROOF_BUNDLE", raising=False)
    assert enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "no", " 0 "])
def test_the_off_values_turn_it_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", value)
    assert enabled() is False


def test_any_other_value_leaves_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "1")
    assert enabled() is True


# ---- the baseline ----------------------------------------------------------------------------


def test_a_baseline_never_taken_says_so() -> None:
    assert BaselineFacts.not_taken() == BaselineFacts(state="not_taken", failures=())


def test_a_clean_baseline_is_not_a_baseline_never_taken() -> None:
    facts = BaselineFacts.from_check(BaselineCheck(frozenset()))
    assert facts.state == "clean" and facts.failures == ()


def test_pre_existing_failures_are_kept_sorted() -> None:
    facts = BaselineFacts.from_check(BaselineCheck(frozenset({"tests/b.py", "tests/a.py"})))
    assert facts.state == "preexisting"
    assert facts.failures == ("tests/a.py", "tests/b.py")


def test_an_unreadable_baseline_is_not_read_as_no_failures() -> None:
    """`problems=None` is "the pre-change run could not be read" — rendering it as "nothing
    failed before" would credit the change with a clean suite nobody saw."""
    facts = BaselineFacts.from_check(BaselineCheck(None))
    assert facts.state == "unreadable" and facts.failures == ()


# ---- the judge -------------------------------------------------------------------------------


def test_judge_facts_carry_the_per_criterion_rows() -> None:
    verdict = ReviewResult(
        verdict="approve",
        summary="all met",
        criteria=[CriterionVerdict("a", "met", "ok"), CriterionVerdict("b", "met", "")],
    )

    facts = JudgeFacts.from_verdict(verdict, revisions=1)

    assert facts.verdict == "approve" and facts.revisions == 1
    assert facts.criteria == (CriterionVerdict("a", "met", "ok"), CriterionVerdict("b", "met", ""))


def test_judge_facts_tolerate_a_reviewer_that_records_no_criteria() -> None:
    """A stub or third-party reviewer returns the older, narrower shape."""
    old = SimpleNamespace(verdict="comment", blockers=["x"], summary="s", uncertain=["u"])

    facts = JudgeFacts.from_verdict(old, revisions=0)

    assert facts.criteria == () and facts.blockers == ("x",) and facts.uncertain == ("u",)


# ---- the cost --------------------------------------------------------------------------------


def test_cost_facts_snapshot_the_ledger() -> None:
    ledger = TokenLedger()
    ledger.record("implement", CompletionResult("", "model-a", 100, 40, 0.25, 1.0))
    ledger.record("semantic_review", CompletionResult("", "model-b", 10, 5, 0.05, 1.0, cost_known=False))

    facts = CostFacts.from_ledger(ledger)

    assert (facts.calls, facts.prompt_tokens, facts.completion_tokens) == (2, 110, 45)
    assert facts.cost_usd == pytest.approx(0.30)
    assert facts.models == ("model-a", "model-b")
    assert facts.unpriced_calls == 1 and facts.unreported_calls == 0


def test_cost_facts_of_an_empty_ledger_are_zero_not_missing() -> None:
    facts = CostFacts.from_ledger(TokenLedger())
    assert facts.calls == 0 and facts.cost_usd == 0.0 and facts.models == ()


# ---- the risk --------------------------------------------------------------------------------


async def test_a_python_change_measures_its_blast_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", lambda root: (12, "py:pkg.core.run"))

    risk = await assess_risk("/w", ["src/pkg/core.py"], uncertain=[], iterations=1)

    assert risk.state == "computed"
    assert risk.blast_radius == 12 and risk.radius_symbol == "py:pkg.core.run"
    assert risk.escalate is True
    assert any("blast radius" in r for r in risk.reasons)


async def test_a_change_with_no_python_file_is_not_measured_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`blast_radius()` returns (0, "") for a change with no `.py` file. Reported as 0 it reads
    as "nothing depends on this" about a Java or Markdown change it never looked at."""

    def _boom(root: Any) -> Any:
        raise AssertionError("must not be asked about a change with no Python file")

    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", _boom)

    risk = await assess_risk("/w", ["src/Main.java", "README.md"], uncertain=[], iterations=1)

    assert risk.state == "computed" and risk.blast_radius is None
    assert risk.escalate is False


async def test_the_other_signals_still_count_when_the_radius_is_not_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", lambda root: (0, ""))

    risk = await assess_risk("/w", ["README.md"], uncertain=["handles unicode"], iterations=5)

    assert risk.blast_radius is None and risk.escalate is True
    assert len(risk.reasons) == 2  # the judge's doubt, and the refine cycles


async def test_a_failing_measurement_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(root: Any) -> Any:
        raise RuntimeError("extractor fell over")

    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", _boom)

    risk = await assess_risk("/w", ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "not_computed" and "RuntimeError" in risk.detail
    assert risk.blast_radius is None and risk.escalate is False


def test_a_run_that_did_not_assess_risk_says_why() -> None:
    risk = RiskFacts.not_computed("safe mode — no PR is opened")
    assert risk.state == "not_computed" and risk.detail == "safe mode — no PR is opened"


# ==== P2: the bundle — built from the facts, hashed, rendered, written ==========================

_ROWS = (
    CriterionVerdict("exports a CSV file", "met", "export_csv writes rows"),
    CriterionVerdict("handles empty input", "met", "returns a header only"),
)


def _facts(**over: Any) -> RunFacts:
    """A run in which everything was recorded. Each test breaks one thing."""
    base: dict[str, Any] = {
        "issue_key": "SSPN-1",
        "intent_id": "intent-a",
        "title": "CSV export",
        "summary": "Add a CSV export.",
        "acceptance_criteria": ("exports a CSV file", "handles empty input"),
        "files": ("src/export.py", "tests/test_export.py"),
        "judge": JudgeFacts(verdict="approve", summary="all met", criteria=_ROWS, revisions=0),
        "baseline": BaselineFacts(state="clean"),
        "risk": RiskFacts(state="computed", blast_radius=3, radius_symbol="py:export.run", iterations=1),
        "required_behavior": "passed",
        "iterations": 1,
        "cost": CostFacts(
            calls=4, prompt_tokens=1000, completion_tokens=250, cost_usd=0.123456, models=("model-a",)
        ),
    }
    base.update(over)
    return RunFacts(**base)


def _bundle(facts: RunFacts | None = None, **kw: Any) -> dict[str, Any]:
    kw.setdefault("diff_sha256", "d" * 64)
    kw.setdefault("spine_version", "3.60.0")
    kw.setdefault("fixes_after_judge", 0)
    return build_bundle(facts or _facts(), **kw)


def _check(bundle: dict[str, Any], name: str) -> dict[str, Any]:
    return bundle["checks"][name]  # type: ignore[no-any-return]


# ---- shape and determinism -------------------------------------------------------------------


def test_the_bundle_names_its_schema_and_the_work() -> None:
    bundle = _bundle()

    assert bundle["schema"] == SCHEMA == "spine.proof-bundle/1"
    assert (bundle["issue_key"], bundle["intent_id"], bundle["title"]) == ("SSPN-1", "intent-a", "CSV export")
    assert bundle["spine_version"] == "3.60.0"
    assert set(bundle["checks"]) == {"acceptance", "tests", "required_behavior", "withdrawn", "risk"}


def test_the_same_facts_give_the_same_bytes() -> None:
    first, second = _bundle(), _bundle()

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert render_markdown(first) == render_markdown(second)


def test_no_check_is_called_anything_but_one_of_three_things() -> None:
    for facts in (_facts(), _facts(judge=None), _facts(required_behavior="not_reached")):
        for check in _bundle(facts)["checks"].values():
            assert check["status"] in {"checked", "not_checked", "not_applicable"}


# ---- the requirement and the hashes ----------------------------------------------------------


def test_the_requirement_hash_is_of_the_text_the_judge_was_given() -> None:
    bundle = _bundle()
    text = "CSV export\n\nAdd a CSV export.\n\n- exports a CSV file\n- handles empty input"

    assert bundle["integrity"]["requirement_sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_changing_a_criterion_changes_the_requirement_hash() -> None:
    other = _facts(acceptance_criteria=("exports a CSV file", "handles unicode"))

    assert _bundle()["integrity"]["requirement_sha256"] != _bundle(other)["integrity"]["requirement_sha256"]


def test_the_diff_hash_is_recorded_when_given_and_absent_when_not() -> None:
    assert _bundle(diff_sha256="a" * 64)["integrity"]["diff_sha256"] == "a" * 64

    without = _bundle(diff_sha256=None)
    assert without["integrity"]["diff_sha256"] is None
    assert any("diff" in g for g in without["gaps"])


def test_an_untouched_bundle_verifies() -> None:
    assert verify_bundle(_bundle()) is True


@pytest.mark.parametrize(
    "tamper",
    [
        lambda b: b["checks"]["acceptance"].update(status="not_checked"),
        lambda b: b["checks"]["tests"]["baseline"].update(state="unreadable"),
        lambda b: b.update(title="something else"),
        lambda b: b["integrity"].update(diff_sha256="0" * 64),
        lambda b: b["gaps"].append("nothing to see"),
    ],
)
def test_changing_any_part_of_the_bundle_breaks_its_record_hash(tamper: Any) -> None:
    bundle = _bundle()
    tampered = copy.deepcopy(bundle)
    tamper(tampered)

    assert verify_bundle(tampered) is False


def test_the_record_hash_does_not_depend_on_key_order() -> None:
    bundle = _bundle()
    shuffled = json.loads(json.dumps(bundle, sort_keys=True))
    reordered = {k: shuffled[k] for k in reversed(list(shuffled))}

    assert verify_bundle(reordered) is True


# ---- acceptance: what the judge said -----------------------------------------------------------


def test_an_approved_change_lists_every_criterion_and_counts_them() -> None:
    acceptance = _check(_bundle(), "acceptance")

    assert acceptance["status"] == "checked" and acceptance["verdict"] == "approve"
    assert acceptance["counts"] == {"met": 2, "unmet": 0, "uncertain": 0, "other": 0}
    assert acceptance["rows_recorded"] is True
    assert [r["criterion"] for r in acceptance["criteria"]] == [r.criterion for r in _ROWS]


def test_no_judge_verdict_is_not_checked_and_says_so() -> None:
    bundle = _bundle(_facts(judge=None))

    assert _check(bundle, "acceptance")["status"] == "not_checked"
    assert "acceptance" in bundle["not_recorded"]


def test_a_verdict_without_rows_is_checked_but_says_the_rows_are_missing() -> None:
    """A reviewer that records no rows (the stub) did give a verdict; it did not say which
    criteria it confirmed, and a list of zero criteria must not read as "none were met"."""
    bundle = _bundle(_facts(judge=JudgeFacts(verdict="comment", summary="stub")))

    acceptance = _check(bundle, "acceptance")
    assert acceptance["status"] == "checked" and acceptance["rows_recorded"] is False
    assert acceptance["counts"] is None
    assert any("per-criterion" in g for g in bundle["gaps"])


def test_unmet_and_uncertain_rows_are_counted_as_what_they_are() -> None:
    rows = (
        CriterionVerdict("a", "met"),
        CriterionVerdict("b", "unmet", "raises"),
        CriterionVerdict("c", "uncertain", "no test"),
        CriterionVerdict("d", "perhaps"),
    )
    judge = JudgeFacts(verdict="comment", criteria=rows, uncertain=("c",))

    counts = _check(_bundle(_facts(judge=judge)), "acceptance")["counts"]

    assert counts == {"met": 1, "unmet": 1, "uncertain": 1, "other": 1}


def test_the_judge_is_said_to_have_seen_the_build_when_fixes_followed() -> None:
    acceptance = _check(_bundle(fixes_after_judge=2), "acceptance")

    assert acceptance["fixes_after_judge"] == 2
    assert "2" in acceptance["detail"] and "after" in acceptance["detail"]


def test_not_knowing_whether_fixes_followed_the_judge_is_a_gap() -> None:
    bundle = _bundle(fixes_after_judge=None)

    assert _check(bundle, "acceptance")["fixes_after_judge"] is None
    assert any("review-loop" in g for g in bundle["gaps"])
    assert not any("review-loop" in g for g in _bundle(fixes_after_judge=0)["gaps"])


def test_a_long_list_of_criteria_is_cut_and_says_how_many() -> None:
    many = tuple(CriterionVerdict(f"criterion {n}", "met", "x" * 1000) for n in range(25))
    acceptance = _check(_bundle(_facts(judge=JudgeFacts(verdict="approve", criteria=many))), "acceptance")

    assert len(acceptance["criteria"]) == 20 and acceptance["criteria_total"] == 25
    assert all(len(r["evidence"]) <= 300 for r in acceptance["criteria"])


# ---- tests: what failed before the change -------------------------------------------------------


@pytest.mark.parametrize(
    ("baseline", "state", "gap"),
    [
        (BaselineFacts.not_taken(), "not_taken", True),
        (BaselineFacts(state="clean"), "clean", False),
        (BaselineFacts(state="preexisting", failures=("tests/test_old.py",)), "preexisting", False),
        (BaselineFacts(state="unreadable"), "unreadable", True),
    ],
)
def test_the_baseline_state_is_reported_and_the_unknown_ones_are_gaps(
    baseline: BaselineFacts, state: str, gap: bool
) -> None:
    bundle = _bundle(_facts(baseline=baseline))

    assert _check(bundle, "tests")["baseline"]["state"] == state
    assert any("baseline" in g for g in bundle["gaps"]) is gap


def test_pre_existing_failures_are_named() -> None:
    baseline = BaselineFacts(state="preexisting", failures=("tests/a.py", "tests/b.py"))

    tests = _check(_bundle(_facts(baseline=baseline)), "tests")

    assert tests["baseline"]["failures"] == ["tests/a.py", "tests/b.py"]


def test_pass_and_fail_counts_are_always_said_to_be_not_recorded() -> None:
    bundle = _bundle()

    assert _check(bundle, "tests")["counts"] is None
    assert any("counts" in g for g in bundle["gaps"])


# ---- required behaviour, withdrawn work, risk --------------------------------------------------


@pytest.mark.parametrize(
    ("state", "status"),
    [("passed", "checked"), ("no_manifest", "not_applicable"), ("not_reached", "not_checked")],
)
def test_required_behavior_maps_each_state_to_its_own_status(state: str, status: str) -> None:
    bundle = _bundle(_facts(required_behavior=state))

    assert _check(bundle, "required_behavior")["status"] == status
    assert ("required_behavior" in bundle["not_recorded"]) is (status == "not_checked")


def test_withdrawn_work_is_named_and_called_unproven() -> None:
    withdrawn = _check(
        _bundle(_facts(coverage_withdrawn=("tests/t.py",), coverage_withdrawn_production=("src/x.py",))),
        "withdrawn",
    )

    assert withdrawn["tests"] == ["tests/t.py"] and withdrawn["production"] == ["src/x.py"]
    assert "not proven" in withdrawn["detail"]


def test_nothing_withdrawn_says_so_plainly() -> None:
    withdrawn = _check(_bundle(), "withdrawn")

    assert withdrawn["tests"] == [] and withdrawn["production"] == []
    assert "not proven" not in withdrawn["detail"]


def test_a_measured_risk_carries_its_reasons() -> None:
    risk = RiskFacts(
        state="computed", escalate=True, reasons=("high blast radius: x has 12 callers",), blast_radius=12
    )

    checked = _check(_bundle(_facts(risk=risk)), "risk")

    assert checked["status"] == "checked" and checked["escalate"] is True
    assert checked["blast_radius"] == 12 and checked["reasons"] == ["high blast radius: x has 12 callers"]


def test_an_unmeasured_radius_is_none_and_a_gap_never_zero() -> None:
    risk = RiskFacts(state="computed", blast_radius=None)
    bundle = _bundle(_facts(risk=risk))

    assert _check(bundle, "risk")["blast_radius"] is None
    assert any("blast radius" in g for g in bundle["gaps"])


def test_a_measured_radius_of_zero_is_a_measurement_not_a_gap() -> None:
    bundle = _bundle(_facts(risk=RiskFacts(state="computed", blast_radius=0)))

    assert _check(bundle, "risk")["blast_radius"] == 0
    assert not any("blast radius" in g for g in bundle["gaps"])


def test_a_risk_that_was_not_computed_is_not_checked_with_its_reason() -> None:
    bundle = _bundle(_facts(risk=RiskFacts.not_computed("measurement failed: RuntimeError")))

    risk = _check(bundle, "risk")
    assert risk["status"] == "not_checked" and "RuntimeError" in risk["detail"]
    assert "risk" in bundle["not_recorded"]


# ---- cost ---------------------------------------------------------------------------------------


def test_cost_is_labelled_an_estimate_and_rounded() -> None:
    cost = _bundle()["cost"]

    assert cost["basis"] == "API list-price estimate, not an invoice"
    assert cost["usd"] == 0.1235 and cost["calls"] == 4 and cost["models"] == ["model-a"]
    assert (cost["prompt_tokens"], cost["completion_tokens"]) == (1000, 250)


def test_unpriced_and_unreported_calls_are_said_not_hidden() -> None:
    cost = CostFacts(calls=3, cost_usd=0.1, unpriced_calls=2, unreported_calls=1)

    block = _bundle(_facts(cost=cost))["cost"]

    assert block["unpriced_calls"] == 2 and block["unreported_calls"] == 1
    assert "not priced on 2 of 3 calls" in block["notes"]
    assert "usage not reported on 1 of 3 calls" in block["notes"]


def test_the_file_list_is_cut_and_says_how_many() -> None:
    files = tuple(f"src/f{n}.py" for n in range(80))

    change = _bundle(_facts(files=files))["change"]

    assert len(change["files"]) == 50 and change["files_total"] == 80


# ---- the PR-body section ------------------------------------------------------------------------


def test_the_section_says_it_lists_what_was_checked_and_promises_nothing() -> None:
    md = render_markdown(_bundle())

    assert md.startswith("### Spine proof bundle")
    assert "not a guarantee" in md
    assert "verified" not in md.lower()


def test_every_check_appears_with_its_status_word() -> None:
    md = render_markdown(_bundle(_facts(judge=None, required_behavior="no_manifest")))

    assert "| Acceptance criteria (judge) | not checked |" in md
    assert "| Required behaviours | not applicable |" in md
    assert "| Tests | checked |" in md


def test_what_was_not_recorded_is_listed_in_words() -> None:
    md = render_markdown(_bundle(_facts(baseline=BaselineFacts.not_taken())))

    assert "Not recorded" in md and "baseline" in md and "counts" in md


def test_table_cells_cannot_break_the_table() -> None:
    rows = (CriterionVerdict("a | b\nc", "met", "x | y\nz"),)
    md = render_markdown(_bundle(_facts(judge=JudgeFacts(verdict="approve", criteria=rows))))

    assert "a \\| b c" in md and "x \\| y z" in md
    assert all(line.count("|") - line.count("\\|") >= 2 for line in md.splitlines() if line.startswith("| "))


def test_the_footer_shows_the_schema_and_short_hashes() -> None:
    md = render_markdown(_bundle(diff_sha256="abcdef" + "0" * 58))

    assert SCHEMA in md and "abcdef000000" in md


def test_the_largest_section_stays_far_below_the_pull_request_body_limit() -> None:
    many = tuple(CriterionVerdict(f"criterion {n} " + "w" * 400, "met", "e" * 1000) for n in range(60))
    facts = _facts(
        judge=JudgeFacts(verdict="approve", criteria=many),
        files=tuple(f"src/{'d' * 100}/f{n}.py" for n in range(200)),
        baseline=BaselineFacts(
            state="preexisting", failures=tuple(f"tests/t{n}.py::test" for n in range(500))
        ),
    )

    assert len(render_markdown(_bundle(facts))) < 20_000  # GitHub's limit is 65,536


# ---- the file ------------------------------------------------------------------------------------


def test_the_json_is_written_under_the_run_directory(tmp_path: Path) -> None:
    bundle = _bundle()

    path = write_bundle(tmp_path, bundle)

    assert path == tmp_path / BUNDLE_FILENAME == tmp_path / "proof-bundle.json"
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == bundle and text.endswith("\n")
    assert verify_bundle(json.loads(text)) is True


def test_the_written_bytes_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    one = write_bundle(tmp_path / "a", _bundle())
    two = write_bundle(tmp_path / "b", _bundle())

    assert one is not None and two is not None and one.read_bytes() == two.read_bytes()


def test_without_a_run_directory_nothing_is_written() -> None:
    assert write_bundle(None, _bundle()) is None


def test_an_unwritable_directory_is_one_line_not_an_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")

    assert write_bundle(blocker, _bundle()) is None  # a file where the directory should be
