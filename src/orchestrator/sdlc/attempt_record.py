"""A record of what each codegen attempt was sent back and what became of it (B72).

Nine benchmark runs ended at ``[refine] [] - <a fix described in words>``, and nothing written
down could say why: the raw reply, which files ``apply_files`` dropped and the failing test
output were kept nowhere. This is the instrument for that, not a fix. It changes no value any
stage returns and no decision the loop makes.

Two things are written under the run's ``record_dir`` (the autorun artifacts directory):

* ``attempts/<stage>-<n>.json`` — one per model call of a codegen stage, retries included. ``n``
  counts per stage and continues past files already there, so a resumed run adds to the record
  instead of replacing it. It holds the raw reply (capped, and says so), what the model said
  beside it and why it stopped (``meta``), what the stage was asked to act on (``context``), the
  summary, the outcome, each file that landed or was dropped with a reason, and the hash of each
  named file before and after the pass.
* ``run-tests-<n>.txt`` — one test run's output, the same ``n`` as ``[run_tests #n]``. A run's
  failure is at the end of its output, so when it is over the cap the *tail* is kept.

Everything here is best-effort and never raises: a record that cannot be written is one log line,
so recording can never change how a run ends. Values of environment variables whose names say
KEY, TOKEN, SECRET, PASSWORD or CREDENTIAL are replaced by ``[redacted]`` before anything is
written, because a raw reply or a test's output can echo them. ``SDLC_ATTEMPT_RECORD=0`` (or
``false``, ``off``, ``no``) turns it off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The reply is the model's own JSON, so a long file inside it makes it long. 32 KiB shows the
# shape of any reply and the start of every file; the point is *what kind* of reply it was.
MAX_REPLY_BYTES = 32 * 1024
# 64 KiB of the end of a test run: enough for the failures and the summary line of a large suite.
MAX_TEST_OUTPUT_BYTES = 64 * 1024
# What a stage was asked to act on (a failure, a reviewer's list): enough to read, not to archive.
MAX_CONTEXT_BYTES = 16 * 1024

_ENV = "SDLC_ATTEMPT_RECORD"
_OFF = frozenset({"0", "false", "off", "no"})
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
# A value shorter than this is as likely to be ordinary text as a secret, and redacting it would
# wreck the record; every real key and token is longer.
_MIN_SECRET_LENGTH = 8


def enabled() -> bool:
    """On unless ``SDLC_ATTEMPT_RECORD`` is ``0``, ``false``, ``off`` or ``no``."""
    return os.environ.get(_ENV, "").strip().lower() not in _OFF


def scrub(text: str) -> str:
    """Replace the value of every credential-named environment variable with ``[redacted]``."""
    for name, value in os.environ.items():
        if len(value) >= _MIN_SECRET_LENGTH and _SECRET_NAME.search(name):
            text = text.replace(value, "[redacted]")
    return text


def file_hash(path: Path) -> str | None:
    """sha256 of a file's bytes, or ``None`` when it is absent or unreadable."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


@dataclass
class AttemptTrace:
    """What one pass of ``apply_files`` did, filled in by it as it goes.

    Handed in by the caller and written only by ``apply_files``; it never influences what that
    function writes, refuses or returns.
    """

    # The reply's own summary, before ``apply_files`` appends any refusal notes to it.
    summary: str = ""
    # True when the reply was not a JSON object at all and the stage allowed an empty answer.
    unparseable: bool = False
    # (path as the reply named it, reason). A reason is one short token: ``edit_failed``,
    # ``syntax``, ``refused_unnamed_edit``, ``weakened_test``, ``skipped_existing_file``, ...
    dropped: list[tuple[str, str]] = field(default_factory=list)
    # Absolute paths the pass wrote, appended to as it goes: the list ``apply_files`` itself
    # uses, so it is right whether the pass returns or raises.
    live_written: list[str] = field(default_factory=list)
    # The same paths relative to the worktree, set by ``settle``.
    written: list[str] = field(default_factory=list)
    # path as the reply named it -> file hash before the pass touched it.
    before: dict[str, str | None] = field(default_factory=dict)
    # The same keys, hashed after the pass (and after the tidy-up ruff runs on what it wrote).
    after: dict[str, str | None] = field(default_factory=dict)
    # Where each named path resolved to, so the second hash reads the file the first one did.
    targets: dict[str, Path] = field(default_factory=dict)
    # target -> its hash before the pass first touched it, so one file spelled two ways is one file.
    _first: dict[Path, str | None] = field(default_factory=dict)

    def drop(self, rel: str, reason: str) -> None:
        self.dropped.append((rel, reason))

    def note_before(self, rel: str, target: Path) -> None:
        if rel in self.before:
            return
        if target not in self._first:
            self._first[target] = file_hash(target)
        self.before[rel] = self._first[target]
        self.targets[rel] = target

    def settle(self, root: Path) -> None:
        """Take the after hashes and the relative written paths now that the pass is over."""
        base = root.resolve()
        relative: list[str] = []
        for written in self.live_written:
            try:
                relative.append(Path(written).resolve().relative_to(base).as_posix())
            except (OSError, ValueError):
                relative.append(written)
        self.written = list(dict.fromkeys(relative))
        for rel, target in self.targets.items():
            self.after[rel] = file_hash(target)


