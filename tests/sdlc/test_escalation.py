"""Calibrated escalation (G10): policy thresholds + blast radius + activity."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from orchestrator.sdlc.escalation import (
    EscalationPolicy,
    EscalationSignals,
    blast_radius,
    changed_python_files,
)

# ---- policy -----------------------------------------------------------------


def test_clean_run_does_not_escalate() -> None:
    decision = EscalationPolicy().decide(EscalationSignals(iterations=1, blast_radius=2))
    assert not decision.escalate and decision.reasons == []


def test_uncertain_criteria_escalate() -> None:
    decision = EscalationPolicy().decide(EscalationSignals(uncertain_criteria=["handles unicode"]))
    assert decision.escalate and "uncertain" in decision.reasons[0]


def test_high_iterations_escalate() -> None:
    decision = EscalationPolicy(max_iterations=3).decide(EscalationSignals(iterations=4))
    assert decision.escalate and "4 test cycles" in decision.reasons[0]


def test_high_blast_radius_escalates_with_symbol() -> None:
    decision = EscalationPolicy(max_blast_radius=10).decide(
        EscalationSignals(blast_radius=12, radius_symbol="py:core.env.load_local_env")
    )
    assert decision.escalate and "load_local_env" in decision.reasons[0]


def test_multiple_signals_accumulate_reasons() -> None:
    decision = EscalationPolicy().decide(
        EscalationSignals(uncertain_criteria=["a"], iterations=9, blast_radius=99, radius_symbol="s")
    )
    assert decision.escalate and len(decision.reasons) == 3


# ---- blast radius -----------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "a.py").write_text(
        "from util import helper\n\n\ndef fa():\n    return helper()\n", encoding="utf-8"
    )
    (repo / "b.py").write_text(
        "from util import helper\n\n\ndef fb():\n    return helper()\n", encoding="utf-8"
    )
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


def test_blast_radius_counts_cross_file_callers(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    # change util.py — helper() is called from two other files
    (repo / "util.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    radius, symbol = blast_radius(repo)
    assert radius == 2 and symbol == "py:util.helper"


def test_blast_radius_zero_for_clean_or_nongit(tmp_path: Path) -> None:
    assert blast_radius(_repo(tmp_path)) == (0, "")  # clean tree → no changed files
    plain = tmp_path / "plain"
    plain.mkdir()
    assert blast_radius(plain) == (0, "")


# ---- what counts as changed (found by replaying 35 real runs) ------------------------------------


def test_a_new_file_in_a_new_directory_is_seen(tmp_path: Path) -> None:
    """`git status --porcelain` collapses an untracked directory to `?? newpkg/`: the file inside it,
    which a modified file now calls, was invisible and its callers uncounted."""
    repo = _repo(tmp_path)
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "fresh.py").write_text("def fresh():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text(
        "from util import helper\nfrom newpkg.fresh import fresh\n\n\n"
        "def fb():\n    return helper() + fresh()\n",
        encoding="utf-8",
    )

    radius, symbol = blast_radius(repo)
    assert radius == 1 and symbol.endswith("fresh")


def test_the_helper_lists_the_file_inside_a_new_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "newpkg").mkdir()
    (repo / "newpkg" / "fresh.py").write_text("def fresh():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("X = 1\n", encoding="utf-8")

    assert changed_python_files(repo) == {"b.py", "newpkg/fresh.py"}


def test_a_renamed_file_is_seen_under_its_new_name(tmp_path: Path) -> None:
    """A rename is reported as `R  old -> new`; read as one path it matched nothing, so moving a
    module reported "0 dependents" however many files import it."""
    repo = _repo(tmp_path)
    _git(repo, "mv", "util.py", "core.py")
    for name in ("a.py", "b.py"):
        text = (repo / name).read_text(encoding="utf-8")
        (repo / name).write_text(text.replace("from util import", "from core import"), encoding="utf-8")

    assert blast_radius(repo) == (2, "py:core.helper")


def test_the_helper_lists_a_rename_by_its_new_name_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "mv", "util.py", "core.py")

    assert changed_python_files(repo) == {"core.py"}  # not "util.py", not "util.py -> core.py"


def test_names_with_spaces_quotes_and_non_ascii_come_back_exactly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for name in ("my mod.py", "módulo.py", 'quo"te.py'):
        (repo / name).write_text("X = 1\n", encoding="utf-8")

    assert changed_python_files(repo) == {"my mod.py", "módulo.py", 'quo"te.py'}


def test_a_deleted_file_and_non_python_files_are_not_changed_python_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "a.py")
    (repo / "notes.md").write_text("hello\n", encoding="utf-8")
    (repo / "data.json").write_text("{}\n", encoding="utf-8")

    assert changed_python_files(repo) == set()


def test_a_directory_that_is_not_a_repository_is_none_not_an_empty_set(tmp_path: Path) -> None:
    """None means "could not tell"; an empty set means "nothing Python changed"."""
    assert changed_python_files(tmp_path) is None
    assert changed_python_files(_repo(tmp_path)) == set()


# ---- activity ---------------------------------------------------------------


async def test_escalation_activity_combines_signals(tmp_path: Path) -> None:
    from orchestrator.sdlc.activities import SDLCActivities
    from orchestrator.sdlc.deps import SDLCDeps
    from orchestrator.sdlc.workspace import WorkspaceManager

    repo = _repo(tmp_path)
    (repo / "util.py").write_text("def helper():\n    return 2\n", encoding="utf-8")

    deps = SDLCDeps(
        session_factory=None,  # type: ignore[arg-type]  # escalation never touches the DB
        workspace=WorkspaceManager(root=Path("/tmp/unused")),
        escalation=EscalationPolicy(max_blast_radius=1, max_iterations=3),
    )
    payload: dict[str, Any] = {
        "path": str(repo),
        "issue_key": "E-1",
        "review": {"uncertain": ["handles unicode"]},
        "iterations": 5,
    }
    out = await SDLCActivities(deps).escalation_check(payload)
    assert out["escalate"] is True
    assert len(out["reasons"]) == 3  # uncertain + iterations + radius
    assert out["blast_radius"] == 2 and out["radius_symbol"] == "py:util.helper"
