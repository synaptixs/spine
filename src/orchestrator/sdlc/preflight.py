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
import os
import sys
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


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of the local CI-parity checks."""

    passed: bool
    output: str = ""


@runtime_checkable
class PreflightRunner(Protocol):
    """Runs the repo's quality bar in a worktree."""

    async def run(self, *, path: str) -> PreflightResult: ...


class StubPreflightRunner:
    """Always-pass preflight — unit tests and scratch-worktree mode."""

    async def run(self, *, path: str) -> PreflightResult:
        _ = path
        return PreflightResult(passed=True, output="stub preflight")


class SubprocessPreflightRunner:
    """ruff check + ruff format --check + mypy, via the worker's interpreter."""

    def __init__(self, python: str | None = None) -> None:
        self._python = python or sys.executable

    async def run(self, *, path: str) -> PreflightResult:
        root = Path(path)
        if not (root / "pyproject.toml").exists():
            return PreflightResult(passed=True, output="no pyproject.toml — preflight skipped")

        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        failures: list[str] = []
        for label, tail in _CHECKS:
            rc, out = await self._exec(self._python, "-m", *tail, cwd=str(root), env=env)
            if rc != 0:
                failures.append(f"--- {label} failed (exit {rc}) ---\n{out[-_MAX_OUTPUT_CHARS:]}")
        if failures:
            return PreflightResult(passed=False, output="\n".join(failures)[-_MAX_OUTPUT_CHARS:])
        return PreflightResult(passed=True, output="preflight green: ruff check, ruff format, mypy")

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
