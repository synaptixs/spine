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
* "an existing test ran" means the run executed more tests than the run wrote. It is **not** required
  (D10: a runner that executes only the run's own files never runs one); it is reported, so the label can
  say that nothing existing supports the change. ``SDLC_OWN_TEST_RESCUE=strict`` requires it (D3). A run
  whose output does not show that any test ran is never rescued.

The output is read with ANSI colour stripped (B98), and a class-level fixture failure (``ERROR: setUpClass
(m.Cls)``) is named by its class and owned by the run's test file that defines it (B105): that is how a run's
own test class failing looks, and it is the one output with ``Ran 0 tests`` that still counts as a test
reached.

``SDLC_OWN_TEST_RESCUE=0`` turns the rescue off, as ``SDLC_REFINE_PROGRESS=0`` does for the stall stop.

The same reading decides the repair turn that runs *before* the rescue (test-trust C2, SSPN-228): when every
failing test is the run's own, the model is first given one move to rewrite those files; the rescue is what
is left when that does not work. ``SDLC_OWN_TEST_REPAIR=0`` turns the repair turn off.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from orchestrator.sdlc.test_location import ran_no_tests
from orchestrator.sdlc.testpaths import is_test_path

__all__ = [
    "OwnTestVerdict",
    "added_test_functions",
    "classify",
    "count_test_functions",
    "enabled",
    "repair_enabled",
    "executed_tests",
    "failing_test_names",
    "import_failures",
    "nothing_left_to_run",
    "strict",
]

# unittest / Django:   FAIL: test_x (module.Class)      ERROR: test_x (unittest.loader._FailedTest)
_UNITTEST_LINE = re.compile(r"(?m)^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)")
#: A class- or module-level fixture, which unittest reports without a test name:
#: ``ERROR: setUpClass (m.Cls)``.
#: Nothing in the class ran, so the line names the class and the fixture (B105).
_FIXTURE = re.compile(r"^(?:setUp|tearDown)(?:Class|Module)$")
#: ANSI escape sequences (colour, cursor): pytest and Django's runner print them when the environment asks for
#: colour, and they sit in front of ``FAILED`` and around the counts (B98).
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# pytest short summary:   FAILED tests/test_x.py::test_a - assert ...      ERROR tests/test_y.py - ImportError
# (unittest's closing "FAILED (failures=1)" starts with a parenthesis, which a pytest id never does.)
_PYTEST_LINE = re.compile(r"(?m)^(?:FAILED|ERROR) ([^\s(]\S*?)(?: - .*)?$")
# An import failure names the MODULE, not a test: ``ERROR: test_x (unittest.loader._FailedTest)``.
_UNITTEST_IMPORT = re.compile(r"(?m)^ERROR: (\w+) \(unittest\.loader\._FailedTest\)")
_RAN = re.compile(r"(?m)^Ran (\d+) tests?\b")
_PYTEST_COUNTS = re.compile(r"(\d+) (?:passed|failed|xfailed|xpassed)\b")
_PYTEST_SUMMARY = re.compile(r"(?m)^=*\s*\d+ \w+.* in \d+(?:\.\d+)?s\b.*$")
_TEST_DEF = re.compile(r"(?m)^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
# sympy's own runner (`bin/test`, B99). It prints a header per file, ``path/test_x.py[7] ``, then one line per
# test, ``test_name ok`` / ``F`` (failed) / ``E`` (raised); a block per failure headed
# ``_ path.py:test_name _``;
# and a closing ``tests finished: 6 passed, 1 failed, in 0.31 seconds``.
_SYMPY_START = re.compile(r"(?m)^=+ test process starts =+\s*$")
_SYMPY_FINISH = re.compile(r"(?m)^=+ tests finished: ([^\n]*?),? in [\d.]+ seconds? =+\s*$")
_SYMPY_FILE = re.compile(r"(?m)^(\S+\.py)\[\d+\]\s*$")
_SYMPY_RED_LINE = re.compile(r"^(test\w*) (?:F|E)(?:\s+\[(?:FAIL|OK)\])?\s*$")
_SYMPY_BLOCK = re.compile(r"(?m)^_*\s*([\w./-]+\.py):(test\w*)\s*_*$")
_SYMPY_RAN = re.compile(r"(\d+) (?:passed|failed|exceptions?|expected to fail)\b")


#: What ``CommandTestRunner`` says for an empty selection (pinned against the real runner by a test: this
#: module is below the runners and may not import them).
_EMPTY_SELECTION = "refusing an empty test run"
#: pytest's exit status for "no tests were collected", which the runners reuse for an empty or zero-test run.
_NO_TESTS_EXIT = 5


def enabled() -> bool:
    """On unless ``SDLC_OWN_TEST_RESCUE=0`` (``strict`` is on, and stricter: see :func:`strict`)."""
    return os.getenv("SDLC_OWN_TEST_RESCUE", "1").strip() != "0"


def repair_enabled() -> bool:
    """On unless ``SDLC_OWN_TEST_REPAIR=0``: the model may rewrite a stalled test it wrote itself (C2)."""
    return os.getenv("SDLC_OWN_TEST_REPAIR", "1").strip() != "0"


def strict() -> bool:
    """``SDLC_OWN_TEST_RESCUE=strict``: also require that a test the run did not write executed (D3).

    The default is the relaxed D10: a runner that executes only the files the run wrote (the benchmark's
    ``{files}`` command) never runs an existing test, so D3 refused 11 of the 14 stored runs that the final
    tree resolved in 7 of. The rescue then goes ahead and the label says that nothing existing supports it.
    """
    return os.getenv("SDLC_OWN_TEST_RESCUE", "1").strip().lower() == "strict"


def _plain(output: str) -> str:
    """``output`` without ANSI escape sequences: what every reader below parses (B98)."""
    return _ANSI.sub("", output)


def nothing_left_to_run(returncode: int, output: str) -> bool:
    """A test run that was refused or executed nothing, as opposed to one that failed.

    After the run's only test file is withdrawn a ``{files}`` command has no file left and says so (exit 5);
    pytest and the benchmark's wrapper use the same status for zero tests.
    """
    return returncode == _NO_TESTS_EXIT and (_EMPTY_SELECTION in output or ran_no_tests(output))


def failing_test_names(output: str, problems: Sequence[str] = ()) -> tuple[str, ...]:
    """What the run reports as failing, once each, in order: the runner's own ``problems`` first, then the
    names read from the output (pytest's short summary, unittest's ``FAIL:``/``ERROR:`` lines)."""
    output = _plain(output)
    names: list[str] = [str(p).strip() for p in problems if str(p).strip()]
    names.extend(m.group(1) for m in _PYTEST_LINE.finditer(output))
    for m in _UNITTEST_LINE.finditer(output):
        # A fixture failure carries its class: without it the name is the same for every class (B105).
        names.append(f"{m.group(2)}.{m.group(1)}" if _FIXTURE.match(m.group(1)) else m.group(1))
    names.extend(_sympy_failures(output))
    return tuple(dict.fromkeys(names))


def executed_tests(output: str) -> int | None:
    """How many tests the run executed, or ``None`` when the output does not say.

    unittest prints ``Ran N tests``; pytest prints ``1 failed, 5 passed, 1 warning in 0.8s`` (a warning or a
    skip is not a test that ran).
    """
    output = _plain(output)
    ran = _RAN.findall(output)
    if ran:
        return int(ran[-1])
    summaries = _PYTEST_SUMMARY.findall(output)
    if summaries:
        return sum(int(n) for n in _PYTEST_COUNTS.findall(summaries[-1]))
    finished = _SYMPY_FINISH.findall(output)
    if finished:
        # An expected failure ran; a skip did not (the file header's ``[85]`` counts both).
        return sum(int(n) for n in _SYMPY_RAN.findall(finished[-1]))
    return None


def _sympy_failures(output: str) -> list[str]:
    """The failed tests in sympy's runner output, as pytest-style ``path::name`` ids (B99).

    Only read when the output is sympy's own (it opens with ``test process starts`` or closes with ``tests
    finished``), so another runner's line that happens to end in ``F`` is never taken for a test. The failure
    blocks name each test; the per-file lines are the fallback for a header that was cut off.
    """
    if not (_SYMPY_START.search(output) or _SYMPY_FINISH.search(output)):
        return []
    found = [f"{m.group(1)}::{m.group(2)}" for m in _SYMPY_BLOCK.finditer(output)]
    current = ""
    for line in output.splitlines():
        header = _SYMPY_FILE.match(line)
        if header:
            current = header.group(1)
            continue
        red = _SYMPY_RED_LINE.match(line)
        if red and current:
            found.append(f"{current}::{red.group(1)}")
    return found


def import_failures(output: str) -> frozenset[str]:
    """The names in ``output`` that are modules the runner could not import, not tests."""
    return frozenset(m.group(1) for m in _UNITTEST_IMPORT.finditer(_plain(output)))


def count_test_functions(source: str) -> int:
    """The ``def test…`` functions a file defines (a lower bound on what it runs; Python style)."""
    return len(_TEST_DEF.findall(source))


def added_test_functions(source: str, before: str = "") -> int:
    """The ``def test…`` functions ``source`` defines that ``before`` (its ``HEAD`` text) did not.

    A file the run only edited already held tests, and those ran and passed on their own: counting them as
    the run's would make "an existing test ran" false for every such run (0 of the 14 stored class-O runs
    were rescuable until this counted what the run added). Compared by name with repeats counted, so a
    renamed test is one new test and a removed test never subtracts.
    """
    now = Counter(_TEST_DEF.findall(source))
    was = Counter(_TEST_DEF.findall(before))
    return sum((now - was).values())


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
    #: The output says at least one test ran. A run that cannot say, or ran none, is never rescued.
    ran: bool = True
    #: ``strict`` mode: also require ``existing_executed`` (D3 as first decided).
    require_existing: bool = False

    @property
    def rescuable(self) -> bool:
        return self.own_only and self.ran and (self.existing_executed or not self.require_existing)


def _defines(source: str, name: str) -> bool:
    return re.search(rf"(?m)^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(", source) is not None


def classify(
    *, output: str, problems: Sequence[str], authored: Mapping[str, str], own_test_count: int
) -> OwnTestVerdict:
    """Whether the failures in ``output`` are all the run's own tests.

    ``authored`` maps every path the run wrote to its text; only test paths can vouch for a name.
    ``own_test_count`` is how many test functions the run *added* (not the tests of a file it merely edited),
    to tell whether anything else executed.
    """
    names = failing_test_names(output, problems)
    modules = import_failures(output)
    tests = {p.replace("\\", "/"): text for p, text in authored.items() if is_test_path(p)}
    if not names:
        return OwnTestVerdict(False, (), (), False, "the output names no failing test")

    files: list[str] = []
    foreign: list[str] = []
    for name in names:
        owner = _owner(name, tests, module=name in modules)
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
    # A class that could not be set up ran none of its tests, so `Ran 0 tests` is how a run's own test class
    # failing looks; it is the one zero-test output that does show a test of the run's own was reached (B105).
    reached = any(_FIXTURE.match(n.rsplit(".", 1)[-1]) for n in names)
    if (executed is None or executed < 1) and not reached:
        return OwnTestVerdict(
            True, held, names, False, "the output does not show that any test ran", ran=False
        )
    existing = (executed or 0) - own_test_count >= 1
    require = strict()
    reason = "" if existing or not require else "no existing test executed alongside the run's own (strict)"
    return OwnTestVerdict(True, held, names, existing, reason, ran=True, require_existing=require)


def _owner(name: str, tests: Mapping[str, str], *, module: bool = False) -> str | None:
    """The test file the run wrote that this failing test belongs to, or ``None``.

    ``module`` is set when the runner could not import the name: it is a module, so only a file of that name
    can be it. A method elsewhere that happens to share the name (django-14349) is a different thing and must
    not vouch for it.
    """
    if module:
        return next((path for path in tests if PurePosixPath(path).stem == name), None)
    if "." in name and "/" not in name and "::" not in name and _FIXTURE.match(name.rsplit(".", 1)[-1]):
        return _fixture_owner(name, tests)
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


def _fixture_owner(name: str, tests: Mapping[str, str]) -> str | None:
    """The test file the run wrote that defines the class a failed fixture belongs to, or ``None``.

    ``name`` is ``<module>.<Class>.<fixture>`` as ``failing_test_names`` builds it from
    ``ERROR: setUpClass (queries.test_x.Cls)``. Both halves must agree: the file's own dotted path ends with
    the module (Django labels are relative to its ``tests`` directory, so ``tests/queries/test_x.py`` is
    ``queries.test_x``), and the file defines that class. A same-named class in some other module is not it.
    """
    *module, cls, _fixture = name.split(".")
    if not module:
        return None
    dotted = ".".join(module)
    for path, text in tests.items():
        file_dotted = ".".join(PurePosixPath(path).with_suffix("").parts)
        if (file_dotted == dotted or file_dotted.endswith("." + dotted)) and re.search(
            rf"(?m)^class\s+{re.escape(cls)}\b", text
        ):
            return path
    return None
