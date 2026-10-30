"""Build an approved plan from an editor, and watch and decide it there (SSPN-274).

The three MCP tools ``sdlc_build``, ``sdlc_builds`` and ``sdlc_build_decide`` are thin over this module.
It drives ``sdlc autorun``, the one runner that refuses to build without a current approved plan, keeps
a run record and parks for a person; it adds nothing to what a run does. What it owns:

* **Starting detached** (D71). The record is written first, so the run id comes back at once, then
  ``autorun --resume <id>`` is launched in its own session with its output in the run's artifacts. The
  run outlives a window reload or an editor restart, and the record is how anyone finds it again. The
  same command resumes it later; it is kept beside the run as ``build.json``.
* **Safe, always** (D67). The command carries ``--safe``, and nothing here or in the tools can make it
  ``--live``: the change is committed on a local branch and the developer pushes with their own git.
* **From the open checkout** (D72). ``--repo`` is the checkout and ``--base`` its current branch, so the
  build starts from that branch's last commit in a worktree of Spine's own clone. The checkout itself is
  never written to (D84); the branch and worktree are reported for the developer to fetch.
* **The review is a park** (D73, D82). ``--gate park`` makes the build wait for a ``review`` approval bound
  to a digest of its diff; :func:`show_build` shows that diff and :func:`decide_build` answers it, and
  only for the digest that was shown.
* **This repository's runs only** (D79). The run store is shared by every Spine on the machine; a run is
  listed, shown, resumed, cancelled or decided only through the checkout its record names.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

#: ``sdlc_builds`` lists at most this many runs, newest first; ``count`` counts them all.
MAX_LISTED = 50
#: A review diff larger than this is cut, and says so (invariant 7); the worktree holds the rest.
MAX_DIFF_BYTES = 200_000
#: What a run was started from (the branch), beside its other artifacts; a resume rebuilds its command.
BUILD_FILE = "build.json"
#: A run or approval id as Spine mints them. Ids a caller passes are joined into paths under the state
#: directory, so one with a separator, a parent step or an absolute root would read — and write back — a
#: record planted elsewhere, whose ``repo`` the caller chose (security review, SSPN-274).
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

__all__ = [
    "MAX_DIFF_BYTES",
    "MAX_LISTED",
    "cancel_build",
    "decide_build",
    "list_builds",
    "resume_build",
    "show_build",
    "start_build",
]


class _RefusedError(Exception):
    """A request this module will not carry out; the message is the tool's ``error``."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = extra


def _error(exc: _RefusedError) -> dict[str, Any]:
    return {"error": str(exc), **exc.extra}


def _spawn(argv: list[str], log: Path) -> int:
    """Launch ``argv`` in a session of its own, output to ``log``; returns its pid.

    A thread waits on it, so a run that ends does not linger as a zombie the record's liveness check
    would read as alive for as long as the server runs.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        proc = subprocess.Popen(  # noqa: S603 — argv is built here, never from a caller's string
            argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid


def _stop(pid: int) -> None:
    """End the run's whole session: the build may be running a test suite of its own."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)


def _orchestrator() -> list[str]:
    """This Spine's own CLI, whatever installed it (uvx, pip, a checkout)."""
    return [sys.executable, "-c", "from orchestrator.cli import app; app()"]


