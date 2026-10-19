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
    ReviewFacts,
    ReviewFinding,
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


def _answers(answer: tuple[int, str]) -> Any:
    """A stand-in for the subprocess measurement that answers ``answer``."""

    async def _measure(path: str, timeout: float) -> tuple[int, str]:
        return answer

    return _measure


async def test_a_python_change_measures_its_blast_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _answers((12, "py:pkg.core.run")))

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

    async def _boom(path: str, timeout: float) -> Any:
        raise AssertionError("must not be asked about a change with no Python file")

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _boom)

    risk = await assess_risk("/w", ["src/Main.java", "README.md"], uncertain=[], iterations=1)

    assert risk.state == "computed" and risk.blast_radius is None
    assert risk.escalate is False


async def test_the_other_signals_still_count_when_the_radius_is_not_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _answers((0, "")))

    risk = await assess_risk("/w", ["README.md"], uncertain=["handles unicode"], iterations=5)

    assert risk.blast_radius is None and risk.escalate is True
    assert len(risk.reasons) == 2  # the judge's doubt, and the refine cycles


async def test_a_failing_measurement_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(path: str, timeout: float) -> Any:
        raise RuntimeError("extractor fell over")

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _boom)

    risk = await assess_risk("/w", ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "not_computed" and "RuntimeError" in risk.detail
    assert risk.blast_radius is None and risk.escalate is False


def test_a_run_that_did_not_assess_risk_says_why() -> None:
    risk = RiskFacts.not_computed("safe mode — no PR is opened")
    assert risk.state == "not_computed" and risk.detail == "safe mode — no PR is opened"


# ---- D12: the measurement runs in a process that can be killed -------------------------------


def _git_repo_with_a_change(root: Path) -> None:
    """a.py defines f, b.py calls it, and a.py has an uncommitted edit: one cross-file caller."""
    import subprocess

    def _g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    _g("init", "-q")
    _g("config", "user.email", "t@e")
    _g("config", "user.name", "t")
    (root / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("from a import f\n\n\ndef g():\n    return f()\n", encoding="utf-8")
    _g("add", "-A")
    _g("commit", "-q", "-m", "base")
    (root / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")


async def test_the_real_probe_measures_a_real_change(tmp_path: Path) -> None:
    _git_repo_with_a_change(tmp_path)

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "computed"
    assert risk.blast_radius == 1 and risk.radius_symbol == "py:a.f"


def test_the_limit_is_120_seconds_unless_the_environment_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.sdlc.proof_bundle import risk_timeout

    monkeypatch.delenv("SDLC_PROOF_BUNDLE_RISK_TIMEOUT", raising=False)
    assert risk_timeout() == 120.0
    monkeypatch.setenv("SDLC_PROOF_BUNDLE_RISK_TIMEOUT", "30")
    assert risk_timeout() == 30.0


@pytest.mark.parametrize("bad", ["", "soon", "0", "-5", "nan", "inf"])
def test_an_unusable_limit_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    from orchestrator.sdlc.proof_bundle import risk_timeout

    monkeypatch.setenv("SDLC_PROOF_BUNDLE_RISK_TIMEOUT", bad)
    assert risk_timeout() == 120.0


_SLEEPER = (
    "import os, sys, time\n"
    "open(os.path.join(sys.argv[1], 'pid'), 'w').write(str(os.getpid()))\n"
    "time.sleep(60)\n"
)


async def test_a_measurement_past_the_limit_is_killed_and_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The point of the subprocess: a thread would keep running, and the interpreter would wait
    for it at exit. This one is killed, and nothing is left behind."""
    import os
    import time

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._PROBE", _SLEEPER)
    started = time.monotonic()

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1, timeout=1)

    assert time.monotonic() - started < 20  # nowhere near the 60 s the probe asked for
    assert risk.state == "not_computed" and "timed out after 1 s" in risk.detail
    assert risk.blast_radius is None and risk.escalate is False
    pid = int((tmp_path / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaped, not merely signalled


async def test_cancelling_the_run_kills_the_measurement_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run that is stopped (Ctrl-C, a budget stop elsewhere) must not leave a child extracting a
    repository behind it."""
    import asyncio
    import os

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._PROBE", _SLEEPER)
    task = asyncio.ensure_future(assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1, timeout=60))
    for _ in range(100):  # wait until the child has announced itself
        if (tmp_path / "pid").exists() and (tmp_path / "pid").read_text():
            break
        await asyncio.sleep(0.05)
    pid = int((tmp_path / "pid").read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_the_limit_is_read_from_the_environment_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._PROBE", _SLEEPER)
    monkeypatch.setenv("SDLC_PROOF_BUNDLE_RISK_TIMEOUT", "2")

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "not_computed" and "timed out after 2 s" in risk.detail


async def test_a_probe_that_exits_badly_is_a_failed_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._PROBE", "import sys; sys.exit(3)")

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1, timeout=30)

    assert risk.state == "not_computed" and "measurement failed" in risk.detail


async def test_an_answer_from_a_probe_that_then_failed_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Found by the mutation sweep: with the exit status ignored, a probe that printed a number and
    then crashed was believed. A process that did not finish cleanly has not measured anything."""
    monkeypatch.setattr(
        "orchestrator.sdlc.proof_bundle._PROBE", "import sys; print('[5, \"py:x\"]'); sys.exit(3)"
    )

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1, timeout=30)

    assert risk.state == "not_computed" and "RuntimeError" in risk.detail
    assert risk.blast_radius is None


async def test_a_probe_that_prints_nonsense_is_a_failed_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._PROBE", "print('not json')")

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1, timeout=30)

    assert risk.state == "not_computed" and "measurement failed" in risk.detail


async def test_a_change_with_no_python_file_starts_no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def _no_spawn(*a: Any, **k: Any) -> Any:
        raise AssertionError("a process was started for a change with no Python file")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_spawn)

    risk = await assess_risk("/w", ["README.md"], uncertain=[], iterations=1)

    assert risk.state == "computed" and risk.blast_radius is None


# ---- D11: the Risk row states the number, not a verdict ----------------------------------------


@pytest.mark.parametrize("escalate", [True, False])
def test_the_risk_row_gives_the_number_and_passes_no_verdict(escalate: bool) -> None:
    risk = RiskFacts(
        state="computed",
        escalate=escalate,
        reasons=("high blast radius: x has 12 callers",) if escalate else (),
        blast_radius=12,
        radius_symbol="py:pkg.x",
    )
    bundle = _bundle(_facts(risk=risk))

    detail = _check(bundle, "risk")["detail"]
    md = render_markdown(bundle)

    assert "Blast radius 12 cross-file caller(s)" in detail and "py:pkg.x" in detail
    for word in ("flag", "attention", "raised no"):
        assert word not in detail.lower() and word not in md.lower()


def test_the_reasons_are_still_listed_and_the_machine_field_is_kept() -> None:
    risk = RiskFacts(
        state="computed", escalate=True, reasons=("refinement took 8 test cycles (>3)",), blast_radius=2
    )
    bundle = _bundle(_facts(risk=risk))

    assert _check(bundle, "risk")["escalate"] is True  # step 4 will read this, not a reader
    assert "**Risk reasons:** refinement took 8 test cycles (>3)" in render_markdown(bundle)


def test_an_unmeasured_radius_is_still_said_to_be_unmeasured() -> None:
    detail = _check(_bundle(_facts(risk=RiskFacts(state="computed", blast_radius=None))), "risk")["detail"]

    assert "not measured" in detail


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
    assert set(bundle["checks"]) == {
        "acceptance",
        "tests",
        "required_behavior",
        "withdrawn",
        "risk",
        "review",
    }


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


# ---- P6: a credential-looking value does not reach the PR or the file --------------------------

_SECRET = "sk-live-9f8e7d6c5b4a3210"


def _leaky_facts() -> RunFacts:
    """Every free-text place the bundle copies from the run holds the secret."""
    return _facts(
        title=f"use {_SECRET}",
        summary=f"the key is {_SECRET}",
        acceptance_criteria=(f"criterion {_SECRET}",),
        files=(f"src/{_SECRET}.py",),
        judge=JudgeFacts(
            verdict="approve",
            summary=f"summary {_SECRET}",
            criteria=(CriterionVerdict(f"crit {_SECRET}", "met", f"evidence {_SECRET}"),),
            blockers=(f"blocker {_SECRET}",),
            uncertain=(f"doubt {_SECRET}",),
        ),
        baseline=BaselineFacts(state="preexisting", failures=(f"tests/{_SECRET}.py::t",)),
        risk=RiskFacts(
            state="computed", reasons=(f"reason {_SECRET}",), blast_radius=1, radius_symbol=f"py:{_SECRET}"
        ),
        cost=CostFacts(calls=1, models=(f"model-{_SECRET}",)),
        coverage_withdrawn=(f"{_SECRET}.py",),
        coverage_withdrawn_production=(f"prod-{_SECRET}.py",),
    )


def test_a_credential_value_is_redacted_everywhere_the_bundle_copies_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", _SECRET)

    bundle = _bundle(_leaky_facts())
    blob = json.dumps(bundle) + render_markdown(bundle)

    assert _SECRET not in blob
    assert blob.count("[redacted]") >= 10  # one in each place it was planted, JSON and Markdown
    assert verify_bundle(bundle) is True  # sealed after the redaction, not before


def test_the_json_file_is_redacted_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", _SECRET)

    path = write_bundle(tmp_path, _bundle(_leaky_facts()))

    assert path is not None and _SECRET not in path.read_text(encoding="utf-8")


def test_the_requirement_hash_is_still_of_the_requirement_as_the_run_saw_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.sdlc.proof_bundle import requirement_text, sha256_text

    monkeypatch.setenv("FAKE_SERVICE_API_KEY", _SECRET)
    facts = _leaky_facts()

    bundle = _bundle(facts)

    assert bundle["integrity"]["requirement_sha256"] == sha256_text(requirement_text(facts))


def test_a_short_value_and_a_path_are_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The attempt record's rule: under 8 characters is as likely to be ordinary text, and an
    existing path is a place, not a secret."""
    monkeypatch.setenv("FAKE_PASSWORD", "abc123")
    monkeypatch.setenv("FAKE_SECRET_DIR", str(tmp_path))
    facts = _facts(acceptance_criteria=(f"abc123 stays, so does {tmp_path}",))

    criteria = _bundle(facts)["requirement"]["criteria"]

    assert criteria == [f"abc123 stays, so does {tmp_path}"]


def test_without_credential_variables_the_bundle_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(__import__("os").environ):
        if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
            monkeypatch.delenv(name)

    assert "[redacted]" not in json.dumps(_bundle())


# ---- D13: the review loop's outcome is a check of its own ------------------------------------


def _finding(n: int = 1, **over: Any) -> ReviewFinding:
    base: dict[str, Any] = {
        "severity": "warning",
        "path": f"src/mod{n}.py",
        "line": n,
        "message": f"looks like a hardcoded credential {n}",
    }
    base.update(over)
    return ReviewFinding(**base)


_CLEAN = ReviewFacts(clean=True, rounds=2, stopped="review clean")
_UNCLEAN = ReviewFacts(
    clean=False,
    rounds=1,
    stopped="the fixer edited nothing: not fixable by editing code",
    unresolved=(_finding(1), _finding(2)),
)


def test_a_clean_review_is_a_checked_row_with_its_rounds() -> None:
    row = _check(_bundle(review=_CLEAN, review_expected=True), "review")

    assert row["status"] == "checked" and row["outcome"] == "clean"
    assert row["rounds"] == 2 and row["unresolved"] == [] and row["unresolved_total"] == 0
    assert "clean after 2 round(s)" in row["detail"]


def test_a_clean_review_that_needed_no_round_does_not_invent_one() -> None:
    row = _check(_bundle(review=ReviewFacts(clean=True, rounds=0), review_expected=True), "review")

    assert row["outcome"] == "clean" and "round" not in row["detail"]


def test_an_unclean_review_says_why_and_lists_what_was_left() -> None:
    row = _check(_bundle(review=_UNCLEAN, review_expected=True), "review")

    assert row["status"] == "checked" and row["outcome"] == "unresolved"
    assert "did not finish clean" in row["detail"] and "2 unresolved" in row["detail"]
    assert "the fixer edited nothing" in row["detail"]
    assert row["unresolved_total"] == 2
    assert row["unresolved"][0] == {
        "severity": "warning",
        "path": "src/mod1.py",
        "line": 1,
        "message": "looks like a hardcoded credential 1",
    }


def test_an_unclean_review_with_nothing_listed_is_still_not_clean() -> None:
    """A crashed review stops with no findings; the absence of a list is not a clean review."""
    crashed = ReviewFacts(clean=False, rounds=0, stopped="the review crashed (RuntimeError: x)")

    row = _check(_bundle(review=crashed, review_expected=True), "review")

    assert row["outcome"] == "unresolved" and "the review crashed" in row["detail"]


def test_no_review_on_a_path_that_reviews_is_not_checked_and_is_listed_as_a_gap() -> None:
    bundle = _bundle(review=None, review_expected=True)

    row = _check(bundle, "review")
    assert row["status"] == "not_checked" and row["outcome"] == "none"
    assert "review" in bundle["not_recorded"]
    assert any("review" in g for g in bundle["gaps"])


def test_a_path_with_no_review_loop_is_not_applicable_not_a_gap() -> None:
    bundle = _bundle(review=None, review_expected=False)

    row = _check(bundle, "review")
    assert row["status"] == "not_applicable" and row["outcome"] == "none"
    assert "review" not in bundle["not_recorded"]
    assert not any("review" in g for g in bundle["gaps"])


def test_the_default_is_a_path_with_no_review_loop() -> None:
    assert _check(build_bundle(_facts()), "review")["status"] == "not_applicable"


def test_the_findings_are_capped_and_the_total_says_how_many_were_left_out() -> None:
    many = ReviewFacts(
        clean=False, rounds=1, stopped="gave up", unresolved=tuple(_finding(n) for n in range(1, 31))
    )

    bundle = _bundle(review=many, review_expected=True)
    row = _check(bundle, "review")
    md = render_markdown(bundle)

    assert len(row["unresolved"]) == 20 and row["unresolved_total"] == 30
    assert "(+20 more)" in md  # 10 listed in the PR section of 30
    assert len(md) < 20_000


def test_a_long_finding_message_is_bounded() -> None:
    long = ReviewFacts(clean=False, stopped="x", unresolved=(_finding(1, message="m" * 5000),))

    row = _check(_bundle(review=long, review_expected=True), "review")

    assert len(row["unresolved"][0]["message"]) <= 300


def test_the_markdown_shows_the_review_row_and_the_unresolved_findings() -> None:
    md = render_markdown(_bundle(review=_UNCLEAN, review_expected=True))

    assert "| Automated review | checked |" in md
    assert "**Unresolved review findings:**" in md
    assert "`src/mod1.py:1`" in md and "hardcoded credential 2" in md


def test_the_markdown_for_a_clean_review_lists_no_findings() -> None:
    md = render_markdown(_bundle(review=_CLEAN, review_expected=True))

    assert "| Automated review | checked |" in md and "Unresolved review findings" not in md


def test_a_finding_message_with_a_pipe_cannot_break_the_table() -> None:
    piped = ReviewFacts(clean=False, stopped="a | b", unresolved=(_finding(1, message="x | y"),))

    md = render_markdown(_bundle(review=piped, review_expected=True))

    [row] = [ln for ln in md.splitlines() if ln.startswith("| Automated review")]
    assert row.count("|") - row.count("\\|") == 4  # three cells, no stray separator


def test_a_credential_in_a_finding_or_the_stop_reason_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", _SECRET)
    leaky = ReviewFacts(
        clean=False,
        stopped=f"stopped near {_SECRET}",
        unresolved=(_finding(1, path=f"src/{_SECRET}.py", message=f"leaks {_SECRET}"),),
    )

    bundle = _bundle(review=leaky, review_expected=True)
    blob = json.dumps(bundle) + render_markdown(bundle)

    assert _SECRET not in blob and "[redacted]" in blob
    assert verify_bundle(bundle) is True


def test_the_review_outcome_is_part_of_the_sealed_record() -> None:
    clean, unclean = (
        _bundle(review=_CLEAN, review_expected=True),
        _bundle(review=_UNCLEAN, review_expected=True),
    )

    assert clean["integrity"]["record_sha256"] != unclean["integrity"]["record_sha256"]
    tampered = copy.deepcopy(unclean)
    tampered["checks"]["review"]["outcome"] = "clean"
    assert verify_bundle(tampered) is False


def test_the_same_review_gives_byte_identical_json() -> None:
    a = _bundle(review=_UNCLEAN, review_expected=True)
    b = _bundle(review=_UNCLEAN, review_expected=True)

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_a_loop_result_is_read_into_facts() -> None:
    from orchestrator.codereview.verifiers import Finding, Severity
    from orchestrator.sdlc.reviewloop import LoopResult, Round

    finding = Finding(verifier_id="v", rule="r", severity=Severity.WARNING, path="a.py", line=3, message="m")
    deferred = Finding(verifier_id="v", rule="r", severity=Severity.NIT, path="b.py", line=1, message="d")
    result = LoopResult(
        rounds=[Round(number=1, findings=1), Round(number=2, findings=1)],
        remaining=[finding],
        deferred=[deferred],
        stopped="gave up",
        clean=False,
    )

    facts = ReviewFacts.from_loop(result)

    assert facts.clean is False and facts.rounds == 2 and facts.stopped == "gave up"
    assert facts.unresolved == (ReviewFinding(severity="warning", path="a.py", line=3, message="m"),)
    assert facts.deferred_total == 1


def test_a_loop_result_that_says_clean_but_lists_findings_is_not_clean() -> None:
    """The flag and the list must agree; when they do not, the safer reading wins."""
    from orchestrator.codereview.verifiers import Finding, Severity
    from orchestrator.sdlc.reviewloop import LoopResult

    left = Finding(verifier_id="v", rule="r", severity=Severity.BLOCKER, path="a.py", line=3, message="m")

    assert ReviewFacts.from_loop(LoopResult(remaining=[left], clean=True)).clean is False


# ---- the maintainer review of 2026-10-19 -----------------------------------------------------


def _loop_result(
    *, clean: bool, remaining: list[Any], stopped: str, deferred: list[Any] | None = None
) -> Any:
    from orchestrator.sdlc.reviewloop import LoopResult, Round

    return LoopResult(
        rounds=[Round(number=1, findings=len(remaining))],
        remaining=remaining,
        deferred=deferred or [],
        stopped=stopped,
        clean=clean,
    )


def _core(severity: str, message: str = "m") -> Any:
    from orchestrator.codereview.verifiers import Finding, Severity

    return Finding(
        verifier_id="v", rule="r", severity=Severity(severity), path="a.py", line=3, message=message
    )


# B1: the loop's own flag is what makes a PR a draft; the bundle must agree with it.


def test_a_loop_that_ended_clean_with_only_advisory_findings_is_clean_and_lists_them_as_advisory() -> None:
    result = _loop_result(
        clean=True, remaining=[_core("nit", "style")], stopped="only advisory findings remain"
    )

    facts = ReviewFacts.from_loop(result)

    assert facts.clean is True and facts.unresolved == ()
    assert facts.advisory == (ReviewFinding(severity="nit", path="a.py", line=3, message="style"),)


@pytest.mark.parametrize("severity", ["blocker", "warning"])
def test_a_clean_flag_with_an_actionable_finding_left_is_still_not_clean(severity: str) -> None:
    result = _loop_result(clean=True, remaining=[_core(severity), _core("nit")], stopped="x")

    facts = ReviewFacts.from_loop(result)

    assert facts.clean is False
    assert [f.severity for f in facts.unresolved] == [severity]  # the nit is not an unresolved defect
    assert [f.severity for f in facts.advisory] == ["nit"]


def test_the_bundle_calls_an_advisory_only_review_clean_and_says_what_is_left_for_a_human() -> None:
    result = _loop_result(
        clean=True, remaining=[_core("nit", "style")], stopped="only advisory findings remain"
    )
    bundle = _bundle(review=ReviewFacts.from_loop(result), review_expected=True)

    row = _check(bundle, "review")
    md = render_markdown(bundle)

    assert row["outcome"] == "clean" and row["status"] == "checked"
    assert row["advisory_total"] == 1 and row["advisory"][0]["message"] == "style"
    assert "did not finish clean" not in row["detail"] and "1 advisory" in row["detail"]
    assert "**Advisory review findings:**" in md and "Unresolved review findings" not in md


def test_findings_set_aside_for_a_human_are_counted_in_the_row_and_the_pr() -> None:
    review = ReviewFacts(clean=True, rounds=2, stopped="review clean", deferred_total=4)

    bundle = _bundle(review=review, review_expected=True)

    assert _check(bundle, "review")["deferred_total"] == 4
    assert "4 set aside for a human" in _check(bundle, "review")["detail"]


# S1: a measurement that did not measure is not a zero.


def _repo_with_a_deleted_module(root: Path) -> None:
    import subprocess

    def _g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    _g("init", "-q")
    _g("config", "user.email", "t@e")
    _g("config", "user.name", "t")
    (root / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "user1.py").write_text("from core import f\n\n\ndef a():\n    return f()\n", encoding="utf-8")
    (root / "user2.py").write_text("from core import f\n\n\ndef b():\n    return f()\n", encoding="utf-8")
    _g("add", "-A")
    _g("commit", "-q", "-m", "base")
    (root / "core.py").unlink()


async def test_a_change_that_only_deletes_python_is_not_measured_not_zero(tmp_path: Path) -> None:
    _repo_with_a_deleted_module(tmp_path)

    risk = await assess_risk(str(tmp_path), ["core.py"], uncertain=[], iterations=1)

    assert risk.state == "computed" and risk.blast_radius is None
    assert "deleted" in risk.detail


async def test_a_directory_git_cannot_read_is_not_computed_not_zero(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    risk = await assess_risk(str(tmp_path), ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "not_computed" and "git" in risk.detail


def test_an_unmeasured_radius_carries_its_own_reason_into_the_row_and_the_gaps() -> None:
    risk = RiskFacts(
        state="computed",
        blast_radius=None,
        detail="no added or modified Python file; deleted ones are not counted",
    )

    bundle = _bundle(_facts(risk=risk))

    assert "deleted ones are not counted" in _check(bundle, "risk")["detail"]
    assert "cross-file caller" not in _check(bundle, "risk")["detail"]
    assert any("deleted ones are not counted" in g for g in bundle["gaps"])


# S2: the cut happens after the redaction, or half a credential survives it.


def test_a_credential_straddling_a_cut_is_redacted_before_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-LIVE-ABCDEF0123456789"
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", secret)
    facts = _facts(
        acceptance_criteria=("c" * 490 + secret,),
        judge=JudgeFacts(
            verdict="approve",
            criteria=(CriterionVerdict("c" * 490 + secret, "met", "e" * 290 + secret),),
        ),
    )
    review = ReviewFacts(clean=False, stopped="x", unresolved=(_finding(1, message="m" * 290 + secret),))

    bundle = _bundle(facts, review=review, review_expected=True)
    blob = json.dumps(bundle) + render_markdown(bundle)

    assert "sk-LIV" not in blob and "sk-L" not in blob
    assert verify_bundle(bundle) is True


# S6: a list that is cut says so.


def test_withdrawn_lists_longer_than_the_pr_shows_say_how_many_are_left_out() -> None:
    names = tuple(f"tests/t{n}.py" for n in range(25))

    bundle = _bundle(_facts(coverage_withdrawn=names))
    md = render_markdown(bundle)

    assert "(+15 more)" in md and len(_check(bundle, "withdrawn")["tests"]) == 25


# S3: what was measured on the build is said to be on the build.


def test_the_lines_that_describe_the_build_say_so() -> None:
    bundle = _bundle(_facts(baseline=BaselineFacts(state="clean")))
    md = render_markdown(bundle)

    assert "after the build" in _check(bundle, "tests")["detail"]
    assert "ran green on the build" in _check(bundle, "required_behavior")["detail"]
    assert "**Files changed by the build:**" in md