def _safe(text: str) -> str:
    """``text`` with anything UTF-8 cannot encode (a lone surrogate) replaced, then scrubbed."""
    return scrub(text.encode("utf-8", errors="replace").decode("utf-8"))


def _cap(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def highest_attempt(record_dir: Path, stage: str) -> int:
    """The largest ``n`` among ``attempts/<stage>-<n>.json`` already written, or 0."""
    best = 0
    try:
        for path in (record_dir / "attempts").glob(f"{stage}-*.json"):
            tail = path.stem[len(stage) + 1 :]
            if tail.isdigit():
                best = max(best, int(tail))
    except OSError:
        pass
    return best


def write_attempt(
    record_dir: Path | None,
    *,
    stage: str,
    attempt: int,
    reply: str,
    outcome: str,
    trace: AttemptTrace,
    error: str = "",
    context: str = "",
    meta: dict[str, Any] | None = None,
) -> Path | None:
    """Write ``attempts/<stage>-<attempt>.json``; ``None`` when off or when it cannot be written."""
    if record_dir is None or not enabled():
        return None
    try:
        shown, truncated = _cap(_safe(reply), MAX_REPLY_BYTES)
        seen, _ = _cap(_safe(context), MAX_CONTEXT_BYTES)
        files: list[dict[str, str]] = [{"path": p, "outcome": "written", "reason": ""} for p in trace.written]
        files += [{"path": p, "outcome": "dropped", "reason": r} for p, r in trace.dropped]
        hashes = {p: {"before": trace.before.get(p), "after": trace.after.get(p)} for p in trace.before}
        payload: dict[str, Any] = {
            "stage": stage,
            "attempt": attempt,
            "outcome": outcome,
            "summary": _safe(trace.summary),
            "context": seen,
            "files": files,
            "hashes": hashes,
            "meta": {k: (_safe(v) if isinstance(v, str) else v) for k, v in (meta or {}).items()},
            "reply": shown,
            "reply_truncated": truncated,
            "error": _safe(error),
        }
        directory = record_dir / "attempts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stage}-{attempt}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001 — the contract above: a record never reaches the run
        logger.warning("sdlc.codegen.attempt_record_failed stage=%s attempt=%d error=%s", stage, attempt, exc)
        return None


def record_test_run(record_dir: Path | None, n: int, output: str) -> Path | None:
    """Keep one test run's output as ``run-tests-<n>.txt``; ``None`` when off or unwritable.

    Over the cap the tail is kept, headed by a line saying how much of the start was dropped. A
    file already there (a resumed run counts from 1 again) is never replaced: the new one is
    ``run-tests-<n>.<k>.txt``.
    """
    if record_dir is None or not enabled():
        return None
    try:
        text = _safe(output)
        raw = text.encode("utf-8")
        if len(raw) > MAX_TEST_OUTPUT_BYTES:
            kept = raw[-MAX_TEST_OUTPUT_BYTES:].decode("utf-8", errors="ignore")
            text = f"[truncated: {len(raw) - len(kept.encode('utf-8'))} bytes omitted from the start]\n{kept}"
        record_dir.mkdir(parents=True, exist_ok=True)
        path = record_dir / f"run-tests-{n}.txt"
        again = 2
        while path.exists():
            path = record_dir / f"run-tests-{n}.{again}.txt"
            again += 1
        path.write_text(text, encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001 — the contract above: a record never reaches the run
        logger.warning("sdlc.codegen.test_run_record_failed n=%d error=%s", n, exc)
        return None
