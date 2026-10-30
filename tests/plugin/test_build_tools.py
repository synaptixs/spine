"""`sdlc_build`, `sdlc_builds` and `sdlc_build_decide`: an approved plan built from the editor (SSPN-274).

Driven through the real tool functions. Nothing here calls a model (D81): a start is checked up to the
detached process it launches, and every park a person decides is a seeded run record and approval,
answered through the tool the editor will call. The one real process (`test_a_real_start_…`) stops at
the plan gate, before any spend.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin import server
from orchestrator.sdlc import editor_builds
from orchestrator.sdlc.builddoc import append_journey, load_spec, plan_file
from orchestrator.sdlc.escalate import ApprovalStore, Decision, raise_approval
from orchestrator.sdlc.review_park import REVIEW_KIND, diff_digest, saved_diff_path
from orchestrator.sdlc.runstate import RunRecord, RunStore

_SPEC = {
    "intent_id": "PROJ-42",
    "title": "Cart.total raises KeyError for an unknown sku",
    "summary": "Cart.total in shop/cart.py crashes when a sku has no price.",
    "acceptance_criteria": ["Cart.total skips skus without a price instead of raising KeyError"],
}


class _Quiet:
    def notify_approval_raised(self, _request: Any) -> bool:
        return False


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Pat Dev", "-c", "user.email=pat@example.test", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs, approvals and artifacts in this test's own directory, never the machine's."""
    state = tmp_path / "spine-runs"
    monkeypatch.setenv("SPINE_RUN_STATE", str(state))
    monkeypatch.setenv("SPINE_RUN_ARTIFACTS", str(state / "artifacts"))
    return state


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(
        "class Cart:\n"
        "    def total(self, prices, items):\n"
        "        return sum(prices[s] * q for s, q in items)\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(".spine/\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Pat Dev")
    _git(root, "config", "user.email", "pat@example.test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


def _plan(checkout: Path, *, approve: bool = True) -> None:
    out = asyncio.run(server.sdlc_plan(str(checkout), dict(_SPEC)))
    assert "path" in out, out
    if approve:
        decided = server.sdlc_approve(str(checkout), "PROJ-42", decided_by="Pat Dev")
        assert decided.get("decision") == "APPROVED", decided


class _Spawned:
    """Stands in for launching the detached `autorun`: records what would run, returns a pid."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv: list[str], log: Path) -> int:
        self.calls.append((argv, log))
        return os.getpid()  # a live pid, so the run reads as alive


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> _Spawned:
    fake = _Spawned()
    monkeypatch.setattr(editor_builds, "_spawn", fake)
    return fake


def _store() -> RunStore:
    return RunStore()


def _approvals() -> ApprovalStore:
    from orchestrator.sdlc.escalate import default_approval_dir

    return ApprovalStore(root=default_approval_dir())


def _seed(checkout: Path, run_id: str, **fields: Any) -> RunRecord:
    record = RunRecord(
        run_id=run_id,
        source="file://spec",
        repo=str(checkout.resolve()),
        started_at=time.time(),
        artifacts_dir=str(Path(os.environ["SPINE_RUN_ARTIFACTS"]) / run_id),
        **fields,
    )
    _store().save(record)
    return record


# ---- the plan keeps its spec ----------------------------------------------------------------------


def test_planning_keeps_the_spec_a_build_needs(checkout: Path) -> None:
    """The plan gate re-derives the plan from its spec; a build started later has to hand it the same one."""
    _plan(checkout, approve=False)

    kept = load_spec("PROJ-42", root=checkout)
    assert kept is not None and _SPEC.items() <= kept.items()  # the validated spec, defaults filled in


# ---- sdlc_build start ------------------------------------------------------------------------------


def test_a_build_starts_only_from_an_approved_plan(checkout: Path, spawned: _Spawned) -> None:
    _plan(checkout, approve=False)

    out = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    assert "approved" in out["error"] and out["state"] == "none"
    assert spawned.calls == [] and _store().all() == []


def test_a_plan_that_changed_after_its_approval_does_not_build(checkout: Path, spawned: _Spawned) -> None:
    _plan(checkout)
    document = plan_file("PROJ-42", "build.md", root=checkout)
    document.write_text(document.read_text(encoding="utf-8") + "\nedited after the yes\n", encoding="utf-8")

    out = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    assert out["state"] == "stale" and spawned.calls == []


def test_a_plan_from_before_specs_were_kept_says_to_plan_again(checkout: Path, spawned: _Spawned) -> None:
    _plan(checkout)
    plan_file("PROJ-42", "spec.json", root=checkout).unlink()

    out = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    assert "sdlc_plan" in out["error"] and spawned.calls == []


def test_a_build_starts_from_a_checkout_on_a_branch(checkout: Path, spawned: _Spawned) -> None:
    _plan(checkout)
    _git(checkout, "checkout", "-q", "--detach")

    out = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    assert "branch" in out["error"] and spawned.calls == []


def test_a_build_needs_a_local_checkout(tmp_path: Path, spawned: _Spawned) -> None:
    for where in ("https://github.com/example/shop.git", str(tmp_path / "nowhere")):
        assert "local checkout" in server.sdlc_build(where, intent_id="PROJ-42")["error"]
    assert spawned.calls == []


def test_a_start_returns_the_run_at_once_and_launches_a_safe_parking_autorun(
    checkout: Path, spawned: _Spawned
) -> None:
    _plan(checkout)

    out = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    run_id = out["run_id"]
    record = _store().load(run_id)
    assert record is not None and record.repo == str(checkout.resolve()) and record.status == "running"
    assert record.pid == os.getpid()
    ((argv, log),) = spawned.calls
    assert argv[argv.index("autorun") - 1] == "sdlc"
    joined = " ".join(argv)
    assert "--safe" in argv and "--live" not in joined  # D67: never live from the editor
    assert argv[argv.index("--gate") + 1] == "park"
    assert argv[argv.index("--resume") + 1] == run_id  # it continues the record written first
    assert argv[argv.index("--path") + 1] == str(checkout.resolve())
    assert argv[argv.index("--repo") + 1] == str(checkout.resolve())  # builds from the checkout (D72)
    assert argv[argv.index("--base") + 1] == "main"
    assert argv[argv.index("--intent") + 1] == "PROJ-42"
    assert (
        _SPEC.items() <= json.loads(Path(argv[argv.index("--spec") + 1]).read_text(encoding="utf-8")).items()
    )
    assert log == Path(record.artifacts_dir) / "autorun.log"


def test_live_is_not_reachable_over_mcp() -> None:
    """D67: no parameter of any build tool can ask for a live run."""
    for tool in (server.sdlc_build, server.sdlc_builds, server.sdlc_build_decide):
        params = set(inspect.signature(tool).parameters)
        assert not params & {"live", "confirm", "safe", "argv", "args"}, tool.__name__


def test_one_plan_builds_once_at_a_time(checkout: Path, spawned: _Spawned) -> None:
    _plan(checkout)
    first = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    second = server.sdlc_build(str(checkout), intent_id="PROJ-42")

    assert first["run_id"] in second["error"] and len(spawned.calls) == 1


# ---- sdlc_build resume / cancel --------------------------------------------------------------------


def _started(checkout: Path, spawned: _Spawned) -> str:
    _plan(checkout)
    return str(server.sdlc_build(str(checkout), intent_id="PROJ-42")["run_id"])


def test_a_live_run_is_not_resumed(checkout: Path, spawned: _Spawned) -> None:
    run_id = _started(checkout, spawned)

    out = server.sdlc_build(str(checkout), action="resume", run_id=run_id)

    assert "still running" in out["error"] and len(spawned.calls) == 1


def test_a_run_waiting_on_a_decision_is_not_resumed(checkout: Path, spawned: _Spawned) -> None:
    run_id = _started(checkout, spawned)
    record = _store().load(run_id)
    assert record is not None
    record.status, record.pid = "parked", 0
    _store().save(record)
    raise_approval(
        run_id=run_id,
        issue_key="",
        title="t",
        reason="r",
        store=_approvals(),
        kind="validity",
        notifier=_Quiet(),
    )

    out = server.sdlc_build(str(checkout), action="resume", run_id=run_id)

    assert "waiting on" in out["error"] and len(spawned.calls) == 1


def test_a_stopped_run_resumes_with_the_same_command(checkout: Path, spawned: _Spawned) -> None:
    run_id = _started(checkout, spawned)
    record = _store().load(run_id)
    assert record is not None
    record.status, record.pid = "parked", 0  # e.g. the plan gate parked it, and the plan is approved now
    _store().save(record)

    out = server.sdlc_build(str(checkout), action="resume", run_id=run_id)

    assert out["run_id"] == run_id and len(spawned.calls) == 2
    assert spawned.calls[1][0] == spawned.calls[0][0]


def test_cancel_stops_a_live_run_and_rejects_what_it_waited_on(
    checkout: Path, spawned: _Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _started(checkout, spawned)
    waiting = raise_approval(
        run_id=run_id,
        issue_key="",
        title="t",
        reason="r",
        store=_approvals(),
        kind=REVIEW_KIND,
        notifier=_Quiet(),
    )
    killed: list[int] = []
    monkeypatch.setattr(editor_builds, "_stop", lambda pid: killed.append(pid))

    out = server.sdlc_build(str(checkout), action="cancel", run_id=run_id)

    assert out["status"] == "abandoned" and killed == [os.getpid()]
    record = _store().load(run_id)
    assert record is not None and record.status == "abandoned" and record.branch == ""
    decided = _approvals().load(waiting.approval_id)
    assert decided is not None and decided.decision == Decision.REJECTED.value and "cancel" in decided.note


def test_cancel_refuses_a_run_whose_process_is_gone(
    checkout: Path, spawned: _Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _started(checkout, spawned)
    record = _store().load(run_id)
    assert record is not None
    record.heartbeat_at = 0.0  # no heartbeat for over an hour: the pid may belong to someone else now
    RunStore().path_for(run_id).write_text(json.dumps({**record.__dict__}), encoding="utf-8")
    killed: list[int] = []
    monkeypatch.setattr(editor_builds, "_stop", lambda pid: killed.append(pid))

    out = server.sdlc_build(str(checkout), action="cancel", run_id=run_id)

    assert "not running" in out["error"] and killed == []


def test_an_unknown_action_is_refused(checkout: Path) -> None:
    assert "action" in server.sdlc_build(str(checkout), action="push", run_id="x")["error"]


# ---- sdlc_builds -----------------------------------------------------------------------------------


def test_the_list_is_this_repositorys_runs_newest_first(checkout: Path, tmp_path: Path) -> None:
    _seed(checkout, "old", status="done")
    time.sleep(0.01)
    _seed(checkout, "new", status="parked")
    _store().save(RunRecord(run_id="elsewhere", source="s", repo=str(tmp_path / "other")))
    _store().save(RunRecord(run_id="before-3-67", source="s"))  # an older Spine named no checkout

    out = server.sdlc_builds(str(checkout))

    assert [r["run_id"] for r in out["runs"]] == ["new", "old"]
    assert out["not_shown"] == 2 and out["truncated"] is False


def test_the_list_is_bounded_and_says_so(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editor_builds, "MAX_LISTED", 3)
    for n in range(5):
        _seed(checkout, f"r{n}")

    out = server.sdlc_builds(str(checkout))

    assert len(out["runs"]) == 3 and out["count"] == 5 and out["truncated"] is True


def test_a_run_parked_for_review_shows_its_diff_and_digest(checkout: Path) -> None:
    record = _seed(checkout, "r1", status="parked", branch="feat/x/PROJ-42", worktree="/w/r1")
    diff = "diff --git a/shop/cart.py b/shop/cart.py\n+    return 0\n"
    approval = raise_approval(
        run_id="r1",
        issue_key="",
        title="t",
        reason="review it",
        store=_approvals(),
        kind=REVIEW_KIND,
        digest=diff_digest(diff),
        notifier=_Quiet(),
    )
    saved = saved_diff_path(Path(record.artifacts_dir), approval.approval_id)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(diff, encoding="utf-8")

    out = server.sdlc_builds(str(checkout), run_id="r1")

    assert out["run"]["branch"] == "feat/x/PROJ-42" and out["run"]["worktree"] == "/w/r1"
    waiting = out["waiting_on"]
    assert waiting["approval_id"] == approval.approval_id and waiting["kind"] == REVIEW_KIND
    assert waiting["digest"] == diff_digest(diff)
    assert out["diff"] == diff and out["diff_truncated"] is False


def test_a_large_diff_is_bounded_and_says_so(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editor_builds, "MAX_DIFF_BYTES", 10)
    record = _seed(checkout, "r1", status="parked")
    approval = raise_approval(
        run_id="r1",
        issue_key="",
        title="t",
        reason="r",
        store=_approvals(),
        kind=REVIEW_KIND,
        digest="d",
        notifier=_Quiet(),
    )
    saved = saved_diff_path(Path(record.artifacts_dir), approval.approval_id)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text("x" * 50, encoding="utf-8")

    out = server.sdlc_builds(str(checkout), run_id="r1")

    assert out["diff"] == "x" * 10 and out["diff_truncated"] is True and out["diff_bytes"] == 50


def test_a_run_shows_the_stages_it_went_through(checkout: Path) -> None:
    from orchestrator.sdlc.builddoc import JourneyEntry

    _seed(checkout, "r1", status="parked", phase="plan", intent_id="PROJ-42")
    for stage, status in (("intake", "ok"), ("plan", "failed")):
        append_journey(
            JourneyEntry(
                run_id="r1", stage=stage, status=status, detail=stage, at="2026-10-30T11:00:00+00:00"
            ),
            intent_id="PROJ-42",
            root=checkout,
        )
    append_journey(
        JourneyEntry(run_id="other", stage="intake", status="ok", detail="", at="2026-10-30T10:00:00+00:00"),
        intent_id="PROJ-42",
        root=checkout,
    )

    out = server.sdlc_builds(str(checkout), run_id="r1")

    assert [(s["stage"], s["status"]) for s in out["stages"]] == [("intake", "ok"), ("plan", "failed")]


def test_another_repositorys_run_is_not_shown(checkout: Path, tmp_path: Path) -> None:
    _store().save(RunRecord(run_id="elsewhere", source="s", repo=str(tmp_path / "other")))

    assert "not a run of" in server.sdlc_builds(str(checkout), run_id="elsewhere")["error"]


# ---- sdlc_build_decide -----------------------------------------------------------------------------


def _review_park(checkout: Path) -> Any:
    _seed(checkout, "r1", status="parked")
    return raise_approval(
        run_id="r1",
        issue_key="",
        title="t",
        reason="r",
        store=_approvals(),
        kind=REVIEW_KIND,
        digest="a" * 64,
        notifier=_Quiet(),
    )


def test_approving_records_the_person_and_the_note(checkout: Path) -> None:
    approval = _review_park(checkout)

    out = server.sdlc_build_decide(
        str(checkout), "r1", approval.approval_id, approve=True, expected_digest="a" * 64, note="reads right"
    )

    assert out["decision"] == Decision.APPROVED.value and out["decided_by"] == "Pat Dev"  # the git name
    decided = _approvals().load(approval.approval_id)
    assert decided is not None and decided.decided_by == "Pat Dev" and decided.note == "reads right"


def test_a_decision_on_a_diff_that_is_not_the_one_shown_records_nothing(checkout: Path) -> None:
    approval = _review_park(checkout)

    for digest in ("b" * 64, ""):
        out = server.sdlc_build_decide(
            str(checkout), "r1", approval.approval_id, approve=True, expected_digest=digest
        )
        assert "digest" in out["error"]
    still = _approvals().load(approval.approval_id)
    assert still is not None and still.pending


def test_a_rejection_needs_a_note(checkout: Path) -> None:
    approval = _review_park(checkout)

    out = server.sdlc_build_decide(
        str(checkout), "r1", approval.approval_id, approve=False, expected_digest="a" * 64
    )

    assert "note" in out["error"]
    still = _approvals().load(approval.approval_id)
    assert still is not None and still.pending


def test_a_decision_is_about_this_repositorys_run_and_its_own_approval(
    checkout: Path, tmp_path: Path
) -> None:
    approval = _review_park(checkout)
    _store().save(RunRecord(run_id="elsewhere", source="s", repo=str(tmp_path / "other")))
    _seed(checkout, "r2", status="parked")

    other_repo = server.sdlc_build_decide(str(checkout), "elsewhere", approval.approval_id, approve=True)
    other_run = server.sdlc_build_decide(
        str(checkout), "r2", approval.approval_id, approve=True, expected_digest="a" * 64
    )

    assert "not a run of" in other_repo["error"] and "not an approval of" in other_run["error"]
    still = _approvals().load(approval.approval_id)
    assert still is not None and still.pending


def test_a_park_with_no_digest_is_decided_without_one(checkout: Path) -> None:
    """A budget park binds to nothing; it is approved with a note and resumed."""
    _seed(checkout, "r1", status="parked")
    approval = raise_approval(
        run_id="r1",
        issue_key="",
        title="budget",
        reason="cap",
        store=_approvals(),
        kind="budget",
        notifier=_Quiet(),
    )

    out = server.sdlc_build_decide(str(checkout), "r1", approval.approval_id, approve=True, note="raised it")

    assert out["decision"] == Decision.APPROVED.value


def test_behind_a_company_login_the_signed_in_person_decides(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B114, as for plans: the token names who decided, and a typed name cannot stand in for it."""
    approval = _review_park(checkout)
    monkeypatch.setattr(
        server, "_signed_in_person", lambda _t: ("pat@corp.test", frozenset({"pat@corp.test"}))
    )

    refused = server.sdlc_build_decide(
        str(checkout),
        "r1",
        approval.approval_id,
        approve=True,
        expected_digest="a" * 64,
        decided_by="someone",
    )
    out = server.sdlc_build_decide(
        str(checkout), "r1", approval.approval_id, approve=True, expected_digest="a" * 64
    )

    assert "signed in as" in refused["error"]
    assert out["decided_by"] == "pat@corp.test"


# ---- one real process, stopped before any spend ---------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="detached sessions are POSIX")
def test_a_real_start_parks_at_the_plan_gate_when_the_code_moved_since_the_yes(checkout: Path) -> None:
    """The start is real: the detached `autorun` runs, re-derives the plan, finds it stale and parks (D81).

    The tool checks the document on disk; the gate checks the document the code would produce now. A
    commit after the approval is the case only the gate can see, and it is caught before any model call.
    """
    _plan(checkout)
    (checkout / "shop" / "tax.py").write_text("RATE = 0.2\n", encoding="utf-8")
    _git(checkout, "add", "shop/tax.py")
    _git(checkout, "commit", "-qm", "moved on")

    run_id = server.sdlc_build(str(checkout), intent_id="PROJ-42")["run_id"]
    deadline = time.time() + 90
    record = _store().load(run_id)
    while record is not None and record.status == "running" and time.time() < deadline:
        time.sleep(0.5)
        record = _store().load(run_id)

    log = (Path(record.artifacts_dir) / "autorun.log").read_text(encoding="utf-8") if record else ""
    assert record is not None and record.status == "parked", log
    assert record.phase == "plan" and "changed since" in record.parked_reason, log
    assert record.spent_usd == 0.0


# ---- ids a caller passes name records, never paths -------------------------------------------------


# Not "": an empty `run_id` asks `sdlc_builds` for the list, by design.
@pytest.mark.parametrize("bad", ["../../x", "/tmp/forged", "a/b", "..", "x" * 200])
def test_an_id_that_is_not_an_id_is_refused_before_any_file_is_read(
    checkout: Path, tmp_path: Path, bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run or approval id is joined into a path under the state directory; one with a separator, a
    parent step or an absolute root would read (and write back) a record the caller planted elsewhere,
    whose `repo` they chose, whose `pid` cancel would signal and whose `build.json` resume would run."""
    forged = tmp_path / "forged.json"
    forged.write_text(
        json.dumps({"run_id": "forged", "source": "s", "repo": str(checkout.resolve()), "status": "parked"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    stopped: list[int] = []
    monkeypatch.setattr(editor_builds, "_stop", lambda pid: stopped.append(pid))
    real = _review_park(checkout)

    calls = [
        server.sdlc_builds(str(checkout), run_id=bad),
        server.sdlc_build(str(checkout), action="resume", run_id=bad),
        server.sdlc_build(str(checkout), action="cancel", run_id=bad),
        server.sdlc_build_decide(
            str(checkout), bad, real.approval_id, approve=True, expected_digest="a" * 64
        ),
        server.sdlc_build_decide(str(checkout), "r1", bad, approve=True, expected_digest="a" * 64),
    ]

    for out in calls:
        assert "error" in out and "not a valid" in out["error"], out
    assert stopped == []
    still = _approvals().load(real.approval_id)
    assert still is not None and still.pending


def test_a_resume_runs_the_build_command_never_one_read_from_a_file(
    checkout: Path, spawned: _Spawned
) -> None:
    run_id = _started(checkout, spawned)
    record = _store().load(run_id)
    assert record is not None
    record.status, record.pid = "parked", 0
    _store().save(record)
    (Path(record.artifacts_dir) / editor_builds.BUILD_FILE).write_text(
        json.dumps({"argv": ["/bin/sh", "-c", "touch pwned"], "branch": "main"}), encoding="utf-8"
    )

    server.sdlc_build(str(checkout), action="resume", run_id=run_id)

    assert spawned.calls[1][0] == spawned.calls[0][0]  # rebuilt from the record, not the file
    assert "/bin/sh" not in spawned.calls[1][0]


# ---- found by the paid acceptance run: a build waiting for review is parked AND alive ---------------


def _waiting(checkout: Path, *, alive: bool) -> Any:
    """A build parked at the review gate whose process is (or is not) still there, waiting."""
    record = _seed(checkout, "w1", status="parked", pid=os.getpid() if alive else 0)
    if not alive:
        record.heartbeat_at = 0.0
        RunStore().path_for("w1").write_text(json.dumps({**record.__dict__}), encoding="utf-8")
    return raise_approval(
        run_id="w1",
        issue_key="",
        title="t",
        reason="r",
        store=_approvals(),
        kind=REVIEW_KIND,
        digest="a" * 64,
        notifier=_Quiet(),
    )


def test_a_build_waiting_for_review_reads_as_alive(checkout: Path) -> None:
    """Its record says parked (it waits on a person) while its process runs: not shown as gone."""
    _waiting(checkout, alive=True)

    (row,) = server.sdlc_builds(str(checkout))["runs"]

    assert row["status"] == "parked" and row["alive"] is True


def test_cancel_ends_a_build_waiting_for_review(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D83: the wait has no timeout, so Cancel is how it ends."""
    waiting = _waiting(checkout, alive=True)
    killed: list[int] = []
    monkeypatch.setattr(editor_builds, "_stop", lambda pid: killed.append(pid))

    out = server.sdlc_build(str(checkout), action="cancel", run_id="w1")

    assert out["status"] == "abandoned" and killed == [os.getpid()]
    decided = _approvals().load(waiting.approval_id)
    assert decided is not None and decided.decision == Decision.REJECTED.value


def test_a_build_still_waiting_is_not_resumed_beside_itself(checkout: Path, spawned: _Spawned) -> None:
    _waiting(checkout, alive=True)

    out = server.sdlc_build(str(checkout), action="resume", run_id="w1")

    assert "still running" in out["error"] and spawned.calls == []


def test_a_waiting_build_whose_process_died_is_gone_not_cancellable(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _waiting(checkout, alive=False)
    killed: list[int] = []
    monkeypatch.setattr(editor_builds, "_stop", lambda pid: killed.append(pid))

    (row,) = server.sdlc_builds(str(checkout))["runs"]
    out = server.sdlc_build(str(checkout), action="cancel", run_id="w1")

    assert row["alive"] is False and "not running" in out["error"] and killed == []
