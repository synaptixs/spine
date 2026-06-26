"""Independent graders for the persona-skill measurement (P0).

The skill A/B can only show signal if the model is graded by something it did
**not** author. Today's benchmark acceptance is self-graded — the model writes
both the implementation and the tests that judge it — so a better *test* strategy
can't move a pass/fail the model controls. These are the independent graders that
fix that, kept deterministic and unit-testable (the subprocess shells are thin;
the decision logic is pure):

- **Held-out reference tests** — a hidden suite, never shown to the model, run
  against the model's *implementation*. This is the headroom that lets a skill
  show up: a thin solution that passes the model's own happy-path tests still
  fails the held-out edge/error/boundary cases.
- **Semgrep findings** — count security findings on the generated source (the
  ``security`` extra); lower is better, the independent signal for
  ``security-aware-coding``.
- **Symbol reuse** — did the change reuse existing package symbols, or introduce
  a parallel pattern? A pure check over the written source — the independent
  signal for ``convention-digest``.

Nothing here imports an LLM: every function is a deterministic grader so the
measurement (P2) rests on executable evidence, not a model judging a model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# Where held-out reference tests are dropped inside a worktree. Isolated from the
# model's own files so they neither pollute the fit grader nor get imported by it.
_HELD_OUT_DIR = "_heldout"
_DEFAULT_TIMEOUT = 300


@dataclass(frozen=True)
class HeldOutResult:
    """Outcome of running a ticket's hidden reference suite against the impl.

    ``ran`` is False when the ticket shipped no held-out tests (so ``passed`` is
    not a real signal — callers must check ``ran`` before trusting ``passed``).
    """

    ran: bool
    passed: bool
    output: str

    @property
    def is_signal(self) -> bool:
        """True only when a held-out suite actually ran — guards naive ``passed``."""
        return self.ran


def run_held_out_tests(
    workdir: Path,
    tests: Mapping[str, str],
    *,
    src_subdir: str = "src",
    timeout: int = _DEFAULT_TIMEOUT,
    python: str | None = None,
) -> HeldOutResult:
    """Run a hidden reference suite against the model's implementation in ``workdir``.

    The model never sees ``tests`` (filename → content); they're written into an
    isolated ``_heldout/`` dir, run with the worktree's ``src`` root on the path
    (mirroring the benchmark's own pytest invocation), then removed. Independent
    acceptance = this suite passes.

    Returns ``ran=False`` when ``tests`` is empty — there's no held-out signal to
    report, not a failure.
    """
    if not tests:
        return HeldOutResult(ran=False, passed=False, output="no held-out tests")

    held_dir = workdir / _HELD_OUT_DIR
    held_dir.mkdir(parents=True, exist_ok=True)
    try:
        paths: list[str] = []
        for name, content in tests.items():
            # Confine to the held-out dir — a ticket-authored filename is trusted
            # input here, but never let it escape the worktree regardless.
            target = (held_dir / name).resolve()
            if not target.is_relative_to(held_dir.resolve()):
                raise ValueError(f"held-out test path escapes the worktree: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            paths.append(str(target))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(workdir / src_subdir)
        proc = subprocess.run(
            [python or sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *paths],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(workdir),
            check=False,
        )
        out = (proc.stdout + proc.stderr).strip()
        return HeldOutResult(ran=True, passed=proc.returncode == 0, output=out)
    finally:
        shutil.rmtree(held_dir, ignore_errors=True)


def count_semgrep_findings(stdout: str) -> int:
    """Parse semgrep ``--json`` stdout → number of findings (pure).

    Tolerant of the noise semgrep prints around its JSON: scans for the object
    and counts ``results``. Unparseable output yields 0 — a grader must never
    crash a measurement run; a real scan failure is surfaced by the runner.
    """
    text = stdout.strip()
    if not text:
        return 0
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return 0
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return 0
    if not isinstance(payload, dict):
        return 0
    results = payload.get("results")
    return len(results) if isinstance(results, list) else 0


def semgrep_findings(
    paths: Iterable[Path],
    *,
    config: str = "auto",
    timeout: int = _DEFAULT_TIMEOUT,
) -> int | None:
    """Run semgrep over ``paths`` and return the finding count, or ``None``.

    Best-effort by design: ``None`` means semgrep isn't available (the
    ``security`` extra isn't installed) or the scan errored — the caller treats
    that as "no security signal this run", never a failed measurement. The
    finding-count parsing is the pure, unit-tested ``count_semgrep_findings``.
    """
    files = [str(p) for p in paths if p.exists()]
    if not files:
        return None
    try:
        proc = subprocess.run(
            ["semgrep", "scan", "--config", config, "--json", "--quiet", *files],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # semgrep exits non-zero when findings exist; only a missing/garbled JSON
    # body (a real tool error) should degrade to None.
    if not proc.stdout.strip():
        return None
    return count_semgrep_findings(proc.stdout)


_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M)


def reused_existing_symbols(source: str, *, package: str = "orchestrator") -> bool:
    """True when ``source`` imports from the existing ``package`` (pure).

    The convention/reuse signal for ``convention-digest``: a change that builds on
    the repo's own symbols imports them; a parallel reimplementation doesn't. This
    is the executable core of "reuse what exists" — it deliberately can't see
    *intent*, only whether the existing package was actually referenced.
    """
    for match in _IMPORT_RE.finditer(source):
        module = match.group(1)
        if module == package or module.startswith(f"{package}."):
            return True
    # A relative import (``from . import x`` / ``from .mod import y``) also reuses
    # the surrounding package rather than starting a parallel top-level module.
    return bool(re.search(r"^\s*from\s+\.", source, re.M))


def read_source(paths: Iterable[Path]) -> str:
    """Concatenate the readable text of ``paths`` (skips test files), for grading."""
    chunks: list[str] = []
    for p in paths:
        if not p.exists() or p.name.startswith("test_"):
            continue
        try:
            chunks.append(p.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


__all__ = [
    "HeldOutResult",
    "count_semgrep_findings",
    "read_source",
    "reused_existing_symbols",
    "run_held_out_tests",
    "semgrep_findings",
]
