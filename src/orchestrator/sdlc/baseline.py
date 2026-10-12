"""Judge a run by the failures it caused, not the ones the repository already had (CB-764).

**Why this exists.** A Python ticket on an AWS SAM repository failed six times in a row with
``rc=2``. The repository's own ``tests/`` held four files that could not import in a fresh
environment — two needed ``boto3``/``langchain``, two a ``DB_HOST`` variable — and pytest
stopped at collection before running anything. The run's new test passed 4/4 when run alone;
refine was handed the collection errors anyway and spent every attempt, twice rewriting working
scraper logic, on a failure no edit could fix.

Two measures, both deterministic and model-free:

* **A baseline.** Before any code is generated the suite runs once on the untouched worktree,
  and what fails there is recorded (``take_baseline``). ``BaselineAwareRunner`` then treats a
  later run whose every failure is on that list as green — pre-existing failures are reported,
  never counted — and tells refine which failures are not its to fix when there are new ones.
  Because it wraps the runner itself, the retry, coverage and mutation probes all see the same
  judgement.
* **An environment stop.** Without a baseline (``SDLC_TEST_BASELINE=0``, or a baseline that could
  not be read), a failure made only of collection errors in files this run never wrote, naming a
  module the repository does not contain, is a missing dependency. ``environment_blocker`` says so,
  and the loop stops instead of asking a model to edit code over it.

Only failures a runner can *name* take part: ``TestRunResult.problems`` is filled by the pytest
runner today. A runner that names nothing is passed through unchanged, as before.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from orchestrator.sdlc.contracts import TestRunner, TestRunResult
from orchestrator.sdlc.diagnostics import dotnet_blocker, is_collection_error, missing_modules

#: pytest's "tests ran and some failed" exit code. Only then is the summary a complete list:
#: 2 (interrupted), 3 (internal error), 4 (usage) and 5 (nothing collected) mean it is not.
_TESTS_FAILED = 1
#: How many pre-existing failures a message names before summarising the rest.
_NAMED = 6


def baseline_enabled() -> bool:
    """``SDLC_TEST_BASELINE=0`` turns the pre-change suite run off (it costs one run)."""
    return (os.getenv("SDLC_TEST_BASELINE") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _named(items: Iterable[str]) -> str:
    listed = list(items)
    more = f" (+{len(listed) - _NAMED} more)" if len(listed) > _NAMED else ""
    return ", ".join(listed[:_NAMED]) + more


class BaselineAwareRunner:
    """A ``TestRunner`` that passes a run whose every failure was already failing before it."""

    def __init__(self, inner: TestRunner, before: frozenset[str]) -> None:
        self._inner = inner
        self._before = before
        # A runner that can leave old failures out of the text refine reads (.NET, D7) is told
        # what they are; one that cannot (pytest) is judged exactly as before.
        know = getattr(inner, "know_baseline", None)
        if callable(know):
            know(before)

    async def run(self, *, path: str) -> TestRunResult:
        result = await self._inner.run(path=path)
        problems = _problems(result)
        if result.passed or result.returncode != _TESTS_FAILED or not problems:
            return result
        old = [p for p in problems if p in self._before]
        if not old:
            return result
        new = [p for p in problems if p not in self._before]
        if not new:
            note = f"[baseline] every failure predates this change and is not counted: {_named(old)}\n"
            return TestRunResult(True, result.returncode, note + result.output, problems)
        # Said first, so refine does not spend an attempt on a failure it did not cause.
        note = (
            f"[baseline] NOT YOURS — these failed before this change; do not try to fix them: {_named(old)}\n"
            f"[baseline] caused by this change: {_named(new)}\n"
        )
        # A fresh result, not `dataclasses.replace`: a runner may hand back any object with
        # these attributes, and several test doubles do.
        return TestRunResult(False, result.returncode, note + result.output, problems, _failure(result))


#: `TestRunResult.failure` values that stop a run before any code is generated (B73).
BASELINE_STOP_FAILURES: frozenset[str] = frozenset({"build", "restore", "feed", "sdk"})
#: ... and the ones that stop it mid-run. Narrower: after a change, a package the model added
#: can fail to resolve and a test it wrote can hang, and refine can fix both.
MIDRUN_STOP_FAILURES: frozenset[str] = frozenset({"feed", "sdk", "timeout"})


@dataclass(frozen=True)
class BaselineCheck:
    """What the pre-change run established: the failures that predate the change, or why to stop."""

    problems: frozenset[str] | None
    stop: str = ""


def _failure(result: TestRunResult) -> str:
    # Tolerant, like `_problems`: a runner or test double that predates the field has no cause.
    return str(getattr(result, "failure", "") or "")


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _blocker_detail(result: TestRunResult) -> str:
    blocker = dotnet_blocker(result.output)
    return blocker.detail if blocker else _first_line(result.output)


async def check_baseline(runner: TestRunner, path: str, emit: Callable[[str], None]) -> BaselineCheck:
    """Run the suite on the untouched worktree: what already fails, or why the run must not start.

    Two things end a run here (B73), both because a baseline that is not green *and clean* cannot
    be told from the change: the build does not compile (no test can run, so "every failure
    predates the change" would pass a run whose tests never executed) and the environment cannot
    build it at all (a NuGet feed, a missing SDK). A timeout, or any run the runner could not read,
    stops nothing — the run is judged as it was before there was a baseline.
    """
    result = await runner.run(path=path)
    if result.passed:
        emit("[baseline] the suite passes before any change")
        return BaselineCheck(frozenset())
    failure = _failure(result)
    if failure in BASELINE_STOP_FAILURES:
        return BaselineCheck(None, _baseline_stop(result, failure))
    problems = _problems(result)
    if failure == "timeout" or result.returncode != _TESTS_FAILED or not problems:
        why = "timed out" if failure == "timeout" else f"rc={result.returncode}"
        emit(f"[baseline] could not read the pre-change suite ({why}) — judging every failure")
        return BaselineCheck(None)
    emit(
        f"[baseline] {len(problems)} failure(s) predate this change and will not count "
        f"against it: {_named(problems)}"
    )
    return BaselineCheck(frozenset(problems))


def _baseline_stop(result: TestRunResult, failure: str) -> str:
    escape = "set SDLC_TEST_BASELINE=0 to skip this check"
    if failure == "build":
        marker = "--- end of the build output ---"
        errors = result.output.split(marker)[0].strip() if marker in result.output else result.output[:1500]
        return (
            "VERDICT: BLOCKED — the repository's build is red before any change, so no test can "
            f"run and nothing was generated.\n{errors}\nFix the base branch, or {escape}."
        )
    return (
        "VERDICT: BLOCKED — the environment cannot build this repository before any change "
        f"({failure}): {_blocker_detail(result)}\nFix the environment, or {escape}."
    )


def midrun_blocker(result: TestRunResult) -> str:
    """Why a run under way should stop rather than ask refine, or ``""``.

    Narrower than the baseline's stops (D10): a package the model added can fail to resolve and a
    test it wrote can hang, and refine can fix both, so only what the change cannot have caused
    qualifies — a feed that cannot be reached, a missing SDK, the run's time limit.
    """
    failure = _failure(result)
    if result.passed or failure not in MIDRUN_STOP_FAILURES:
        return ""
    return f"the environment, not the change, failed this run ({failure}): {_blocker_detail(result)}"


async def take_baseline(runner: TestRunner, path: str, emit: Callable[[str], None]) -> frozenset[str] | None:
    """Run the suite on the untouched worktree; the failures it names, or ``None`` if unreadable.

    An empty set is a clean baseline. ``None`` means the run said nothing usable — a runner that
    names no problems, or a pytest exit that is not "some tests failed" — and the caller then
    judges every failure as before. A run that must not start at all is ``check_baseline``'s.
    """
    return (await check_baseline(runner, path, emit)).problems


def _repo_has_module(root: Path, module: str) -> bool:
    return any((base / module).is_dir() or (base / f"{module}.py").is_file() for base in (root, root / "src"))


def _problems(result: TestRunResult) -> tuple[str, ...]:
    # Tolerant, like every other reader of a run result here: a runner (or a test double) that
    # predates the field returns an object without it, which reads as "named nothing".
    return tuple(getattr(result, "problems", ()) or ())


def names_problems(runner: object) -> bool:
    """True for a runner that fills ``TestRunResult.problems`` — only then is a baseline useful."""
    return bool(getattr(runner, "names_problems", False))


def environment_blocker(result: TestRunResult, authored: Iterable[str], root: Path) -> str:
    """Why ``result`` is an environment problem refine cannot fix, or ``""``.

    Every failure must be a collection error, none in a file this run wrote, and the output must
    name a missing module the repository does not itself contain — a dependency, not a file the
    change broke. A collection error the change *caused* (it broke a module an old test imports)
    names a repository module, so it still goes to refine.
    """
    problems = _problems(result)
    if result.passed or not problems:
        return ""
    if not all(is_collection_error(p) for p in problems):
        return ""
    resolved_root = root.resolve()
    written = set()
    for f in authored:
        try:
            written.add(Path(f).resolve().relative_to(resolved_root).as_posix())
        except ValueError:
            written.add(Path(f).as_posix())
    if any(p in written for p in problems):
        return ""
    absent = [m for m in missing_modules(result.output) if not _repo_has_module(root, m)]
    if not absent:
        return ""
    return (
        f"every failure is a test file this run did not write that cannot be imported "
        f"({_named(problems)}), missing {', '.join(absent)} — an environment problem, not "
        "one editing code can fix. Declare the dependency in the project, set "
        "SDLC_AUTOHEAL_UNLISTED=1 to let the run install it, or keep SDLC_TEST_BASELINE on so "
        "pre-existing failures are not counted"
    )


__all__ = [
    "BASELINE_STOP_FAILURES",
    "MIDRUN_STOP_FAILURES",
    "BaselineAwareRunner",
    "BaselineCheck",
    "check_baseline",
    "midrun_blocker",
    "baseline_enabled",
    "environment_blocker",
    "names_problems",
    "take_baseline",
]
