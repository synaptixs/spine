"""When two edits in a row leave the failing run exactly as it was, the next one will not change it (B80).

The refine loop in ``feature_runner.run_feature`` used to stop early only when refine edited **no
files**: the next run would then be byte-identical to the one that just failed. An edit that changed
nothing about the failure was still treated as progress, so a model-written test that asks for
something the ticket never stated could cost the whole allowance: the product rewritten, round after
round, with the same assertion failing each time.

This module is the notion of "the same failure", and nothing else. It is deliberately the
**strictest** one available, because the stop it feeds can only ever end a run *earlier*; it must
never mistake a run that is converging for one that is stalled:

* the output the runner returned is compared, not a summary of it. (A runner keeps only the last
  4,000 characters; see ``comparable`` for what that means for a long one.) And
* only the parts of an output that are never the same twice are removed, and only in the exact line
  shapes a runner prints them in: a test summary's duration, an object's address after ``at``, the
  unique part of a temporary directory. A text that merely *contains* a duration, an address or a path
  (``assert 'expires in 30s' == ...``, ``assert color == 0xff8800``, ``/tmp/out_a.txt``) is left
  alone, because that is what an assertion is about. A form the patterns do not recognise makes two
  identical failures look different, which is the safe direction: the loop then behaves as it always
  did. And
* where the runner names its failing tests (``TestRunResult.problems``: pytest and .NET do;
  ``SDLC_TEST_COMMAND``, Maven, Node and Go do not), the names are part of the signature too.

A run that cannot show that nothing changed is never counted at all (``comparable``).

Nothing here judges *why* a test fails, whether the ticket supports it, or what to do about it. That
is the part a review loop on #458 showed does not converge, and it is out of scope.
``SDLC_REFINE_PROGRESS=0`` turns the check off, as ``SDLC_TEST_BASELINE=0`` does for the baseline.
"""

from __future__ import annotations

import os
import re
from typing import Any

from orchestrator.core.digest import digest_of

__all__ = [
    "REPEATS_TO_STALL",
    "RUNNER_OUTPUT_LIMIT",
    "StallTracker",
    "comparable",
    "enabled",
    "failure_signature",
    "normalise_output",
]

#: Identical failing runs *after the first* that make a stall: three identical runs, so two edits
#: that changed nothing.
REPEATS_TO_STALL = 2

#: The most output a runner keeps (``testrunner._MAX_OUTPUT_CHARS``, which a test pins this to).
RUNNER_OUTPUT_LIMIT = 4000

_NUM = r"\d+(?:\.\d+)?"

# A duration is removed only on the line a runner prints it on, and only the number. Every pattern
# is anchored to the start of the line, so text inside an assertion (``E   assert ... in 30s``) or in
# captured output never matches; the first group is the part of the line to keep.
_DURATIONS = (
    # unittest / Django:        Ran 8 tests in 0.006s
    re.compile(rf"(?m)^(Ran \d+ tests? in ){_NUM}s$"),
    # pytest summary:           ==== 1 failed, 2 passed in 0.12s (0:00:01) ====
    re.compile(
        rf"(?m)^(=* ?(?:\d+ \w+|no tests ran)(?:, \d+ \w+)* in ){_NUM}s(?: \(\d+:\d\d:\d\d\))?( ?=*)$"
    ),
    # pytest --durations:       0.01s call     tests/a.py::test_x
    re.compile(rf"(?m)^(\s*){_NUM}s(?=\s+(?:call|setup|teardown)\s)"),
    # Go:                       --- FAIL: TestX (0.00s)      and      FAIL<TAB>pkg<TAB>0.123s
    re.compile(rf"(?m)^(\s*--- (?:FAIL|PASS|SKIP): \S+ \(){_NUM}s(\))$"),
    re.compile(rf"(?m)^((?:ok|FAIL)\s+\S+\s+){_NUM}s((?:\s+\(cached\))?)$"),
    # jest / mocha:             Time:        1.234 s      and      ✓ does a thing (12 ms)
    re.compile(rf"(?m)^(Time:\s{{2,}}){_NUM}\s?s$"),
    re.compile(rf"(?m)^(\s+[✓✔✕✗√×] .+ \(){_NUM} ?ms(\))$"),
)
# An object's address in its repr: <Foo object at 0x7f3a2c1b>. Only after "at", so a hex value
# an assertion compares (``color == 0xff8800``) is untouched.
_ADDRESS = re.compile(r"(?<=\bat )0x[0-9a-fA-F]{6,}")
# The part of a temporary directory that is unique per run, and only that part, so what the path
# *points at* still tells two failures apart:
#   pytest's basetemp   pytest-of-<user>/pytest-<n>
#   macOS's per-user    /var/folders/<hash>/<hash>/T/
#   tempfile.mkdtemp    tmp<8 random characters>
_PYTEST_TMP = re.compile(r"pytest-of-[^/\\\s]+([/\\])pytest-\d+")
_MACOS_TMP = re.compile(r"/var/folders/[^/\s]+/[^/\s]+/T/")
_MKDTEMP = re.compile(r"(?<=[/\\])tmp[a-z0-9_]{8}(?=[/\\\s:'\"]|$)")