def _branch(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def _plan_state(repo: Path, intent_id: str) -> str:
    """``none``, ``stale``, ``approved`` or ``rejected`` — of the document on disk, as the editor shows it."""
    from orchestrator.sdlc.builddoc import load_approval, plan_digest, plan_file

    document = plan_file(intent_id, "build.md", root=repo)
    approval = load_approval(intent_id, root=repo)
    if approval is None or not document.is_file():
        return "none"
    if approval.digest != plan_digest(document.read_text(encoding="utf-8")):
        return "stale"
    return str(approval.decision).lower()


def _stores() -> tuple[Any, Any]:
    from orchestrator.sdlc.escalate import ApprovalStore, default_approval_dir
    from orchestrator.sdlc.runstate import RunStore

    return RunStore(), ApprovalStore(root=default_approval_dir())


def _check_id(kind: str, value: str) -> None:
    if not _ID.fullmatch(value or ""):
        raise _RefusedError(f"{value!r} is not a valid {kind} id")


def _alive(record: Any) -> bool:
    """Whether the run's own process is still there — waiting on a person counts.

    ``RunRecord.is_live_process`` answers for a run whose status is ``running``, which is what the reaper
    needs. A build waiting at the review gate is ``parked`` *and* alive (D82): found by the paid acceptance
    run, where the panel showed it gone and Cancel refused to end the wait it exists to end (D83).
    """
    if record.status not in ("running", "parked"):
        return False
    probe = replace(record, status="running")
    return bool(probe.is_live_process and probe.pid)


def _own(repo: Path, run_id: str, runs: Any) -> Any:
    """The record of ``run_id``, refused unless it is a run of this checkout."""
    _check_id("run", run_id)
    record = runs.load(run_id)
    if record is None:
        raise _RefusedError(f"no run {run_id!r}")
    if record.repo != str(repo):
        raise _RefusedError(f"{run_id} is not a run of {repo}")
    return record


def _row(record: Any) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "intent_id": record.intent_id,
        "status": record.status,
        "phase": record.phase,
        "alive": _alive(record),
        "spent_usd": record.spent_usd,
        "started_at": record.started_at,
        "heartbeat_at": record.heartbeat_at,
        "branch": record.branch,
        "worktree": record.worktree,
        "parked_reason": record.parked_reason,
        "approval_id": record.approval_id,
    }


def _launch(record: Any, argv: list[str], runs: Any) -> int:
    pid = _spawn(argv, Path(record.artifacts_dir) / "autorun.log")
    latest = runs.load(record.run_id) or record
    if not latest.pid or latest.pid == record.pid:  # the run has not yet claimed its record
        latest.pid = pid
        runs.save(latest)
    return pid


def _command(repo: Path, intent_id: str, branch: str, run_id: str) -> list[str]:
    """The one command a build runs: safe, parking for review, from this checkout's branch (D67, D72, D73)."""
    from orchestrator.sdlc.builddoc import plan_file, spec_path

    plan_file(intent_id, "build.md")  # the id names files; it must not choose their directory
    _check_id("run", run_id)
    spec = spec_path(intent_id, root=repo)
    return [
        *_orchestrator(),
        "sdlc",
        "autorun",
        "--source",
        f"file://{spec}",
        "--spec",
        str(spec),
        "--intent",
        intent_id,
        "--path",
        str(repo),
        "--repo",
        str(repo),
        "--base",
        branch,
        "--safe",
        "--gate",
        "park",
        "--resume",
        run_id,
    ]


def start_build(repo: Path, intent_id: str) -> dict[str, Any]:
    """Launch a safe, parking ``autorun`` of ``intent_id``'s approved plan; return its run at once."""
    from orchestrator.sdlc.autorun import default_artifacts_dir
    from orchestrator.sdlc.builddoc import load_spec, plan_file, spec_path
    from orchestrator.sdlc.runstate import RunRecord

    try:
        if not intent_id:
            raise _RefusedError("start needs intent_id: the plan to build")
        try:
            plan_file(intent_id, "build.md")  # the id names files; it must not choose their directory
        except ValueError as exc:
            raise _RefusedError(str(exc)) from exc
        state = _plan_state(repo, intent_id)
        if state != "approved":
            raise _RefusedError(
                f"the plan for {intent_id} is not approved ({state}): read it and approve it before building",
                state=state,
            )
        if load_spec(intent_id, root=repo) is None:
            raise _RefusedError(
                f"the plan for {intent_id} kept no spec (planned before builds could start from it): plan it "
                "again with sdlc_plan, read it, approve it, then build"
            )
        branch = _branch(repo)
        if not branch or branch == "HEAD":
            raise _RefusedError(
                "check out a branch first: a build starts from the last commit of the branch you are on"
            )
        runs, _approvals = _stores()
        for held in runs.all():
            if (
                held.repo == str(repo)
                and held.intent_id == intent_id
                and (held.status == "parked" or _alive(held))
            ):
                raise _RefusedError(
                    f"{intent_id} is already being built by run {held.run_id} ({held.status}): "
                    "resume, decide or "
                    "cancel that one"
                )
    except _RefusedError as exc:
        return _error(exc)

    run_id = uuid.uuid4().hex[:16]
    spec = spec_path(intent_id, root=repo)
    artifacts = default_artifacts_dir(run_id)
    argv = _command(repo, intent_id, branch, run_id)
    record = RunRecord(
        run_id=run_id,
        source=f"file://{spec}",
        phase="queued",
        repo=str(repo),
        intent_id=intent_id,
        started_at=time.time(),
        artifacts_dir=str(artifacts),
    )
    runs.save(record)  # first: the id is real before any process exists
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / BUILD_FILE).write_text(json.dumps({"branch": branch}, indent=2), encoding="utf-8")
    pid = _launch(record, argv, runs)
    return {
        "run_id": run_id,
        "status": "running",
        "pid": pid,
        "branch_from": branch,
        "artifacts_dir": str(artifacts),
        "log": str(artifacts / "autorun.log"),
    }


