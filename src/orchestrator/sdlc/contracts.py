"""Shared SDLC adapter contracts, independent of language implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import Field, dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable


class ToolchainLayout(Protocol):
    """Only the layout metadata the registry itself consumes."""

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]

    @property
    def mode(self) -> str: ...
    @property
    def build_tool(self) -> str: ...
    @property
    def source_dir(self) -> str: ...


@dataclass(frozen=True)
class TestRunResult:
    """Outcome of running a worktree's tests."""

    __test__ = False  # not a pytest test class despite the Test* name

    passed: bool
    returncode: int
    output: str = ""
    # What failed, as the runner could identify it from the WHOLE output — pytest node ids
    # (`tests/x.py::test_a`) and, for a collection error, the file (`tests/y.py`). Empty when
    # the runner does not parse its output, or found nothing it could name. Lets a run tell
    # the failures that predate its change from the ones it caused (`sdlc/baseline.py`).
    problems: tuple[str, ...] = ()


@runtime_checkable
class TestRunner(Protocol):
    """Runs the tests in a worktree, returning pass/fail + captured output."""

    async def run(self, *, path: str) -> TestRunResult: ...


@runtime_checkable
class TestEnvironment(Protocol):
    """An interpreter (and its installed deps) to run a worktree's tests with."""

    @property
    def python(self) -> str: ...
    async def ensure(self, worktree: Path | str) -> None: ...
    async def install(self, packages: list[str]) -> bool: ...
    def describe(self) -> str: ...


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
