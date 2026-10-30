"""The `--review` gate answered from anywhere, not only from a terminal (SSPN-274).

`sdlc autorun --review` asks once before the build's first write, and again about the review loop's own
fixes (B13). The terminal gate asks with ``typer.confirm``, which an editor or an MCP client cannot
answer. This gate asks through an approval instead: it raises a ``review`` approval bound to a digest of
the diff, marks the run parked, and waits for someone to decide it (`sdlc runs approve`, or the editor's
``sdlc_build_decide``). The diff the person is shown is saved beside the run's other artifacts.

**It waits in the running process** (D82), as the own-test question does (SSPN-228). A park that exited
could never be approved: a resume re-runs every stage from intake (B12) and builds a fresh diff in a
fresh worktree, so its digest would never match the yes. While it waits it keeps the run's heartbeat
alive, so a run someone is reading is not reaped as abandoned, and it waits until decided or cancelled
(D83). A process that dies mid-wait leaves its approval behind; the resumed run asks again and says
why the earlier yes does not carry.

**A yes is to the diff that was shown.** When the approval comes back APPROVED the gate measures the
worktree again; a diff that changed while it waited is asked about afresh rather than committed on the
strength of a yes to something else. Without a run to park the gate refuses, as the terminal gate does
with no terminal: nobody could ever answer it, and assuming yes is not a gate.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from orchestrator.core.digest import digest_of

#: The approval kind this gate raises; ``sdlc_build_decide`` and ``sdlc_builds`` look for it.
REVIEW_KIND = "review"

__all__ = ["REVIEW_KIND", "ReviewParkGate", "diff_digest", "review_diff", "saved_diff_path"]


def review_diff(path: Path) -> str:
    """The whole change in ``path`` against its last commit, new files included.

    ``add -N`` makes untracked files show in ``git diff`` without staging their content, which is what
    the terminal gate shows too; the two gates must show a person the same change.
    """
    subprocess.run(["git", "-C", str(path), "add", "-N", "-A"], capture_output=True, check=False)
    return subprocess.run(
        ["git", "-C", str(path), "diff", "HEAD"], capture_output=True, text=True, check=False
    ).stdout


def diff_digest(diff: str) -> str:
    return digest_of({"diff": diff})


def saved_diff_path(artifacts_dir: Path, approval_id: str) -> Path:
    """Where the diff an approval was raised for is kept, for whoever decides it."""
    return Path(artifacts_dir) / f"review-{approval_id}.diff"


class ReviewParkGate:
    """``gate(path, files) -> bool`` for ``autorun``, which calls :meth:`attach` with its run first."""

    def __init__(
        self,
        *,
        poll_seconds: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        notifier: Any = None,
    ) -> None:
        self._poll = poll_seconds
        self._sleep = sleep or asyncio.sleep
        self._notifier = notifier
        self._ctx: Any = None
        # The approvals this gate raised itself. A yes among them is this run's own earlier ask (the
        # build, before the review loop's fixes), not one a previous process was given.
        self._raised: set[str] = set()

    def attach(self, ctx: Any) -> None:
        """The run this gate parks: its id, its approvals, its record and its artifacts."""
        self._ctx = ctx

    async def __call__(self, path: Path, files: list[str]) -> bool:
        ctx = self._ctx
        if ctx is None or ctx.record is None:
            return False
        from orchestrator.sdlc.escalate import ApprovalStore, Decision, default_approval_dir

        store = ApprovalStore(root=ctx.approvals_dir or default_approval_dir())
        diff = review_diff(path)
        approval = self._raise(ctx, store, diff, files, why=self._earlier_yes(ctx, store))
        try:
            while True:
                await self._sleep(self._poll)
                ctx.checkpoint()  # the heartbeat: a run waiting on a person is not abandoned
                current = store.load(approval.approval_id)
                if current is None or current.pending:
                    continue
                if current.decision != Decision.APPROVED.value:
                    return False
                now = review_diff(path)
                if diff_digest(now) == current.digest:
                    return True
                diff = now
                approval = self._raise(
                    ctx,
                    store,
                    diff,
                    files,
                    why=f"The diff changed after {current.approval_id} was approved, so that yes does not "
                    "cover it. ",
                )
        finally:
            ctx.checkpoint(status="running")

    def _raise(self, ctx: Any, store: Any, diff: str, files: list[str], *, why: str) -> Any:
        from orchestrator.sdlc.escalate import raise_approval

        digest = diff_digest(diff)
        approval = raise_approval(
            run_id=ctx.run_id,
            issue_key=ctx.issue_key or "",
            title="review the diff before it is committed",
            reason="(see the diff)",
            store=store,
            kind=REVIEW_KIND,
            digest=digest,
            notifier=self._notifier if self._notifier is not False else _NoNotifier(),
        )
        saved = saved_diff_path(ctx.artifacts_dir, approval.approval_id)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(diff, encoding="utf-8")
        approval.reason = (
            f"{why}Review {len(files)} file(s) before anything is committed: {', '.join(files) or '(none)'}. "
            f"The diff is {saved} (digest {digest[:12]})."
        )
        store.save(approval)
        self._raised.add(approval.approval_id)
        ctx.checkpoint(
            status="parked",
            parked_reason="waiting for a review of the diff",
            approval_id=approval.approval_id,
        )
        return approval

    def _earlier_yes(self, ctx: Any, store: Any) -> str:
        """Why a resumed run asks again, when it was already given a yes for an earlier build."""
        earlier = [a for a in store.decided_for(ctx.run_id, REVIEW_KIND) if a.approval_id not in self._raised]
        if not earlier:
            return ""
        return (
            f"An earlier approval ({earlier[0].approval_id}) was for a different diff: a resumed run builds "
            "again, so it is asked again. "
        )


class _NoNotifier:
    """Stands in for 'do not notify' (``raise_approval`` treats ``None`` as 'use the default')."""

    def notify_approval_raised(self, _request: Any) -> bool:
        return False