def normalise_output(text: str) -> str:
    """The output with the parts that are never the same twice removed, and nothing else."""
    out = text
    for pattern in _DURATIONS:
        out = pattern.sub(_keep_around, out)
    out = _ADDRESS.sub("0x<addr>", out)
    out = _PYTEST_TMP.sub(lambda m: f"pytest-of-<user>{m.group(1)}pytest-<n>", out)
    out = _MACOS_TMP.sub("/var/folders/<h>/<h>/T/", out)
    out = _MKDTEMP.sub("tmp<rand>", out)
    return out.strip()


def _keep_around(match: re.Match[str]) -> str:
    """Replace the duration with a marker and keep what the pattern captured around it."""
    groups = match.groups()
    head = groups[0] or ""
    tail = "".join(g or "" for g in groups[1:])
    return f"{head}<t>{tail}"


def comparable(result: Any) -> bool:
    """Whether this run's output can show that nothing changed.

    Two kinds cannot, and are never counted (the loop resets the count instead):

    * a run that was killed or timed out (``returncode < 0``): every runner reports that with its own
      fixed message ("test run timed out"), so two of them read alike whatever the edits did, and a
      hang is something refine *can* fix; and
    * a run whose output reached the runner's keep-the-last-4,000-characters limit: an earlier part of
      a long report may have been clipped away, so a fix there would leave the tail looking unchanged.
    """
    returncode = getattr(result, "returncode", 0)
    if isinstance(returncode, int) and returncode < 0:
        return False
    return len(str(getattr(result, "output", "") or "")) < RUNNER_OUTPUT_LIMIT


def failure_signature(result: Any) -> str:
    """A digest of one failing test run: the failing ids where the runner names them, and the output.

    ``result`` is a ``TestRunResult`` or anything shaped like it; ``problems`` is optional because
    most runners leave it empty (and test doubles omit it). The ids are sorted, so the order a
    runner listed them in cannot change the signature.
    """
    problems = sorted(str(p) for p in (getattr(result, "problems", ()) or ()))
    output = str(getattr(result, "output", "") or "")
    return digest_of({"problems": problems, "output": normalise_output(output)})


class StallTracker:
    """Counts, per kind of check, how many failing runs in a row matched the one before them.

    ``observe`` is called with the signature of each failing run: the first sighting of a failure
    counts 0, an exact repeat counts one more, and anything different starts again. ``reset`` is for
    a run that passed, or one that is not ``comparable``: a failure that comes back after either is
    a new failure, even if it reads the same.
    """

    def __init__(self, threshold: int = REPEATS_TO_STALL) -> None:
        self._threshold = threshold
        self._last: dict[str, str] = {}
        self._repeats: dict[str, int] = {}

    def observe(self, kind: str, signature: str) -> int:
        if self._last.get(kind) == signature:
            self._repeats[kind] = self._repeats.get(kind, 0) + 1
        else:
            self._last[kind] = signature
            self._repeats[kind] = 0
        return self._repeats[kind]

    def reset(self, kind: str) -> None:
        self._last.pop(kind, None)
        self._repeats.pop(kind, None)

    def repeats(self, kind: str) -> int:
        return self._repeats.get(kind, 0)

    def stalled(self, kind: str) -> bool:
        return self.repeats(kind) >= self._threshold


def enabled() -> bool:
    """``SDLC_REFINE_PROGRESS=0`` (or false, no, off) turns the check off; it is on by default."""
    return (os.getenv("SDLC_REFINE_PROGRESS") or "1").strip().lower() not in {"0", "false", "no", "off"}
