"""Recent git churn, and what it is honestly allowed to say.

The gap: the recency pass lived inside `build_rca`, keyed to a fault site. The enhancement
profile drops `n_rca` — a feature has no symptom to localize — and dropped this with it, for
half of all tickets. The question is not symptom-dependent; the *intersection* is, which is why
the two paths cross the same `git log` with different file sets and word the result differently.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.sdlc.churn import changed_recently, recently_changed_files


def _repo(tmp_path: Path, *files: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_it_reports_the_files_history_touched(tmp_path: Path) -> None:
    _repo(tmp_path, "src/app/store.py", "README.md")

    assert recently_changed_files(tmp_path) == {"src/app/store.py", "README.md"}


def test_a_directory_that_is_not_a_repo_yields_nothing_and_does_not_raise(tmp_path: Path) -> None:
    """Best-effort by construction: no git, a shallow clone, a fresh checkout. A missing signal
    must never be the thing that fails a run."""
    assert recently_changed_files(tmp_path) == set()
    assert recently_changed_files(None) == set()
    assert changed_recently(["store.py"], tmp_path) == ()


def test_it_crosses_history_with_the_files_the_ticket_is_about(tmp_path: Path) -> None:
    _repo(tmp_path, "src/app/store.py", "src/app/quiet.py")
    (tmp_path / "src/app/store.py").write_text("y\n", encoding="utf-8")

    assert changed_recently(["src/app/store.py"], tmp_path) == ("src/app/store.py",)
    assert changed_recently(["src/app/absent.py"], tmp_path) == ()


def test_a_bare_filename_matches_the_path_git_reports(tmp_path: Path) -> None:
    """The two sides are named differently: git reports `src/app/store.py`, a landing site may
    carry only `store.py`."""
    _repo(tmp_path, "src/app/store.py")

    assert changed_recently(["store.py"], tmp_path) == ("store.py",)


def test_a_suffix_that_is_not_a_path_boundary_does_not_match(tmp_path: Path) -> None:
    """`store.py` must not match `my_store.py` — that is a different file, and a churn signal
    naming the wrong one is worse than none."""
    _repo(tmp_path, "src/app/my_store.py")

    assert changed_recently(["store.py"], tmp_path) == ()


def test_the_order_asked_for_is_the_order_returned_and_repeats_collapse(tmp_path: Path) -> None:
    """Deterministic output for a deterministic input — the evidence digest depends on it."""
    _repo(tmp_path, "a.py", "b.py")

    assert changed_recently(["b.py", "a.py", "b.py"], tmp_path) == ("b.py", "a.py")


def test_no_paths_asks_git_nothing(tmp_path: Path) -> None:
    assert changed_recently([], tmp_path) == ()


def test_the_bug_path_and_the_enhancement_path_share_one_implementation() -> None:
    """`build_rca` used to inline its own intersection. Two implementations of "has this changed
    lately?" would be two answers, and nothing would say which run got which."""
    import orchestrator.sdlc.rca as rca

    # Read out of the module namespace: `changed_recently` is not part of `rca`'s public API
    # (it is imported, not re-exported), and asserting through `vars` says exactly that.
    assert vars(rca)["changed_recently"] is changed_recently
