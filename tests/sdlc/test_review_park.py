"""The `--review` gate that parks for an approval instead of asking at a terminal (SSPN-274, D73, D82).

The run waits inside its own process, as the own-test question does (SSPN-228): a resume re-runs every
stage and builds a fresh diff, so a park that exited could never be approved. The approval is bound to a
digest of the diff, the person answers it from anywhere (`sdlc runs approve`, the editor), and the gate
returns the answer. No model is involved, so these run free (D81).
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.autorun import RunContext
from orchestrator.sdlc.escalate import ApprovalStore, decide
from orchestrator.sdlc.review_park import REVIEW_KIND, ReviewParkGate, diff_digest, review_diff
from orchestrator.sdlc.runstate import RunRecord, RunStore


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A built change, uncommitted, as `run_feature` leaves it when it asks the gate."""
    root = tmp_path / "wt"
    root.mkdir()
    (root / "cart.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    (root / "cart.py").write_text(
        "def total(items):\n    return sum(i for i in items if i)\n", encoding="utf-8"
    )
    (root / "tax.py").write_text("RATE = 0.2\n", encoding="utf-8")  # a new file is part of the change
    return root


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    store = RunStore(root=tmp_path / "state")
    record = RunRecord(run_id="run1", source="s", status="running")
    store.save(record)
    context = RunContext(
        run_id="run1", source="s", live=False, root=tmp_path, artifacts_dir=tmp_path / "artifacts"
    )
    context.record, context.store = record, store
    context.approvals_dir = tmp_path / "approvals"
    context.issue_key = "PROJ-42"
    return context


def _approvals(ctx: RunContext) -> ApprovalStore:
    assert ctx.approvals_dir is not None
    return ApprovalStore(root=ctx.approvals_dir)


def _pending(ctx: RunContext) -> Any:
    (approval,) = [a for a in _approvals(ctx).all() if a.pending]
    return approval


def _gate(ctx: RunContext, on_poll: Any) -> ReviewParkGate:
    """A gate whose wait calls ``on_poll(n)`` instead of sleeping: the test plays the person."""
    polls = {"n": 0}

    async def _sleep(_seconds: float) -> None:
        polls["n"] += 1
        if polls["n"] > 50:
            raise AssertionError("the gate kept waiting")
        on_poll(polls["n"])

    gate = ReviewParkGate(sleep=_sleep, notifier=False)
    gate.attach(ctx)
    return gate


def test_the_diff_shown_is_the_whole_change_including_new_files(worktree: Path) -> None:
    diff = review_diff(worktree)

    assert "cart.py" in diff and "+    return sum(i for i in items if i)" in diff
    assert "tax.py" in diff and "+RATE = 0.2" in diff
    assert diff_digest(diff) == diff_digest(review_diff(worktree))  # stable for the same change


def test_it_parks_for_an_approval_bound_to_the_diff_and_commits_on_a_yes(
    worktree: Path, ctx: RunContext
) -> None:
    seen: dict[str, Any] = {}

    def _person(n: int) -> None:
        if n == 1:
            approval = _pending(ctx)
            assert ctx.record is not None
            seen["record"] = (ctx.record.status, ctx.record.approval_id)
            seen["approval"] = approval
            decide(
                approval.approval_id, approved=True, store=_approvals(ctx), by="reviewer", note="looks right"
            )

    assert asyncio.run(_gate(ctx, _person)(worktree, ["cart.py", "tax.py"])) is True

    approval = seen["approval"]
    assert approval.kind == REVIEW_KIND
    assert approval.digest == diff_digest(review_diff(worktree))
    assert seen["record"] == ("parked", approval.approval_id)  # the run said it was waiting, and on what
    saved = ctx.artifacts_dir / f"review-{approval.approval_id}.diff"
    assert saved.read_text(encoding="utf-8") == review_diff(worktree)  # what the person was shown
    assert ctx.record is not None and ctx.record.status == "running"  # and it carried on afterwards


def test_a_no_declines_the_change(worktree: Path, ctx: RunContext) -> None:
    def _person(n: int) -> None:
        if n == 1:
            decide(
                _pending(ctx).approval_id,
                approved=False,
                store=_approvals(ctx),
                by="reviewer",
                note="wrong file",
            )

    assert asyncio.run(_gate(ctx, _person)(worktree, ["cart.py"])) is False


