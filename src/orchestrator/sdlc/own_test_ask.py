"""The question put to a person when the automatic fixes for a test the run wrote have failed (SSPN-228, P4a).

``feature_runner.run_feature`` takes an optional ``ask`` callable shaped like its ``gate``: it is given an
:class:`OwnTestQuestion` and returns an :class:`OwnTestAnswer`, or ``None`` when nobody answered. ``None`` for
the callable (the benchmark, CI, ``sdlc feature``) means no person is reachable and the loop never asks.
This module holds only the two value types; the loop decides what each choice does, and ``autorun`` supplies
the callable that raises an approval and waits for it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AskOwnTests",
    "OwnTestAnswer",
    "OwnTestQuestion",
    "ask_window_seconds",
    "make_ask",
    "question_digest",
    "render_question",
]


@dataclass(frozen=True)
class OwnTestQuestion:
    #: Repository-relative test files the run wrote that are failing.
    files: tuple[str, ...]
    #: The failing tests, as the run named them.
    names: tuple[str, ...]
    #: ``own_test_cause`` class, the line that showed it, and one sentence on what would fix it.
    cause: str
    evidence: str
    suggestion: str
    #: The runner's output, clipped.
    failures: str


@dataclass(frozen=True)
class OwnTestAnswer:
    #: One of ``escalate.OWN_TEST_CHOICES``.
    choice: str
    #: The person's words; for ``explain`` they are what the model is told.
    note: str = ""
    by: str = ""


AskOwnTests = Callable[[OwnTestQuestion], Awaitable[OwnTestAnswer | None]]


def ask_window_seconds(minutes: float | None = None) -> float:
    """How long to wait for a person, in seconds; ``0`` means never ask.

    ``minutes`` (an explicit setting) wins over ``SDLC_ASK_OWN_TESTS_MINUTES``. Anything unreadable or
    negative is
    ``0``: "a person is reachable" is something an operator says on purpose, never something guessed.
    """
    raw: Any = minutes if minutes is not None else os.getenv("SDLC_ASK_OWN_TESTS_MINUTES", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value * 60.0 if value > 0 else 0.0


def question_digest(question: OwnTestQuestion) -> str:
    """Identifies *which* failing tests were asked about (not how the question was worded)."""
    basis = "\n".join(sorted(question.files) + ["--"] + sorted(question.names))
    return hashlib.sha256(basis.encode()).hexdigest()


def render_question(question: OwnTestQuestion, approval_id: str) -> str:
    """The question as a person reads it, ending in the exact command that answers it."""
    return (
        "The tests this run wrote are failing, and the automatic repair did not make the suite pass.\n"
        f"Files: {', '.join(question.files)}\n"
        f"Failing: {', '.join(question.names)}\n"
        f"Cause (diagnosed, no model): {question.cause} — {question.evidence}\n"
        f"Suggested fix: {question.suggestion}\n\n"
        "Answer with one of:\n"
        "  suggest   repair the tests once more, following the suggested fix\n"
        "  delete    withdraw these tests and release the change unproven\n"
        '  explain   repair the tests following your words (--note "…" is required)\n'
        "  release   release the change unproven, as the automatic rescue would\n"
        "  stop      fail the run, leaving the tests where they are\n\n"
        f'orchestrator sdlc runs approve {approval_id} --choice <one of the above> [--note "…"]'
    )


def make_ask(
    *,
    store: Any,
    run_id: str,
    issue_key: str,
    window_seconds: float,
    poll_seconds: float = 5.0,
    checkpoint: Callable[..., None],
    sleep: Callable[[float], Awaitable[None]] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    notifier: Any = None,
) -> AskOwnTests | None:
    """The ``ask`` for ``run_feature``: raise an approval, wait up to ``window_seconds`` for it to be decided.

    ``None`` when the window is not positive, so a run that was not set up to reach a person never asks. The
    approval is the autorun one (``escalate``), decided by ``orchestrator sdlc runs approve --choice``; the
    wait keeps the run's heartbeat alive and the run record says it is parked. A window that closes
    unanswered decides the approval *rejected*, in the system's name and saying so, so no stale question
    outlives the run; it never grants anything. ``notifier=False`` skips the best-effort notification.
    """
    if window_seconds <= 0:
        return None

    from orchestrator.sdlc.escalate import Decision, decide, raise_approval

    async def ask(question: OwnTestQuestion) -> OwnTestAnswer | None:
        approval = raise_approval(
            run_id=run_id,
            issue_key=issue_key,
            title="the tests this run wrote are failing — what should happen to them?",
            reason="(see the question)",
            store=store,
            kind="own_tests",
            digest=question_digest(question),
            notifier=notifier if notifier is not False else _NoNotifier(),
        )
        approval.reason = render_question(question, approval.approval_id)
        store.save(approval)
        checkpoint(
            status="parked",
            parked_reason="waiting for an answer about the run's own tests",
            approval_id=approval.approval_id,
        )
        started = monotonic()
        try:
            while True:
                await (sleep or asyncio.sleep)(poll_seconds)
                checkpoint()  # the heartbeat: a waiting run is not an abandoned one
                current = store.load(approval.approval_id)
                if current is not None and not current.pending:
                    approved = current.decision == Decision.APPROVED.value
                    choice = current.choice or ("release" if approved else "stop")
                    return OwnTestAnswer(choice=choice, note=current.note, by=current.decided_by)
                if monotonic() - started >= window_seconds:
                    decide(
                        approval.approval_id,
                        approved=False,
                        store=store,
                        by="the wait window closed",
                        note=f"no answer within {window_seconds / 60:g} min; the automatic rescue applied",
                    )
                    return None
        finally:
            checkpoint(status="running")

    return ask


class _NoNotifier:
    """Stands in for 'do not notify' (``raise_approval`` treats ``None`` as 'use the default')."""

    def notify_approval_raised(self, _request: Any) -> bool:
        return False
