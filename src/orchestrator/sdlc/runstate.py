"""What a run knows about itself, on disk — so a crash is recoverable, not fatal.

The walking skeleton held its state in a local variable for the length of one process. Kill
it and you lose which ticket it owned, which worktree it built, and whether it had already
created a tracker issue — the last of which is how a crashed run once minted a duplicate
ticket that had to be deleted by hand.

A run record fixes three things at once:

* **Resume, not restart.** A retry picks up the same run, adopts the issue it already
  created, and does not do the outward-facing half twice.
* **One run owns one ticket.** Starting a second run against a ticket that already has a live
  one is refused, rather than racing it.
* **Reap.** A run whose process is gone is *findable* — with its worktree, branch and issue —
  instead of being a mystery worktree and a ticket stuck In Progress.

**Deliberately file-backed, not a database table.** The linear path runs with zero infra;
requiring Postgres to run one ticket would make the supervisor less useful than the thing it
supervises. The registry DB is the right home once a run spans processes (Mode B), and the
record shape here is what that table will hold. Writes are atomic (temp file + replace) so a
kill mid-write leaves the previous record readable rather than a truncated one.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

RunStatus = Literal["running", "parked", "done", "failed", "abandoned"]

# How long a record may go without a heartbeat before the reaper calls it abandoned. A stage
# can legitimately take minutes (codegen, a test suite), so this is generous: the cost of
# declaring a live run dead is worse than noticing a dead one late.
STALE_AFTER_SECONDS = 3600.0


@dataclass
class RunRecord:
    """One run's durable state. Everything a resume or a reaper needs, and nothing else."""

    run_id: str
    source: str
    status: RunStatus = "running"
    phase: str = ""
    issue_key: str = ""
    branch: str = ""
    worktree: str = ""
    pr_url: str = ""
    verdict: str = ""
    parked_reason: str = ""
    spent_usd: float = 0.0
    started_at: float = 0.0
    heartbeat_at: float = 0.0
    pid: int = 0
    live: bool = False
    artifacts_dir: str = ""

    @property
    def is_live_process(self) -> bool:
        """Whether the owning process still exists.

        A pid alone is not proof — pids are reused — so the heartbeat is what decides, and
        the pid check only rules out the obvious case of a process that is plainly gone.
        """
        if self.status != "running":
            return False
        if time.time() - self.heartbeat_at > STALE_AFTER_SECONDS:
            return False
        if not self.pid:
            return True
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # someone else's process, so it does exist
            return True
        return True


@dataclass
class RunStore:
    """Records on disk, one JSON file per run."""

    root: Path = field(default_factory=lambda: default_state_dir())

    def path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, record: RunRecord) -> None:
        """Atomic write: a kill mid-save leaves the previous record, never a half-written one."""
        self.root.mkdir(parents=True, exist_ok=True)
        record.heartbeat_at = time.time()
        payload = json.dumps(asdict(record), indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path_for(record.run_id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, run_id: str) -> RunRecord | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # unreadable record: treat as absent rather than crash a resume
        known = {f for f in RunRecord.__dataclass_fields__}
        return RunRecord(**{k: v for k, v in raw.items() if k in known})

    def all(self) -> list[RunRecord]:
        if not self.root.is_dir():
            return []
        records = [self.load(p.stem) for p in sorted(self.root.glob("*.json"))]
        return [r for r in records if r is not None]

    def active_for_issue(self, issue_key: str) -> RunRecord | None:
        """A run still holding this ticket, if any — the one-run-per-ticket guard."""
        if not issue_key:
            return None
        for record in self.all():
            if record.issue_key != issue_key or record.status not in ("running", "parked"):
                continue
            # Parked means waiting for a human, not finished — it still owns the ticket.
            if record.status == "parked" or record.is_live_process:
                return record
        return None

    def stale(self) -> list[RunRecord]:
        """Records claiming to be running whose process is gone — the reaper's input."""
        return [r for r in self.all() if r.status == "running" and not r.is_live_process]


def default_state_dir() -> Path:
    """Outside the repo, like run artifacts: state is not source, and markdown in the tree
    becomes ``Doc`` nodes."""
    base = os.getenv("SPINE_RUN_STATE") or str(Path(tempfile.gettempdir()) / "spine-runs")
    return Path(base) / "_state"


def render_runs(records: list[RunRecord]) -> str:
    """One table: which runs exist, what they hold, and whether anyone is still driving."""
    if not records:
        return "No runs recorded."
    lines = ["| Run | Status | Phase | Issue | Spent | Alive |", "|---|---|---|---|---|---|"]
    for r in sorted(records, key=lambda r: r.started_at, reverse=True):
        alive = "yes" if r.is_live_process else "no"
        lines.append(
            f"| {r.run_id} | {r.status} | {r.phase or '—'} | {r.issue_key or '—'} "
            f"| ${r.spent_usd:.2f} | {alive} |"
        )
    return "\n".join(lines)


def render_reap(records: list[RunRecord]) -> str:
    """What a dead run left behind. Reported, never cleaned up silently — a worktree may hold
    the only copy of work someone wants, and a ticket's status is an outward-facing write."""
    if not records:
        return "Nothing to reap — no run is claiming to be alive without a process."
    lines = [f"{len(records)} abandoned run(s):", ""]
    for r in records:
        lines.append(f"- **{r.run_id}** · last seen {_ago(r.heartbeat_at)} · phase {r.phase or '—'}")
        if r.issue_key:
            lines.append(f"    - issue `{r.issue_key}` may be left In Progress")
        if r.worktree:
            lines.append(f"    - worktree `{r.worktree}`")
        if r.branch:
            lines.append(f"    - branch `{r.branch}`")
        if r.pr_url:
            lines.append(f"    - PR {r.pr_url}")
    return "\n".join(lines)


def _ago(when: float) -> str:
    if not when:
        return "never"
    seconds = max(0, int(time.time() - when))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


__all__ = [
    "STALE_AFTER_SECONDS",
    "RunRecord",
    "RunStatus",
    "RunStore",
    "default_state_dir",
    "render_reap",
    "render_runs",
]