def resume_build(repo: Path, run_id: str) -> dict[str, Any]:
    """Launch the run again with the command it started with: after a decision, a budget raise, a crash."""
    from orchestrator.sdlc.escalate import OWN_TEST_KIND, Decision

    runs, approvals = _stores()
    try:
        record = _own(repo, run_id, runs)
        if _alive(record):
            raise _RefusedError(f"{run_id} is still running: there is nothing to resume")
        if record.status == "done":
            raise _RefusedError(f"{run_id} finished: start a new build instead")
        latest = approvals.for_run(run_id, ignoring=OWN_TEST_KIND)
        if latest is not None and latest.pending:
            raise _RefusedError(
                f"{run_id} is waiting on {latest.approval_id} ({latest.kind}): decide it first"
            )
        if latest is not None and latest.decision == Decision.REJECTED.value:
            raise _RefusedError(
                f"{run_id} was rejected by {latest.decided_by or 'a person'}: start a new build instead"
            )
        # The command is rebuilt from the record, never read back: a file is not a command to run.
        try:
            branch = str(
                json.loads((Path(record.artifacts_dir) / BUILD_FILE).read_text(encoding="utf-8"))["branch"]
            )
            argv = _command(repo, record.intent_id, branch, run_id)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise _RefusedError(
                f"{run_id} was not started from an editor: resume it with `sdlc autorun --resume`"
            ) from exc
    except _RefusedError as exc:
        return _error(exc)
    pid = _launch(record, argv, runs)
    return {"run_id": run_id, "status": "running", "pid": pid}


def cancel_build(repo: Path, run_id: str, *, by: str) -> dict[str, Any]:
    """Stop a live run (D78): its process ends, what it waited on is rejected, branch and worktree stay."""
    from orchestrator.sdlc.escalate import decide

    runs, approvals = _stores()
    try:
        record = _own(repo, run_id, runs)
        # The heartbeat is what proves the pid is still this run's; a stale record's pid may be anyone's.
        if not _alive(record):
            raise _RefusedError(f"{run_id} is not running ({record.status}): there is nothing to cancel")
    except _RefusedError as exc:
        return _error(exc)
    _stop(record.pid)
    for waiting in approvals.all():
        if waiting.run_id == run_id and waiting.pending:
            decide(
                waiting.approval_id, approved=False, store=approvals, by=by, note="cancelled from the editor"
            )
    record.status = "abandoned"
    record.parked_reason = f"cancelled by {by}"
    runs.save(record)
    return {"run_id": run_id, "status": "abandoned", "branch": record.branch, "worktree": record.worktree}


