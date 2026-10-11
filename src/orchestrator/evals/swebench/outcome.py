"""One instance, one bucket — and the patch that goes to the grader.

The six buckets the published number is reported in (plan D15). Every instance lands in exactly one,
and all stay in the denominator:

``resolved`` / ``unresolved`` / ``grader-error`` are decided by the official grader, afterwards.
``parked`` / ``budget-stop`` / ``generator-error`` are decided here, from the run record the pipeline
itself writes — **not from the exit code**. A run that committed a build but whose review left
findings exits non-zero and still has a patch worth grading; filing it as a failure would understate
the pipeline. ``submitted`` is the provisional state of an instance whose patch is handed to the
grader. A run that finished without producing any change is ``unresolved``, an honest "did not
solve", and is kept apart from a crash.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The reporting buckets (plan D15).
BUCKETS = ("resolved", "unresolved", "parked", "generator-error", "budget-stop", "grader-error")

#: What :func:`classify` can return: five of the six, plus the provisional ``submitted``.
GENERATION_BUCKETS = ("submitted", "unresolved", "parked", "generator-error", "budget-stop")

#: The title ``sdlc/autorun.py`` gives the approval it raises when the spend cap stops a run. The
#: approval record has no machine-readable kind, so this is the signal; a test fails if it changes.
BUDGET_TITLE = "budget exhausted"

#: Spine's own bookkeeping and bytecode: never part of a submitted change.
_EXCLUDES = (":(exclude).spine", ":(exclude)BACKLOG.md", ":(exclude)**/__pycache__/**")


@dataclass(frozen=True)
class Patch:
    text: str
    files: tuple[str, ...]
    #: Files left out because the instance's test patch overwrites them (the grader would revert them).
    dropped: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    bucket: str
    reason: str
    spent_usd: float = 0.0
    verdict: str = ""
    branch: str = ""
    worktree: str = ""


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True)
    if done.returncode:
        raise RuntimeError(
            f"git {' '.join(args[:3])} failed: {done.stderr.decode('utf-8', 'replace').strip()}"
        )
    return done.stdout.decode("utf-8", "surrogateescape")


def extract_patch(worktree: Path, base: str, *, drop: Iterable[str]) -> Patch:
    """The change committed since ``base``, minus bookkeeping and the files the grader overwrites.

    Only the *committed* state counts: the pipeline commits a finished change, so uncommitted edits
    in the worktree are the debris of a run that stopped midway, not a result.
    """
    drop_set = set(drop)
    names = [
        n
        for n in _git(worktree, "diff", "--name-only", "-z", base, "HEAD", "--", *_EXCLUDES).split("\0")
        if n
    ]
    dropped = tuple(sorted(n for n in names if n in drop_set))
    kept = tuple(sorted(n for n in names if n not in drop_set))
    if not kept:
        return Patch(text="", files=(), dropped=dropped)
    excludes = [*_EXCLUDES, *(f":(exclude){n}" for n in dropped)]
    text = _git(worktree, "diff", "--binary", "--no-color", "--no-ext-diff", base, "HEAD", "--", *excludes)
    return Patch(text=text, files=kept, dropped=dropped)


def find_record(state_root: Path) -> dict[str, Any] | None:
    """The newest run record under ``<state_root>/_state`` — a per-instance directory holds one run."""
    records = (
        sorted((state_root / "_state").glob("*.json"), key=lambda p: p.stat().st_mtime)
        if (state_root / "_state").is_dir()
        else []
    )
    if not records:
        return None
    loaded: dict[str, Any] = json.loads(records[-1].read_text(encoding="utf-8"))
    return loaded


def budget_parked(state_root: Path, record: dict[str, Any]) -> bool:
    """Whether the approval this parked run is waiting on is the spend-cap one."""
    approval_id = str(record.get("approval_id") or "")
    path = state_root / "_approvals" / f"{approval_id}.json"
    if not approval_id or not path.is_file():
        return False
    return BUDGET_TITLE in str(json.loads(path.read_text(encoding="utf-8")).get("title", ""))


#: The line ``autorun`` prints when something other than a planned stop ends a run.
_CRASH = re.compile(r"^\[autorun\] \w+(Error|Exception)\b", re.MULTILINE)


def crashed(log: str) -> bool:
    """Whether the run's log shows an unexpected exception, as opposed to a planned stop."""
    return bool(_CRASH.search(log))


def classify(
    record: dict[str, Any] | None,
    *,
    timed_out: bool,
    patch_text: str,
    budget_parked: bool,
    crashed: bool = False,
) -> Outcome:
    """File one run in one bucket (rules in the module docstring and plan D32)."""
    if timed_out:
        return Outcome("generator-error", "wall-clock limit reached; the run was killed")
    if record is None:
        return Outcome("generator-error", "no run record was written")
    common: dict[str, Any] = {
        "spent_usd": float(record.get("spent_usd") or 0.0),
        "verdict": str(record.get("verdict") or ""),
        "branch": str(record.get("branch") or ""),
        "worktree": str(record.get("worktree") or ""),
    }
    status, phase = str(record.get("status")), str(record.get("phase"))
    if status == "parked":
        if budget_parked:
            return Outcome("budget-stop", str(record.get("parked_reason") or "spend cap reached"), **common)
        return Outcome("parked", str(record.get("parked_reason") or "refused before building"), **common)
    if status in ("running", "abandoned"):
        return Outcome("generator-error", f"the run never finished (status {status})", **common)
    if patch_text and status in ("done", "failed") and (phase in ("done", "") or crashed):
        # A committed build is a result even if something threw afterwards (in review, say).
        reason = "review left findings; the tested build was committed" if status == "failed" else "passed"
        return Outcome("submitted", reason, **common)
    if crashed:
        return Outcome("generator-error", "unexpected exception in the pipeline", **common)
    if status in ("done", "failed") and phase in ("done", ""):
        if patch_text:
            reason = (
                "review left findings; the tested build was committed" if status == "failed" else "passed"
            )
            return Outcome("submitted", reason, **common)
        return Outcome("unresolved", "finished with no patch", **common)
    if status == "failed":
        return Outcome(
            "unresolved",
            f"no patch: the run stopped in {phase or 'an early stage'} without a green change",
            **common,
        )
    return Outcome("generator-error", f"unrecognised run state {status!r}/{phase!r}", **common)
