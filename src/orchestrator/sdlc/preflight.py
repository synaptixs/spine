"""Preflight parity: the local quality gate must equal the CI quality gate.

Runs #9–#11 each opened a real PR and lost a full CI round-trip to a lint or
type error the pipeline never checked locally — the refine loop only iterated
on *test* failures, so anything else was a guaranteed late failure.

``SubprocessPreflightRunner`` closes that gap: after tests pass and before a
PR opens, it runs the exact CI bar — ``ruff check``, ``ruff format --check``,
``mypy`` — inside the worktree, using the repo's own config. Failures feed
the same refinement loop as test failures, so the model iterates locally
(seconds, free) instead of burning a CI run per lint rule.

Tools are invoked as ``python -m …`` (the worker's PATH may not expose the
console scripts) with the same sanitized env as the test runner. A worktree
without ``pyproject.toml`` (Block C's scratch mode) skips with a pass — there
is no configured bar to hold it to.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from orchestrator.sdlc.testrunner import _SECRET_ENV_PREFIXES

_MAX_OUTPUT_CHARS = 4000
_TOOL_TIMEOUT = 180.0

# (label, argv tail) — exactly what the repo's CI job runs.
_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check", ("ruff", "check")),
    ("ruff format", ("ruff", "format", "--check")),
    ("mypy", ("mypy",)),
)


# Parseable variants of the same checks, used only when a baseline is in play. The bar is
# unchanged — these are the same tools with the same repo config; only the output format
# differs, so findings can be counted instead of merely detected.
_JSON_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check", ("ruff", "check", "--output-format", "json")),
    ("ruff format", ("ruff", "format", "--check")),
    ("mypy", ("mypy", "--no-error-summary", "--show-error-codes")),
)

# A tool that cannot run at all — no config section, so it never sees a file. Distinguished
# from "ran and found nothing", which is the answer that matters.
_UNUSABLE_MARKERS: tuple[str, ...] = (
    "usage: mypy",
    "missing target module",
    "no files or directories",
    "error: unrecognized arguments",
)

# `path:line: error: message  [code]`
_MYPY_LINE = re.compile(r"^(?P<path>[^:]+):\d+:(?:\d+:)?\s+error:\s+.*\[(?P<code>[\w-]+)\]\s*$")
# ruff's diagnostic format points at the file on its own line: `   --> path:line:col`
_RUFF_ARROW = re.compile(r"^\s*-->\s+(?P<path>\S+?):\d+:\d+\s*$")


def _is_unusable(output: str) -> bool:
    """Did the tool fail to run at all, rather than run and report findings?

    `mypy` invoked with no arguments in a repo with no `[tool.mypy]` section prints its usage
    banner and exits — it never type-checked anything. Counting that as "clean" would inflate
    every result; treating it as a hard error would exclude most real repositories, which
    carry no such config. It is reported instead.
    """
    low = output.lower()
    return any(marker in low for marker in _UNUSABLE_MARKERS)


def _parse(label: str, output: str, root: Path) -> Counter[tuple[str, str, str]] | None:
    """Findings as (tool, path, code); None when the output cannot be understood."""
    counts: Counter[tuple[str, str, str]] = Counter()
    if label == "ruff check":
        text = output.strip()
        if not text:
            return counts
        try:
            for item in json.loads(text):
                rel = _relative(str(item.get("filename", "")), root)
                counts[(label, rel, str(item.get("code") or "?"))] += 1
        except (json.JSONDecodeError, TypeError, AttributeError):
            return None
        return counts
    if label == "ruff format":
        # No rule codes here, so the key degrades to the file: a file already unformatted
        # stays tolerated, a newly unformatted one fails.
        for line in output.splitlines():
            m = _RUFF_ARROW.match(line)
            if m:
                counts[(label, _relative(m["path"], root), "unformatted")] += 1
        return counts
    if label == "mypy":
        for line in output.splitlines():
            m = _MYPY_LINE.match(line.strip())
            if m:
                counts[(label, _relative(m["path"], root), m["code"])] += 1
        return counts
    return None


def _relative(path: str, root: Path) -> str:
    """Path relative to the worktree it was reported in.

    Load-bearing for the diff, not cosmetic. A baseline is captured once at the repository
    root while every ticket runs in its own temporary worktree, so absolute paths never
    match between the two and *every* finding would read as new. `ruff --output-format=json`
    reports absolute paths while `ruff format` reports relative ones, so both are normalised
    here rather than trusted.
    """
    p = Path(path.replace("\\", "/"))
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix().lstrip("./")


class PreflightBaselineError(RuntimeError):
    """A baseline could not be captured, so `mergeable` cannot be computed.

    Raised rather than degraded, because a missing baseline does not make the gate weaker —
    it makes every number downstream meaningless, and a silent pass reads exactly like a
    real result.
    """


@dataclass(frozen=True)
class Baseline:
    """What a repository's quality tools already report, before any change.

    `findings` counts `(tool, path, code)` triples. Line numbers are deliberately excluded
    from the key: inserting a function shifts every line beneath it, so a line-keyed baseline
    would report an untouched file as entirely new on any insertion. Counting `(path, code)`
    catches "this file gained another `attr-defined`" while ignoring "the same finding moved
    down twelve lines".
    """

    findings: Mapping[tuple[str, str, str], int]
    skipped: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.findings.values())

    def describe(self) -> str:
        parts = [f"{self.total} pre-existing finding(s)"]
        if self.skipped:
            parts.append(f"tools excluded (no config in target repo): {', '.join(self.skipped)}")
        return " · ".join(parts)


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of the local CI-parity checks."""

    passed: bool
    output: str = ""


