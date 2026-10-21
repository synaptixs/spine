"""The real `ask`: an approval a person answers with `sdlc runs approve --choice …` (SSPN-228, P4a).

The answer is written by a *second* store object, as `sdlc runs approve` does from another process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.escalate import Approval, ApprovalStore, Decision, decide
from orchestrator.sdlc.own_test_ask import (
    OwnTestAnswer,
    OwnTestQuestion,
    ask_window_seconds,
    make_ask,
    render_question,
)

_Q = OwnTestQuestion(
    files=("tests/test_own.py",),
    names=("test_wrong_expectation",),
    cause="wrong_expectation",
    evidence="AssertionError: 1 != 2",
    suggestion="The test asserts something the ticket does not ask for; correct it or drop it.",
    failures="FAIL: test_wrong_expectation (test_own.OwnTests)\nAssertionError: 1 != 2\n",
)


class _Clock:
    """A clock the fake sleep advances, and a hook that lets 'the person' answer at a chosen moment."""

    def __init__(self, store_root: Path, *, answer_after: int | None = None, **answer: Any) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.root = store_root
        self.answer_after = answer_after
        self.answer = answer

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds
        if self.answer_after is not None and self.sleeps == self.answer_after:
            other = ApprovalStore(root=self.root)  # another process's view of the same files
            [pending] = [a for a in other.all() if a.pending]
            decide(pending.approval_id, store=other, **self.answer)


def _ask(
    tmp_path: Path,
    clock: _Clock,
    *,
    window: float = 600.0,
    beats: list[str] | None = None,
    seen: list[Any] | None = None,
) -> Any:
    return make_ask(
        store=ApprovalStore(root=tmp_path / "approvals"),
        run_id="r1",
        issue_key="SSPN-1",
        window_seconds=window,
        poll_seconds=5.0,
        checkpoint=(lambda **kw: beats.append(str(kw.get("status"))))
        if beats is not None
        else (lambda **kw: None),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        notifier=False,
        on_result=(lambda *a: seen.append(a)) if seen is not None else None,
    )


async def test_a_question_is_raised_as_an_own_test_approval_and_the_answer_comes_back(tmp_path: Path) -> None:
    clock = _Clock(
        tmp_path / "approvals", answer_after=3, approved=True, by="alice", note="", choice="delete"
    )
    ask = _ask(tmp_path, clock)

    answer = await ask(_Q)

    assert answer == OwnTestAnswer(choice="delete", note="", by="alice")
    [approval] = ApprovalStore(root=tmp_path / "approvals").all()
    assert approval.kind == "own_tests" and approval.run_id == "r1" and approval.issue_key == "SSPN-1"
    assert approval.decision == Decision.APPROVED.value and approval.choice == "delete"


async def test_the_question_a_person_reads_names_the_test_the_cause_and_the_command(tmp_path: Path) -> None:
    clock = _Clock(tmp_path / "approvals", answer_after=1, approved=True, by="a", note="", choice="release")
    await _ask(tmp_path, clock)(_Q)

    [approval] = ApprovalStore(root=tmp_path / "approvals").all()
    text = approval.reason
    assert "tests/test_own.py" in text and "test_wrong_expectation" in text
    assert "wrong_expectation" in text and "AssertionError: 1 != 2" in text
    assert _Q.suggestion in text
    assert f"orchestrator sdlc runs approve {approval.approval_id} --choice" in text
    for choice in ("suggest", "delete", "explain", "release", "stop"):
        assert choice in text


async def test_explain_returns_the_persons_note(tmp_path: Path) -> None:
    clock = _Clock(
        tmp_path / "approvals",
        answer_after=2,
        approved=True,
        by="alice",
        note="use unittest",
        choice="explain",
    )

    answer = await _ask(tmp_path, clock)(_Q)

    assert answer == OwnTestAnswer("explain", "use unittest", "alice")


async def test_a_plain_yes_with_no_choice_is_a_release_and_a_plain_no_is_a_stop(tmp_path: Path) -> None:
    yes = _Clock(tmp_path / "approvals", answer_after=1, approved=True, by="alice", note="", choice="")
    assert (await _ask(tmp_path, yes)(_Q)) == OwnTestAnswer("release", "", "alice")

    no = _Clock(tmp_path / "approvals2", answer_after=1, approved=False, by="bob", note="", choice="")
    other = make_ask(
        store=ApprovalStore(root=tmp_path / "approvals2"),
        run_id="r2",
        issue_key="",
        window_seconds=60,
        poll_seconds=5,
        checkpoint=lambda **kw: None,
        sleep=no.sleep,
        monotonic=no.monotonic,
        notifier=False,
    )
    assert other is not None
    assert (await other(_Q)) == OwnTestAnswer("stop", "", "bob")


async def test_when_the_window_closes_unanswered_the_answer_is_none_and_the_approval_says_why(
    tmp_path: Path,
) -> None:
    clock = _Clock(tmp_path / "approvals")  # nobody ever answers
    beats: list[str] = []
    ask = _ask(tmp_path, clock, window=30.0, beats=beats)

    answer = await ask(_Q)

    assert answer is None
    assert clock.sleeps == 6  # 30 s window, polled every 5 s, and no longer
    [approval] = ApprovalStore(root=tmp_path / "approvals").all()
    assert approval.decision == Decision.REJECTED.value and "no answer" in approval.note
    assert approval.decided_by != "" and approval.choice == ""


async def test_the_run_is_parked_while_waiting_and_running_again_after_and_keeps_its_heartbeat(
    tmp_path: Path,
) -> None:
    clock = _Clock(tmp_path / "approvals", answer_after=2, approved=True, by="a", note="", choice="release")
    beats: list[str] = []

    await _ask(tmp_path, clock, beats=beats)(_Q)

    assert beats[0] == "parked" and beats[-1] == "running"
    assert (
        len(beats) >= 3
    )  # a beat for each poll: the reaper (STALE_AFTER_SECONDS) must not take a waiting run


async def test_a_zero_window_builds_no_ask_at_all(tmp_path: Path) -> None:
    assert (
        make_ask(
            store=ApprovalStore(root=tmp_path),
            run_id="r",
            issue_key="",
            window_seconds=0,
            poll_seconds=5,
            checkpoint=lambda **kw: None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [(None, 0.0), ("", 0.0), ("0", 0.0), ("15", 900.0), ("0.5", 30.0), ("nonsense", 0.0), ("-3", 0.0)],
)
def test_the_window_is_read_in_minutes_and_anything_unreadable_means_never_ask(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, seconds: float
) -> None:
    if raw is None:
        monkeypatch.delenv("SDLC_ASK_OWN_TESTS_MINUTES", raising=False)
    else:
        monkeypatch.setenv("SDLC_ASK_OWN_TESTS_MINUTES", raw)

    assert ask_window_seconds() == seconds


def test_an_explicit_value_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_ASK_OWN_TESTS_MINUTES", "15")

    assert ask_window_seconds(2) == 120.0 and ask_window_seconds(0) == 0.0


def test_the_digest_follows_the_failing_tests_not_the_wording() -> None:
    from dataclasses import replace

    from orchestrator.sdlc.own_test_ask import question_digest

    assert question_digest(_Q) == question_digest(replace(_Q, evidence="something else"))
    assert question_digest(_Q) != question_digest(replace(_Q, names=("test_other",)))
    assert "tests/test_own.py" in render_question(_Q, "r1-1")
    assert isinstance(Approval, type)


async def test_the_result_hook_hears_the_answer_and_how_long_the_person_took(tmp_path: Path) -> None:
    clock = _Clock(
        tmp_path / "approvals", answer_after=3, approved=True, by="alice", note="", choice="delete"
    )
    seen: list[Any] = []

    await _ask(tmp_path, clock, seen=seen)(_Q)

    [(question, approval_id, answer, waited)] = seen
    assert question == _Q and approval_id.startswith("r1-")
    assert answer == OwnTestAnswer("delete", "", "alice") and waited == 15.0


async def test_the_result_hook_hears_a_window_that_closed_unanswered(tmp_path: Path) -> None:
    seen: list[Any] = []

    await _ask(tmp_path, _Clock(tmp_path / "approvals"), window=10.0, seen=seen)(_Q)

    [(_q, _id, answer, waited)] = seen
    assert answer is None and waited >= 10.0


async def test_a_hook_that_raises_does_not_change_the_answer(tmp_path: Path) -> None:
    clock = _Clock(
        tmp_path / "approvals", answer_after=1, approved=True, by="alice", note="", choice="release"
    )

    def _boom(*_a: Any) -> None:
        raise OSError("disk full")

    ask = make_ask(
        store=ApprovalStore(root=tmp_path / "approvals"),
        run_id="r1",
        issue_key="",
        window_seconds=60,
        poll_seconds=5,
        checkpoint=lambda **kw: None,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        notifier=False,
        on_result=_boom,
    )
    assert ask is not None
    assert (await ask(_Q)) == OwnTestAnswer("release", "", "alice")
