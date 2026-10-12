"""A record of what each codegen attempt was sent back and what became of it (B72).

Nine benchmark runs ended at ``[refine] [] - <a fix described in words>``, and nothing written
down could say why: the raw reply, which files ``apply_files`` dropped and the failing test
output were kept nowhere. This is the instrument for that, not a fix. It changes no value any
stage returns and no decision the loop makes.

Two things are written under the run's ``record_dir`` (the autorun artifacts directory):

* ``attempts/<stage>-<n>.json`` — one per model call of a codegen stage, retries included. ``n``
  counts per stage. It holds the raw reply (capped, and says so), the summary, the outcome, each
  file that landed or was dropped with the reason, and the hash of each named file before and
  after the pass.
* ``run-tests-<n>.txt`` — one test run's output, the same ``n`` as ``[run_tests #n]``. A run's
  failure is at the end of its output, so when it is over the cap the *tail* is kept.

Everything here is best-effort. A record that cannot be written is one log line, never an
exception, so recording can never change how a run ends. ``SDLC_ATTEMPT_RECORD=0`` turns it off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The reply is the model's own JSON, so a long file inside it makes it long. 32 KiB shows the
# shape of any reply and the start of every file; the point is *what kind* of reply it was.
MAX_REPLY_BYTES = 32 * 1024
# 64 KiB of the end of a test run: enough for the failures and the summary line of a large suite.
MAX_TEST_OUTPUT_BYTES = 64 * 1024

_ENV = "SDLC_ATTEMPT_RECORD"


def enabled() -> bool:
    """On unless ``SDLC_ATTEMPT_RECORD=0``."""
    return os.environ.get(_ENV, "").strip() != "0"


def file_hash(path: Path) -> str | None:
    """sha256 of a file's bytes, or ``None`` when it is absent or unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass
class AttemptTrace:
    """What one pass of ``apply_files`` did, filled in by it as it goes.

    Handed in by the caller and written only by ``apply_files``; it never influences what that
    function writes, refuses or returns.
    """

    # The reply's own summary, before ``apply_files`` appends any refusal notes to it.
    summary: str = ""
    # (path as the reply named it, reason). A reason is one short token: ``edit_failed``,
    # ``syntax``, ``refused_unnamed_edit``, ``weakened_test``, ``skipped_existing_file``, ...
    dropped: list[tuple[str, str]] = field(default_factory=list)
    # Paths (worktree-relative) the pass wrote.
    written: list[str] = field(default_factory=list)
    # path as the reply named it -> file hash before the pass touched it.
    before: dict[str, str | None] = field(default_factory=dict)
    # The same keys, hashed after the pass (and after the tidy-up ruff runs on what it wrote).
    after: dict[str, str | None] = field(default_factory=dict)
    # Where each named path resolved to, so the second hash reads the file the first one did.
    targets: dict[str, Path] = field(default_factory=dict)

    def drop(self, rel: str, reason: str) -> None:
        self.dropped.append((rel, reason))

    def note_before(self, rel: str, target: Path) -> None:
        if rel not in self.before:
            self.before[rel] = file_hash(target)
            self.targets[rel] = target

    def settle(self) -> None:
        """Hash every named file now that the pass is over."""
        for rel, target in self.targets.items():
            self.after[rel] = file_hash(target)


def _cap(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def write_attempt(
    record_dir: Path | None,
    *,
    stage: str,
    attempt: int,
    reply: str,
    outcome: str,
    trace: AttemptTrace,
    error: str = "",
) -> Path | None:
    """Write ``attempts/<stage>-<attempt>.json``; ``None`` when off or when it cannot be written."""
    if record_dir is None or not enabled():
        return None
    shown, truncated = _cap(reply, MAX_REPLY_BYTES)
    files: list[dict[str, str]] = [{"path": p, "outcome": "written", "reason": ""} for p in trace.written]
    files += [{"path": p, "outcome": "dropped", "reason": r} for p, r in trace.dropped]
    hashes = {p: {"before": trace.before.get(p), "after": trace.after.get(p)} for p in trace.before}
    payload: dict[str, Any] = {
        "stage": stage,
        "attempt": attempt,
        "outcome": outcome,
        "summary": trace.summary,
        "files": files,
        "hashes": hashes,
        "reply": shown,
        "reply_truncated": truncated,
        "error": error,
    }
    try:
        directory = record_dir / "attempts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stage}-{attempt}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("sdlc.codegen.attempt_record_failed stage=%s attempt=%d error=%s", stage, attempt, exc)
        return None


def record_test_run(record_dir: Path | None, n: int, output: str) -> Path | None:
    """Keep one test run's output as ``run-tests-<n>.txt``; ``None`` when off or unwritable.

    Over the cap the tail is kept, headed by a line saying how much of the start was dropped.
    """
    if record_dir is None or not enabled():
        return None
    raw = output.encode("utf-8")
    text = output
    if len(raw) > MAX_TEST_OUTPUT_BYTES:
        kept = raw[-MAX_TEST_OUTPUT_BYTES:].decode("utf-8", errors="ignore")
        text = f"[truncated: {len(raw) - len(kept.encode('utf-8'))} bytes omitted from the start]\n{kept}"
    try:
        record_dir.mkdir(parents=True, exist_ok=True)
        path = record_dir / f"run-tests-{n}.txt"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("sdlc.codegen.test_run_record_failed n=%d error=%s", n, exc)
        return None
