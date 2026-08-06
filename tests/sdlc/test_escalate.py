"""Parking a run against a human decision, and refusing to walk past it.

`escalation.py` annotates and never blocks — right for a pipeline with a human at the merge
gate, wrong for an unattended one, where nobody sees the annotation until a PR exists that
may never be opened. These tests cover the tier that stops.

The rule they exist to protect: **an undecided approval blocks a resume**. Without that,
parking is decorative — the run stops, asks, and carries on the moment anyone retries it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.escalate import (
    Approval,
    ApprovalStore,
    Decision,
    Tier,
    decide,
    default_approval_dir,
    raise_approval,
    render_approvals,
    tier_for,
)


class _Notifier:
    def __init__(self, *, ok: bool = True, boom: bool = False) -> None:
        self.ok = ok
        self.boom = boom
        self.seen: list[Any] = []

    def notify_approval_raised(self, request: Any) -> bool:
        if self.boom:
            raise RuntimeError("slack is down")
        self.seen.append(request)
        return self.ok


def _store(tmp_path: Path) -> ApprovalStore:
    return ApprovalStore(root=tmp_path / "approvals")


# ---- which stops park ------------------------------------------------------


@pytest.mark.parametrize("kind", ["verdict", "budget", "regression"])
def test_a_decision_a_human_owes_parks(kind: str) -> None:
    """The run cannot proceed without an answer, so it stops and asks."""
    assert tier_for(reason_kind=kind) is Tier.PARK


def test_calibrated_risk_on_a_green_change_only_annotates() -> None:
    """Blocking here would stop the runs that are working in order to say "this one looked
    hard" — the PR is where that belongs."""
    assert tier_for(reason_kind="risk", risk_reasons=["blast radius 12"]) is Tier.ANNOTATE


# ---- raising ---------------------------------------------------------------


def test_raising_records_and_notifies(tmp_path: Path) -> None:
    store = _store(tmp_path)
    notifier = _Notifier()

    approval = raise_approval(
        run_id="r1",
        issue_key="SSPN-9",
        title="budget exhausted",
        reason="spent $5 of $1",
        store=store,
        notifier=notifier,
    )

    assert approval.pending and approval.notified
    assert store.load(approval.approval_id) is not None
    # The message names the ticket, not just an opaque run id.
    assert "SSPN-9" in notifier.seen[0].title


def test_a_broken_notifier_never_takes_the_run_down(tmp_path: Path) -> None:
    """The run has already stopped. Crashing because Slack is down would lose the parking."""
    approval = raise_approval(
        run_id="r1",
        issue_key="",
        title="t",
        reason="r",
        store=_store(tmp_path),
        notifier=_Notifier(boom=True),
    )

    assert approval.pending
    assert approval.notified is False  # recorded, so the gap is visible rather than assumed


def test_an_unconfigured_notifier_is_silent_not_fatal(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Most operators have not wired Slack; they should still get parking."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    approval = raise_approval(run_id="r1", issue_key="", title="t", reason="r", store=_store(tmp_path))

    assert approval.pending and approval.notified is False


# ---- deciding --------------------------------------------------------------


def test_approving_records_who_and_why(tmp_path: Path) -> None:
    store = _store(tmp_path)
    approval = raise_approval(
        run_id="r1", issue_key="", title="t", reason="r", store=store, notifier=_Notifier()
    )

    decided = decide(approval.approval_id, approved=True, store=store, by="alice", note="cap raised")

    assert decided.decision == Decision.APPROVED.value
    assert decided.decided_by == "alice" and decided.note == "cap raised"
    assert store.load(approval.approval_id).decision == Decision.APPROVED.value  # type: ignore[union-attr]


def test_deciding_twice_keeps_the_first_answer(tmp_path: Path) -> None:
    """A second opinion arriving later must not silently overturn a recorded decision."""
    store = _store(tmp_path)
    approval = raise_approval(
        run_id="r1", issue_key="", title="t", reason="r", store=store, notifier=_Notifier()
    )
    decide(approval.approval_id, approved=False, store=store, by="alice")

    again = decide(approval.approval_id, approved=True, store=store, by="bob")

    assert again.decision == Decision.REJECTED.value and again.decided_by == "alice"


def test_deciding_something_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        decide("nope", approved=True, store=_store(tmp_path))


# ---- overdue ---------------------------------------------------------------


def test_an_unanswered_approval_goes_overdue_but_is_never_auto_approved(tmp_path: Path) -> None:
    """A timeout that grants permission is not a gate."""
    store = _store(tmp_path)
    approval = Approval(
        approval_id="a1",
        run_id="r1",
        issue_key="",
        title="t",
        reason="r",
        raised_at=time.time() - 100_000,
        timeout_seconds=10.0,
    )
    store.save(approval)

    loaded = store.load("a1")

    assert loaded is not None
    assert loaded.overdue and loaded.pending
    assert loaded.decision == Decision.PENDING.value
    assert "overdue" in render_approvals([loaded])


def test_a_decided_approval_is_never_overdue(tmp_path: Path) -> None:
    approval = Approval(
        approval_id="a1",
        run_id="r1",
        issue_key="",
        title="t",
        reason="r",
        raised_at=time.time() - 100_000,
        timeout_seconds=10.0,
        decision=Decision.APPROVED.value,
    )
    assert not approval.overdue


# ---- storage ---------------------------------------------------------------


def test_the_newest_approval_for_a_run_wins(tmp_path: Path) -> None:
    """A run can be parked more than once — for a verdict, then later for a budget."""
    store = _store(tmp_path)
    store.save(Approval(approval_id="old", run_id="r1", issue_key="", title="t", reason="first", raised_at=1))
    store.save(
        Approval(approval_id="new", run_id="r1", issue_key="", title="t", reason="second", raised_at=2)
    )

    assert store.for_run("r1").approval_id == "new"  # type: ignore[union-attr]
    assert store.for_run("other") is None


def test_an_unreadable_approval_reads_as_absent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "broken.json").write_text("{ not json", encoding="utf-8")

    assert store.load("broken") is None
    assert store.all() == []


def test_the_approval_dir_is_outside_the_repo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SPINE_RUN_STATE", raising=False)
    assert Path.cwd() not in default_approval_dir().parents
