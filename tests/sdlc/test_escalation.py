"""Calibrated escalation (G10): policy thresholds + blast radius + activity."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from orchestrator.sdlc.escalation import (
    EscalationPolicy,
    EscalationSignals,
    blast_radius,
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
