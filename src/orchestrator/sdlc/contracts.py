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
    @property
    def project_dir(self) -> str: ...


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


@dataclass(frozen=True)
class RequirementResult:
    """Outcome of one check from a project's required-behavior manifest."""

    requirement_id: str
    passed: bool
    required: bool
    command: tuple[str, ...]
    output: str = ""
    # True when the check could not run at all (missing command, timeout) rather than ran
    # and failed — SSPN-118/D21: distinguishes an environment problem refine cannot fix from
    # a real behavioral gap, structurally (by exit code), not by sniffing output text.
    environment_blocked: bool = False


@dataclass(frozen=True)
class RequiredBehaviorResult:
    """Outcome of a project's required-behavior manifest, or the fact it has none.

    A project without ``.spine/required-behavior.yaml`` is unverified, not failed —
    ``passed`` is ``True`` with an empty ``items`` and an explicit ``output`` saying so,
    mirroring ``SubprocessPreflightRunner``'s own "no pyproject.toml" self-skip. ``passed``
    only considers items where ``required`` is true.
    """

    passed: bool
    items: tuple[RequirementResult, ...] = ()
    output: str = ""


@runtime_checkable
class RequiredBehaviorRunner(Protocol):
    """Runs a project's declared required-behavior checks, if it has any configured."""

    async def run(self, *, path: str) -> RequiredBehaviorResult: ...
