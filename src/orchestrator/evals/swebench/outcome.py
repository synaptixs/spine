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
    #: The patch as git wrote it, byte for byte. A source file need not be UTF-8; decoding and
    #: re-encoding would change the patch the grader receives.
    data: bytes
    files: tuple[str, ...]
    #: Files left out because the instance's test patch overwrites them (the grader would revert them).
    dropped: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


@dataclass(frozen=True)
class Outcome:
    bucket: str
    reason: str
    spent_usd: float = 0.0
    verdict: str = ""
    branch: str = ""
    worktree: str = ""


def _git_bytes(root: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True)
    if done.returncode:
        raise RuntimeError(
            f"git {' '.join(args[:3])} failed: {done.stderr.decode('utf-8', 'replace').strip()}"
        )
    return done.stdout


def _git(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", "surrogateescape")


def extract_patch(worktree: Path, base: str, *, drop: Iterable[str]) -> Patch:
    """The change committed since ``base``, minus bookkeeping and the files the grader overwrites.

    Only the *committed* state counts: the pipeline commits a finished change, so uncommitted edits
    in the worktree are the debris of a run that stopped midway, not a result.
    """
    root = worktree.resolve() if worktree.is_dir() else None
    top = _git(worktree, "rev-parse", "--show-toplevel").strip() if root else ""
    if root is None or Path(top).resolve() != root:
        raise RuntimeError(f"{worktree} is not the root of a git worktree; refusing to read a patch from it")
    drop_set = set(drop)
    names = [
        n
        for n in _git(
            worktree, "diff", "--name-only", "-z", "--no-renames", base, "HEAD", "--", *_EXCLUDES
        ).split("\0")
        if n
    ]
    dropped = tuple(sorted(n for n in names if n in drop_set))
    kept = tuple(sorted(n for n in names if n not in drop_set))
    if not kept:
        return Patch(data=b"", files=(), dropped=dropped)
    excludes = [*_EXCLUDES, *(f":(exclude){n}" for n in dropped)]
    data = _git_bytes(
        worktree, "diff", "--binary", "--no-color", "--no-ext-diff", base, "HEAD", "--", *excludes
    )
    return Patch(data=data, files=kept, dropped=dropped)


def find_record(state_root: Path) -> dict[str, Any] | None:
    """The newest run record under ``<state_root>/_state`` — a per-instance directory holds one run."""
    records = (
        sorted((state_root / "_state").glob("*.json"), key=lambda p: p.stat().st_mtime)
        if (state_root / "_state").is_dir()
        else []
    )
    if not records:
        return None
    try:
        loaded: dict[str, Any] = json.loads(records[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # a run killed mid-write leaves a truncated record
        raise ValueError(f"unreadable run record {records[-1].name}: {exc}") from exc
    return loaded


def budget_parked(state_root: Path, record: dict[str, Any]) -> bool:
    """Whether the approval this parked run is waiting on is the spend-cap one."""
    approval_id = str(record.get("approval_id") or "")
    path = state_root / "_approvals" / f"{approval_id}.json"
    if not approval_id or not path.is_file():
        return False
    return BUDGET_TITLE in str(json.loads(path.read_text(encoding="utf-8")).get("title", ""))


#: The line ``autorun`` prints when something other than a planned stop ends a run.
_CRASH = re.compile(r"^\[autorun\] (\w+(?:Error|Exception))\b", re.MULTILINE)

#: Exceptions that are the pipeline *failing to produce a change*, not the machinery failing. The first
#: real run ended in ``CodegenError`` after two repair passes: the model's edit never matched the file.
#: That is an unresolved instance and counts as one — filing it as a ``generator-error`` would hide the
#: pipeline's own failure rate behind the harness's.
_FAILED_TO_SOLVE = frozenset({"CodegenError"})


#: ``autorun`` logs every test run as ``[run_tests #N] passed=False rc=R``. Exit codes 97 (the image's
#: environment would not activate) and 98 (the overlay would not extract) are the wrapper's own, not the
#: tests': a run that ends because of them is the harness failing, not the pipeline failing to solve.
_HARNESS_RC = re.compile(r"^\[run_tests #\d+\] passed=False rc=(97|98)\b", re.MULTILINE)


def harness_failure(log: str) -> str:
    """``"exit 97"`` / ``"exit 98"`` if a test run in the log failed that way, else ``""``."""
    found = _HARNESS_RC.findall(log)
    return f"exit {found[-1]}" if found else ""


def crashed(log: str) -> bool:
    """Whether the run's log shows an unexpected exception, as opposed to a planned stop or a
    pipeline that simply did not manage to produce a change."""
    return any(name not in _FAILED_TO_SOLVE for name in _CRASH.findall(log))


def classify(
    record: dict[str, Any] | None,
    *,
    timed_out: bool,
    patch_text: str | bytes,
    budget_parked: bool,
    crashed: bool = False,
    harness_failure: str = "",
) -> Outcome:
    """File one run in one bucket (rules in the module docstring and plan D32).

    The record's spend is booked whatever the outcome — a run killed on the clock had still spent
    what its record says, and filing it at zero would let the next sweep spend it again.
    """
    common: dict[str, Any] = {
        "spent_usd": float((record or {}).get("spent_usd") or 0.0),
        "verdict": str((record or {}).get("verdict") or ""),
        "branch": str((record or {}).get("branch") or ""),
        "worktree": str((record or {}).get("worktree") or ""),
    }
    if timed_out:
        return Outcome("generator-error", "wall-clock limit reached; the run was killed", **common)
    if record is None:
        return Outcome("generator-error", "no run record was written", **common)
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
    if harness_failure:
        return Outcome(
            "generator-error", f"the test environment failed ({harness_failure}), not the tests", **common
        )
    if crashed:
        return Outcome("generator-error", "unexpected exception in the pipeline", **common)
    if status in ("done", "failed") and phase in ("done", ""):
        return Outcome("unresolved", "finished with no patch", **common)
    if status == "failed":
        return Outcome(
            "unresolved",
            f"no patch: the run stopped in {phase or 'an early stage'} without a green change",
            **common,
        )
    return Outcome("generator-error", f"unrecognised run state {status!r}/{phase!r}", **common)