@runtime_checkable
class PreflightRunner(Protocol):
    """Runs the repo's quality bar in a worktree."""

    async def run(self, *, path: str, baseline: Baseline | None = None) -> PreflightResult: ...


class StubPreflightRunner:
    """Always-pass preflight — unit tests and scratch-worktree mode."""

    async def run(self, *, path: str, baseline: Baseline | None = None) -> PreflightResult:
        _ = (path, baseline)
        return PreflightResult(passed=True, output="stub preflight")


class SubprocessPreflightRunner:
    """ruff check + ruff format --check + mypy, via the worker's interpreter."""

    def __init__(self, python: str | None = None) -> None:
        self._python = python or sys.executable

    async def run(self, *, path: str, baseline: Baseline | None = None) -> PreflightResult:
        root = Path(path)
        if not (root / "pyproject.toml").exists():
            return PreflightResult(passed=True, output="no pyproject.toml — preflight skipped")

        if baseline is None:
            # Unchanged behaviour: any finding fails. This is the production SDLC path, and
            # it is correct there — Spine holds itself to zero findings, so "new" and "all"
            # are the same set.
            env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
            failures: list[str] = []
            for label, tail in _CHECKS:
                rc, out = await self._exec(self._python, "-m", *tail, cwd=str(root), env=env)
                if rc != 0:
                    failures.append(f"--- {label} failed (exit {rc}) ---\n{out[-_MAX_OUTPUT_CHARS:]}")
            if failures:
                return PreflightResult(passed=False, output="\n".join(failures)[-_MAX_OUTPUT_CHARS:])
            return PreflightResult(passed=True, output="preflight green: ruff check, ruff format, mypy")

        # Baseline mode: judge the *change*, not the repository. A codebase carrying 518
        # pre-existing mypy errors (measured on ontomesh) would otherwise fail every ticket
        # before the model contributes a line, and the gate would report 0/50 in both arms —
        # a run that costs money and measures nothing.
        current, _ = await self._collect(root)
        new = Counter(current)
        new.subtract(baseline.findings)
        introduced = {k: n for k, n in new.items() if n > 0}
        if not introduced:
            note = f"preflight green vs baseline ({baseline.describe()})"
            return PreflightResult(passed=True, output=note)

        lines = [f"--- {sum(introduced.values())} NEW finding(s) vs baseline ---"]
        for tool, rel, code in sorted(introduced):
            lines.append(f"  {tool}: {rel} [{code}] x{introduced[(tool, rel, code)]}")
        return PreflightResult(passed=False, output="\n".join(lines)[-_MAX_OUTPUT_CHARS:])

    async def capture_baseline(self, *, path: str) -> Baseline:
        """What this repository already reports, before any change.

        Captured once per repository rather than per ticket: every ticket runs in a fresh
        worktree at the same commit, so a per-ticket baseline would repeat a full `mypy` run
        across the corpus for identical output.
        """
        root = Path(path)
        if not (root / "pyproject.toml").exists():
            raise PreflightBaselineError(
                f"{root} has no pyproject.toml. Preflight would silently pass every ticket, "
                "so `mergeable` would mean 'tests passed' while claiming to mean more."
            )
        findings, skipped = await self._collect(root)
        return Baseline(findings=dict(findings), skipped=skipped)

    async def _collect(self, root: Path) -> tuple[Counter[tuple[str, str, str]], tuple[str, ...]]:
        """Run each check and count its findings as (tool, path, code)."""
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        found: Counter[tuple[str, str, str]] = Counter()
        skipped: list[str] = []
        for label, tail in _JSON_CHECKS:
            rc, out = await self._exec(self._python, "-m", *tail, cwd=str(root), env=env)
            if _is_unusable(out):
                # The tool has no config in this repo, so it never saw a file. Excluded and
                # named, rather than hard-stopped (which would rule out most real
                # repositories) or silently counted as clean (which would inflate the gate).
                skipped.append(label)
                continue
            parsed = _parse(label, out, root)
            if parsed is None:
                raise PreflightBaselineError(
                    f"could not parse `{label}` output in {root} (exit {rc}). Without a "
                    "parseable baseline, new findings cannot be told from pre-existing ones."
                )
            found.update(parsed)
        return found, tuple(skipped)

    async def _exec(self, *argv: str, cwd: str, env: dict[str, str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=_TOOL_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, f"{argv[2] if len(argv) > 2 else argv[0]} timed out"
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, stdout_bytes.decode("utf-8", "replace")


__all__ = ["PreflightResult", "PreflightRunner", "StubPreflightRunner", "SubprocessPreflightRunner"]