def list_builds(repo: Path) -> dict[str, Any]:
    """This checkout's runs, newest first and bounded; ``not_shown`` counts everyone else's."""
    runs, _approvals = _stores()
    everything = runs.all()
    mine = sorted((r for r in everything if r.repo == str(repo)), key=lambda r: r.started_at, reverse=True)
    return {
        "runs": [_row(r) for r in mine[:MAX_LISTED]],
        "count": len(mine),
        "truncated": len(mine) > MAX_LISTED,
        # Other checkouts', and records from a Spine that did not yet say whose they were.
        "not_shown": len(everything) - len(mine),
    }


def show_build(repo: Path, run_id: str) -> dict[str, Any]:
    """One run: its record, its stages, what it waits on, and for a review the diff and its digest."""
    from orchestrator.sdlc.builddoc import load_journey
    from orchestrator.sdlc.review_park import REVIEW_KIND, saved_diff_path

    runs, approvals = _stores()
    try:
        record = _own(repo, run_id, runs)
    except _RefusedError as exc:
        return _error(exc)
    journey = load_journey(record.intent_id, root=repo) if record.intent_id else []
    out: dict[str, Any] = {
        "run": _row(record),
        "stages": [
            {"stage": e.stage, "status": e.status, "detail": e.detail, "at": e.at}
            for e in journey
            if e.run_id == run_id
        ],
        "waiting_on": None,
    }
    pending = sorted(
        (a for a in approvals.all() if a.run_id == run_id and a.pending), key=lambda a: a.raised_at
    )
    if not pending:
        return out
    approval = pending[-1]
    out["waiting_on"] = {
        "approval_id": approval.approval_id,
        "kind": approval.kind,
        "title": approval.title,
        "reason": approval.reason,
        "digest": approval.digest,
        "raised_at": approval.raised_at,
    }
    if approval.kind == REVIEW_KIND:
        try:
            raw = saved_diff_path(Path(record.artifacts_dir), approval.approval_id).read_bytes()
        except OSError:
            raw = b""
        out["diff"] = raw[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        out["diff_bytes"] = len(raw)
        out["diff_truncated"] = len(raw) > MAX_DIFF_BYTES
    return out


def decide_build(
    repo: Path, run_id: str, approval_id: str, *, approve: bool, expected_digest: str, note: str, by: str
) -> dict[str, Any]:
    """Answer what a parked run waits on: only this checkout's run, its own approval, the diff shown."""
    from orchestrator.sdlc.escalate import OWN_TEST_KIND, decide
    from orchestrator.sdlc.review_park import REVIEW_KIND

    runs, approvals = _stores()
    try:
        _own(repo, run_id, runs)
        _check_id("approval", approval_id)
        approval = approvals.load(approval_id)
        if approval is None or approval.run_id != run_id:
            raise _RefusedError(f"{approval_id!r} is not an approval of run {run_id}")
        if not approval.pending:
            raise _RefusedError(
                f"{approval_id} was already decided ({approval.decision} by {approval.decided_by})"
            )
        if approval.kind == OWN_TEST_KIND:
            raise _RefusedError(
                f"{approval_id} asks for a choice: answer it with `sdlc runs approve {approval_id} --choice`"
            )
        if approval.digest and expected_digest != approval.digest:
            raise _RefusedError(
                f"{approval_id} is bound to digest {approval.digest[:12]}, not the one given: read it again "
                "(sdlc_builds) and decide on what you read",
                digest=approval.digest,
                expected_digest=expected_digest,
            )
        if not approve and not note.strip():
            raise _RefusedError("a rejection needs a note: say what is wrong")
        if not by:
            raise _RefusedError("cannot tell who is deciding — pass decided_by")
    except _RefusedError as exc:
        return _error(exc)
    decided = decide(approval_id, approved=approve, store=approvals, by=by, note=note)
    if approval.kind == REVIEW_KIND:
        follows = "the run is waiting on this and carries on by itself"
    else:
        follows = f"resume the run (sdlc_build action=resume run_id={run_id})"
    return {
        "run_id": run_id,
        "approval_id": approval_id,
        "decision": decided.decision,
        "decided_by": decided.decided_by,
        "note": decided.note,
        "next": follows,
    }
