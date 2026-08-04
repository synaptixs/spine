"""Parking a run against a human decision — and telling someone it happened.

`escalation.py` computes calibrated risk and, by design, **never blocks**: it annotates, and
the merge gate's approver reads with attention proportional to that annotation. That is right
for a supervised pipeline. It is wrong for an unattended one, where "annotate and continue"
means nobody sees it until the PR — and where the run may need to stop *before* there is a PR
to annotate.

So risk gets two tiers:

* ``ANNOTATE`` — record it and keep going. Today's behaviour, unchanged.
* ``PARK`` — stop, write an approval, notify, and wait. The run is resumable; nothing is
  thrown away.

**A parked run that nobody is told about is just a stopped run.** The notification is the
half that makes parking useful, which is why it is here rather than left to the operator to
wire up.

**Deliberately file-backed, beside the run record.** `approval/` is the durable, DB-backed
gate the Temporal path uses, and it stays the right home when a run spans processes. Making
the linear path require Postgres to *pause* would put a heavier dependency on stopping than
on running. The record shape mirrors `ApprovalRequest` so the Mode-B move is a change of
storage, not of meaning.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# How long an unanswered approval waits before it is *surfaced* — never auto-approved. A
# timeout that grants permission is not a gate; the run stays parked and starts showing up as
# overdue, which is a prompt for a human rather than a decision made on their behalf.
DEFAULT_TIMEOUT_SECONDS = 24 * 3600.0


class Tier(str, Enum):
    ANNOTATE = "ANNOTATE"  # record it, keep going
    PARK = "PARK"  # stop and ask


class Decision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass
class Approval:
    """One pending human decision, durable across processes."""

    approval_id: str
    run_id: str
    issue_key: str
    title: str
    reason: str
    risk: str = "HIGH"
    decision: str = Decision.PENDING.value
    decided_by: str = ""
    note: str = ""
    raised_at: float = 0.0
    decided_at: float = 0.0
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    notified: bool = False

    @property
    def pending(self) -> bool:
        return self.decision == Decision.PENDING.value

    @property
    def overdue(self) -> bool:
        """Unanswered past its timeout. Surfaced, never auto-decided."""
        return self.pending and (time.time() - self.raised_at) > self.timeout_seconds


@dataclass
class ApprovalStore:
    """Approvals on disk, one JSON file each, beside the run records."""

    root: Path

    def path_for(self, approval_id: str) -> Path:
        return self.root / f"{approval_id}.json"

    def save(self, approval: Approval) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(approval), indent=2, sort_keys=True))
            os.replace(tmp, self.path_for(approval.approval_id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, approval_id: str) -> Approval | None:
        path = self.path_for(approval_id)
        if not path.is_file():
            return None
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        known = {f for f in Approval.__dataclass_fields__}
        return Approval(**{k: v for k, v in raw.items() if k in known})

    def all(self) -> list[Approval]:
        if not self.root.is_dir():
            return []
        found = [self.load(p.stem) for p in sorted(self.root.glob("*.json"))]
        return [a for a in found if a is not None]

    def for_run(self, run_id: str) -> Approval | None:
        """The newest approval raised for a run, decided or not."""
        matches = [a for a in self.all() if a.run_id == run_id]
        return max(matches, key=lambda a: a.raised_at) if matches else None


def default_approval_dir() -> Path:
    base = os.getenv("SPINE_RUN_STATE") or str(Path(tempfile.gettempdir()) / "spine-runs")
    return Path(base) / "_approvals"


def tier_for(*, reason_kind: str, risk_reasons: list[str] | None = None) -> Tier:
    """Which tier a stop belongs in.

    A verdict, an exhausted budget or a broken review fix are decisions a human must make —
    the run cannot proceed without one. Calibrated *risk* on an otherwise green change is not:
    it is a warning to read the PR carefully, and blocking on it would stop the runs that are
    working in order to say "this one looked hard".
    """
    if reason_kind in {"verdict", "budget", "regression"}:
        return Tier.PARK
    return Tier.ANNOTATE


def raise_approval(
    *,
    run_id: str,
    issue_key: str,
    title: str,
    reason: str,
    store: ApprovalStore,
    notifier: Any = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Approval:
    """Record the decision a human owes, and try to tell them.

    Notification failure is recorded, never raised: a run that has already stopped must not
    also crash because Slack was down, and an un-notified approval is still findable in
    ``sdlc runs list``.
    """
    approval = Approval(
        approval_id=f"{run_id}-{int(time.time())}",
        run_id=run_id,
        issue_key=issue_key,
        title=title,
        reason=reason,
        raised_at=time.time(),
        timeout_seconds=timeout_seconds,
    )
    approval.notified = _notify(approval, notifier)
    store.save(approval)
    return approval


def _notify(approval: Approval, notifier: Any) -> bool:
    if notifier is None:
        notifier = _default_notifier()
    if notifier is None:
        return False
    try:
        from orchestrator.notify.slack import ApprovalRequest as SlackApproval

        return bool(
            notifier.notify_approval_raised(
                SlackApproval(
                    approval_id=approval.approval_id,
                    title=f"{approval.issue_key or approval.run_id}: {approval.title}",
                    risk_classification=approval.risk,
                )
            )
        )
    except Exception:  # noqa: BLE001 — telling someone is best-effort; stopping already worked
        return False


def _default_notifier() -> Any:
    """A Slack notifier when one is configured, else nothing.

    Unconfigured is the normal case and must be silent: an operator who has not wired Slack
    should still get parking, just without the message.
    """
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return None
    from orchestrator.notify.slack import SlackWebhookConfig, SlackWebhookNotifier

    return SlackWebhookNotifier(SlackWebhookConfig(webhook_url=url))


def decide(
    approval_id: str, *, approved: bool, store: ApprovalStore, by: str = "cli", note: str = ""
) -> Approval:
    """Record a human's answer. Idempotent-ish: deciding twice keeps the first answer."""
    approval = store.load(approval_id)
    if approval is None:
        raise KeyError(f"no approval {approval_id!r}")
    if not approval.pending:
        return approval
    approval.decision = Decision.APPROVED.value if approved else Decision.REJECTED.value
    approval.decided_by = by
    approval.note = note
    approval.decided_at = time.time()
    store.save(approval)
    return approval


def render_approvals(approvals: list[Approval]) -> str:
    if not approvals:
        return "No approvals raised."
    lines = ["| Approval | Run | Issue | State | Raised | Notified |", "|---|---|---|---|---|---|"]
    for a in sorted(approvals, key=lambda a: a.raised_at, reverse=True):
        state = a.decision + (" (overdue)" if a.overdue else "")
        raised = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.raised_at)) if a.raised_at else "—"
        lines.append(
            f"| {a.approval_id} | {a.run_id} | {a.issue_key or '—'} | {state} | {raised} "
            f"| {'yes' if a.notified else 'no'} |"
        )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "Approval",
    "ApprovalStore",
    "Decision",
    "Tier",
    "decide",
    "default_approval_dir",
    "raise_approval",
    "render_approvals",
    "tier_for",
]
