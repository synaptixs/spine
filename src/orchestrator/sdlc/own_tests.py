"""Which of a failing run's tests are the ones the run wrote itself (SSPN-227).

The refine loop releases a change only when the whole suite is green, and the suite includes the tests the
model wrote for the change. REPLAY1 (SSPN-226, official grader) found that for 14 of 23 runs stopped on those
tests the model's *first* patch already resolved: the loop kept rewriting correct code to satisfy a test that
asserted something false, was built on a missing test app, or needed a package the runner does not have.

This module is the reading half of the rescue and nothing else. It looks at a failing run's output and says
whether **every** failing test is one the run wrote, and whether an existing test ran alongside. It decides
nothing and touches no file; ``feature_runner`` withdraws the files and re-runs. It is deliberately
conservative, because the only thing it can do is let a run that stops today release a change instead:

* a failing test it cannot name is not the run's own, so a runner whose output carries no test names makes the
  rescue inert (``CommandTestRunner`` sets ``names_problems = False``, so the names are parsed from the output
  here, not taken from ``TestRunResult.problems``);
* a name counts as the run's own only when a **test file** the run wrote defines it (or, for a module that
  could not be imported, is named after it); a production file never vouches for a name;
* "an existing test ran" means the run executed more tests than the run wrote. When the output has no
  summary, or the run's own tests may be counted against it, the answer is no.

``SDLC_OWN_TEST_RESCUE=0`` turns the rescue off, as ``SDLC_REFINE_PROGRESS=0`` does for the stall stop.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from orchestrator.sdlc.testpaths import is_test_path

__all__ = [
    "OwnTestVerdict",
    "classify",
    "count_test_functions",
    "enabled",
    "executed_tests",
    "failing_test_names",
]

# unittest / Django:   FAIL: test_x (module.Class)      ERROR: test_x (unittest.loader._FailedTest)
_UNITTEST_LINE = re.compile(r"(?m)^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)")
# pytest short summary:   FAILED tests/test_x.py::test_a - assert ...      ERROR tests/test_y.py - ImportError
# (unittest's closing "FAILED (failures=1)" starts with a parenthesis, which a pytest id never does.)
_PYTEST_LINE = re.compile(r"(?m)^(?:FAILED|ERROR) ([^\s(]\S*?)(?: - .*)?$")
_RAN = re.compile(r"(?m)^Ran (\d+) tests?\b")
_PYTEST_COUNTS = re.compile(r"(\d+) (?:passed|failed|xfailed|xpassed)\b")
_PYTEST_SUMMARY = re.compile(r"(?m)^=*\s*\d+ \w+.* in \d+(?:\.\d+)?s\b.*$")
_TEST_DEF = re.compile(r"(?m)^\s*(?:async\s+)?def\s+test\w*\s*\(")


def enabled() -> bool:
    """On unless ``SDLC_OWN_TEST_RESCUE=0``."""
    return os.getenv("SDLC_OWN_TEST_RESCUE", "1").strip() != "0"


def failing_test_names(output: str, problems: Sequence[str] = ()) -> tuple[str, ...]:
    """What the run reports as failing, once each, in order: the runner's own ``problems`` first, then the
    names read from the output (pytest's short summary, unittest's ``FAIL:``/``ERROR:`` lines)."""
    names: list[str] = [str(p).strip() for p in problems if str(p).strip()]
    names.extend(m.group(1) for m in _PYTEST_LINE.finditer(output))
    names.extend(m.group(1) for m in _UNITTEST_LINE.finditer(output))
    return tuple(dict.fromkeys(names))


def executed_tests(output: str) -> int | None:
    """How many tests the run executed, or ``None`` when the output does not say.

    unittest prints ``Ran N tests``; pytest prints ``1 failed, 5 passed, 1 warning in 0.8s`` (a warning or a
    skip is not a test that ran).
    """
    ran = _RAN.findall(output)
    if ran:
        return int(ran[-1])
    summaries = _PYTEST_SUMMARY.findall(output)
    if summaries:
        return sum(int(n) for n in _PYTEST_COUNTS.findall(summaries[-1]))
    return None


def count_test_functions(source: str) -> int:
    """The ``def test…`` functions a file defines (a lower bound on what it runs; Python style)."""
    return len(_TEST_DEF.findall(source))


@dataclass(frozen=True)
class OwnTestVerdict:
    #: Every failing test is one a test file the run wrote defines. False when none could be named.
    own_only: bool
    #: The test files holding the run's own failing tests, sorted: what a withdrawal would remove.
    files: tuple[str, ...]
    #: The failing tests, as the run named them.
    names: tuple[str, ...]
    #: At least one test that is not the run's own was executed (so something existing is green).
    existing_executed: bool
    #: Why not, when the answer is no; empty when ``rescuable``.
    reason: str

    @property
    def rescuable(self) -> bool:
        return self.own_only and self.existing_executed


def _defines(source: str, name: str) -> bool:
    return re.search(rf"(?m)^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(", source) is not None


def classify(
    *, output: str, problems: Sequence[str], authored: Mapping[str, str], own_test_count: int
) -> OwnTestVerdict:
    """Whether the failures in ``output`` are all the run's own tests.

    ``authored`` maps every path the run wrote to its text; only test paths can vouch for a name.
    ``own_test_count`` is how many test functions the run wrote, to tell whether anything else executed.
    """
    names = failing_test_names(output, problems)
    tests = {p.replace("\\", "/"): text for p, text in authored.items() if is_test_path(p)}
    if not names:
        return OwnTestVerdict(False, (), (), False, "the output names no failing test")

    files: list[str] = []
    foreign: list[str] = []
    for name in names:
        owner = _owner(name, tests)
        if owner is None:
            foreign.append(name)
        else:
            files.append(owner)
    held = tuple(sorted(dict.fromkeys(files)))
    if foreign:
        return OwnTestVerdict(
            False, held, names, False, "failing test(s) the run did not write: " + ", ".join(foreign)
        )

    executed = executed_tests(output)
    existing = executed is not None and executed - own_test_count >= 1
    reason = "" if existing else "no existing test executed alongside the run's own"
    return OwnTestVerdict(True, held, names, existing, reason)


def _owner(name: str, tests: Mapping[str, str]) -> str | None:
    """The test file the run wrote that this failing test belongs to, or ``None``."""
    if "::" in name:  # pytest node id: the file part must be one the run wrote
        file = name.split("::", 1)[0].replace("\\", "/")
        return file if file in tests else None
    if name.endswith(".py") or "/" in name:  # pytest: a file that failed to collect
        file = name.replace("\\", "/")
        return file if file in tests else None
    for path, text in tests.items():  # unittest: a bare test name defined in a file the run wrote
        if _defines(text, name):
            return path
    for path in tests:  # unittest: a module the run wrote that could not be imported is named after it
        if PurePosixPath(path).stem == name:
            return path
    return None
