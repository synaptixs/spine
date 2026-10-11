"""One instance, one bucket — and the patch that goes to the grader.

The classification reads the run record the pipeline itself writes, not the exit code: a run that
committed a build but whose review left findings exits non-zero and still has a patch worth grading.
The records below have the shapes ``sdlc/autorun.py`` writes (``status``/``phase``/``verdict``).
"""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.evals.swebench import outcome as oc


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_overwritten.py").write_text("T = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD").strip()


# --- the patch ----------------------------------------------------------------------------


def test_the_patch_is_what_was_committed_since_the_base_minus_bookkeeping_and_overwritten_tests(
    tmp_path: Path,
) -> None:
    root, base = _repo(tmp_path)
    (root / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    (root / "tests" / "test_new.py").write_text("def test_n():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_overwritten.py").write_text("T = 2\n", encoding="utf-8")
    (root / ".spine").mkdir()
    (root / ".spine" / "plan.md").write_text("x", encoding="utf-8")
    (root / "BACKLOG.md").write_text("x", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")

    patch = oc.extract_patch(root, base, drop=["tests/test_overwritten.py"])

    assert patch.files == ("pkg/a.py", "tests/test_new.py")
    assert patch.dropped == ("tests/test_overwritten.py",)
    assert "A = 2" in patch.text and "test_new.py" in patch.text
    assert ".spine" not in patch.text and "BACKLOG" not in patch.text and "T = 2" not in patch.text


def test_the_patch_applies_cleanly_to_the_base(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    (root / "tests" / "test_new.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")
    patch = oc.extract_patch(root, base, drop=[])

    fresh = tmp_path.parent / (tmp_path.name + "-fresh")
    _git(tmp_path, "clone", "-q", str(root), str(fresh))
    _git(fresh, "checkout", "-q", base)
    done = subprocess.run(
        ["git", "apply", "--check", "-"], cwd=fresh, input=patch.text, text=True, capture_output=True
    )
    assert done.returncode == 0, done.stderr


def test_no_commit_since_the_base_is_an_empty_patch(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")  # edited but never committed

    patch = oc.extract_patch(root, base, drop=[])

    assert patch.text == "" and patch.files == ()


def test_only_overwritten_tests_changed_is_an_empty_patch_that_says_why(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    (root / "tests" / "test_overwritten.py").write_text("T = 9\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")

    patch = oc.extract_patch(root, base, drop=["tests/test_overwritten.py"])

    assert patch.text == "" and patch.dropped == ("tests/test_overwritten.py",)


# --- the record ---------------------------------------------------------------------------


def _record(**kw: object) -> dict[str, object]:
    return {
        "run_id": "r1",
        "status": "done",
        "phase": "done",
        "verdict": "PASSED",
        "spent_usd": 1.25,
        "branch": "feat/x",
        "worktree": "/w",
        **kw,
    }


def test_a_passed_run_with_a_patch_is_submitted() -> None:
    out = oc.classify(_record(), timed_out=False, patch_text="diff --git a b", budget_parked=False)
    assert out.bucket == "submitted" and out.spent_usd == 1.25


def test_a_build_whose_review_left_findings_is_still_submitted() -> None:
    """status failed + phase done: exits non-zero, but the tested build was committed."""
    out = oc.classify(
        _record(status="failed", verdict="FAILED"), timed_out=False, patch_text="diff", budget_parked=False
    )
    assert out.bucket == "submitted" and "review" in out.reason


def test_a_validity_park_is_parked_and_keeps_its_verdict() -> None:
    rec = _record(status="parked", phase="validity", verdict="UNLOCALIZED", parked_reason="no file")
    out = oc.classify(rec, timed_out=False, patch_text="", budget_parked=False)
    assert (out.bucket, out.verdict) == ("parked", "UNLOCALIZED")


def test_a_budget_park_is_a_budget_stop() -> None:
    rec = _record(status="parked", phase="implement", verdict="", parked_reason="budget exceeded")
    assert oc.classify(rec, timed_out=False, patch_text="", budget_parked=True).bucket == "budget-stop"


def test_a_wall_clock_kill_is_a_generator_error_whatever_the_record_says() -> None:
    assert (
        oc.classify(_record(status="running"), timed_out=True, patch_text="", budget_parked=False).bucket
        == "generator-error"
    )
    assert (
        oc.classify(_record(), timed_out=True, patch_text="diff", budget_parked=False).bucket
        == "generator-error"
    )


def test_no_record_is_a_generator_error() -> None:
    out = oc.classify(None, timed_out=False, patch_text="", budget_parked=False)
    assert out.bucket == "generator-error" and "no run record" in out.reason


@pytest.mark.parametrize("status", ["running", "abandoned"])
def test_a_run_that_never_finished_is_a_generator_error(status: str) -> None:
    assert (
        oc.classify(_record(status=status), timed_out=False, patch_text="", budget_parked=False).bucket
        == "generator-error"
    )


def test_a_failed_run_that_built_nothing_is_unresolved_not_an_error() -> None:
    """The loop ran to its end without a green change: an honest 'did not solve', not a crash."""
    rec = _record(status="failed", phase="implement", verdict="FAILED")
    out = oc.classify(rec, timed_out=False, patch_text="", budget_parked=False)
    assert out.bucket == "unresolved" and "no patch" in out.reason


def test_a_done_run_with_an_empty_patch_is_unresolved() -> None:
    out = oc.classify(_record(), timed_out=False, patch_text="", budget_parked=False)
    assert out.bucket == "unresolved"


def test_every_bucket_the_classifier_returns_is_one_the_ledger_knows() -> None:
    assert set(oc.GENERATION_BUCKETS) <= set(oc.BUCKETS) | {"submitted"}
    assert set(oc.BUCKETS) == {
        "resolved",
        "unresolved",
        "parked",
        "generator-error",
        "budget-stop",
        "grader-error",
    }


# --- reading the run's files --------------------------------------------------------------


def test_the_record_is_read_from_the_state_directory(tmp_path: Path) -> None:
    state = tmp_path / "_state"
    state.mkdir()
    (state / "old.json").write_text(json.dumps({"run_id": "old"}), encoding="utf-8")
    new = state / "new.json"
    new.write_text(json.dumps({"run_id": "new"}), encoding="utf-8")
    import os

    os.utime(state / "old.json", (1, 1))

    assert oc.find_record(tmp_path) == {"run_id": "new"}
    assert oc.find_record(tmp_path / "nowhere") is None


def test_a_budget_park_is_recognised_from_its_approval(tmp_path: Path) -> None:
    approvals = tmp_path / "_approvals"
    approvals.mkdir()
    (approvals / "a1.json").write_text(
        json.dumps({"title": "budget exhausted — raise the cap or drop the run?"}), encoding="utf-8"
    )
    (approvals / "a2.json").write_text(json.dumps({"title": "UNLOCALIZED: no file named"}), encoding="utf-8")

    assert oc.budget_parked(tmp_path, {"approval_id": "a1"}) is True
    assert oc.budget_parked(tmp_path, {"approval_id": "a2"}) is False
    assert oc.budget_parked(tmp_path, {"approval_id": "missing"}) is False
    assert oc.budget_parked(tmp_path, {}) is False


def test_the_budget_title_we_match_is_still_the_one_autorun_writes() -> None:
    """The approval has no machine-readable kind, so the title is the signal. If it is reworded
    this fails, rather than every budget stop silently turning into 'parked'."""
    from orchestrator.sdlc import autorun

    assert oc.BUDGET_TITLE in inspect.getsource(autorun)


# --- a crash is not a failure to solve ----------------------------------------------------


def test_an_unexpected_exception_in_the_log_is_a_crash() -> None:
    log = "[stage] implement\n[autorun] KeyError: 'summary'\nTraceback..."
    assert oc.crashed(log) is True


@pytest.mark.parametrize(
    "log",
    [
        "",
        "[autorun] artifacts in /tmp/x",
        "VERDICT: FAILED — tests stayed red after 5 refine passes",
        "[autorun] resuming r1 · phase start",
    ],
)
def test_ordinary_endings_are_not_crashes(log: str) -> None:
    assert oc.crashed(log) is False


def test_a_crash_is_a_generator_error_even_when_the_record_says_failed() -> None:
    rec = _record(status="failed", phase="implement", verdict="FAILED")
    out = oc.classify(rec, timed_out=False, patch_text="", budget_parked=False, crashed=True)
    assert out.bucket == "generator-error" and "unexpected exception" in out.reason


def test_a_crash_after_a_committed_build_still_has_its_patch() -> None:
    """The build was committed and graded work exists; the exception came afterwards (e.g. in review)."""
    out = oc.classify(
        _record(status="failed", phase="review"),
        timed_out=False,
        patch_text="diff",
        budget_parked=False,
        crashed=True,
    )
    assert out.bucket == "submitted"


def test_a_directory_that_is_not_a_repository_root_is_refused(tmp_path: Path) -> None:
    """Git would otherwise walk up and answer for whatever repository contains the directory."""
    root, base = _repo(tmp_path)
    inner = root / "pkg"  # inside the repo, but not its root

    with pytest.raises(RuntimeError, match="not the root"):
        oc.extract_patch(inner, base, drop=[])
    with pytest.raises(RuntimeError, match="not the root"):
        oc.extract_patch(tmp_path / "nowhere", base, drop=[])