def test_it_keeps_the_run_alive_while_it_waits(worktree: Path, ctx: RunContext) -> None:
    """A parked run with no heartbeat for an hour would be reaped as abandoned while someone reads it."""
    beats: list[float] = []

    def _person(n: int) -> None:
        assert ctx.record is not None
        beats.append(ctx.record.heartbeat_at)
        time.sleep(0.005)  # so a heartbeat written after this one is a later timestamp
        if n == 3:
            decide(_pending(ctx).approval_id, approved=True, store=_approvals(ctx), by="reviewer")

    asyncio.run(_gate(ctx, _person)(worktree, ["cart.py"]))

    assert len(beats) == 3 and beats[0] < beats[1] < beats[2]  # it beat on every poll
    assert ctx.record is not None and ctx.record.heartbeat_at >= beats[-1]


def test_a_yes_to_a_diff_that_has_since_changed_is_not_a_yes(worktree: Path, ctx: RunContext) -> None:
    """Approving what you saw is not approving what is there now: the gate asks again about the new diff."""
    asked: list[Any] = []

    def _person(n: int) -> None:
        pending = [a for a in _approvals(ctx).all() if a.pending]
        if n == 1:
            asked.append(pending[0])
            (worktree / "cart.py").write_text("def total(items):\n    return 0\n", encoding="utf-8")
            decide(pending[0].approval_id, approved=True, store=_approvals(ctx), by="reviewer")
        elif pending:
            asked.append(pending[0])
            decide(pending[0].approval_id, approved=True, store=_approvals(ctx), by="reviewer")

    assert asyncio.run(_gate(ctx, _person)(worktree, ["cart.py"])) is True

    first, second = asked
    assert first.digest != second.digest
    assert second.digest == diff_digest(review_diff(worktree))
    assert "changed" in second.reason


def test_each_ask_is_its_own_approval(worktree: Path, ctx: RunContext) -> None:
    """The review loop asks again about its own fixes (B13): a yes to the build is not a yes to them."""

    def _person(_n: int) -> None:
        for a in _approvals(ctx).all():
            if a.pending:
                decide(a.approval_id, approved=True, store=_approvals(ctx), by="reviewer")

    gate = _gate(ctx, _person)
    assert asyncio.run(gate(worktree, ["cart.py"])) is True
    (worktree / "cart.py").write_text("def total(items):\n    return sum(items or [])\n", encoding="utf-8")
    assert asyncio.run(gate(worktree, ["cart.py"])) is True

    review = sorted((a for a in _approvals(ctx).all() if a.kind == REVIEW_KIND), key=lambda a: a.raised_at)
    assert len(review) == 2 and review[0].digest != review[1].digest
    # Asked again in the same run, not resumed: the build's own yes is not "an earlier approval".
    assert "different diff" not in review[1].reason


def test_a_resumed_run_says_its_earlier_yes_was_for_another_diff(worktree: Path, ctx: RunContext) -> None:
    """A resume rebuilds (D82), so the earlier approval cannot carry; the new ask says why it asks again."""
    from orchestrator.sdlc.escalate import raise_approval

    earlier = raise_approval(
        run_id="run1",
        issue_key="PROJ-42",
        title="t",
        reason="r",
        store=_approvals(ctx),
        kind=REVIEW_KIND,
        digest="0" * 64,
        notifier=_Quiet(),
    )
    decide(earlier.approval_id, approved=True, store=_approvals(ctx), by="reviewer")
    reasons: list[str] = []

    def _person(n: int) -> None:
        if n == 1:
            pending = _pending(ctx)
            reasons.append(pending.reason)
            decide(pending.approval_id, approved=True, store=_approvals(ctx), by="reviewer")

    assert asyncio.run(_gate(ctx, _person)(worktree, ["cart.py"])) is True
    assert earlier.approval_id in reasons[0] and "different diff" in reasons[0]


def test_an_unattached_gate_refuses_rather_than_assuming_yes(worktree: Path) -> None:
    """Without a run to park, nobody can answer: fail closed, as the terminal gate does with no terminal."""
    assert asyncio.run(ReviewParkGate(notifier=False)(worktree, ["cart.py"])) is False


class _Quiet:
    def notify_approval_raised(self, _request: Any) -> bool:
        return False
