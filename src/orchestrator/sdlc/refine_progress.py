"""When two edits in a row leave the failing run exactly as it was, the next one will not change it (B80).

The refine loop in ``feature_runner.run_feature`` used to stop early only when refine edited **no
files**: the next run would then be byte-identical to the one that just failed. An edit that changed
nothing about the failure was still treated as progress, so a model-written test that asks for
something the ticket never stated could cost the whole allowance: the product rewritten, round after
round, with the same assertion failing each time.

This module is the notion of "the same failure", and nothing else. It is deliberately the
**strictest** one available, because the stop it feeds can only ever end a run *earlier*; it must
never mistake a run that is converging for one that is stalled:

* the whole test output is compared, not a summary of it; and
* only the parts of an output that are never the same twice are removed (a duration, a memory
  address, a temporary directory, a process id), and only in the forms a runner is known to print
  them. A duration the patterns do not recognise makes two identical failures look different, which
  is the safe direction: the loop then behaves as it always did. Stripping a number that was the
  point of an assertion would be the unsafe one, so ``assert 1.5s == 2.5s`` is left alone; and
* where the runner names its failing tests (``TestRunResult.problems``: pytest and .NET do;
  ``SDLC_TEST_COMMAND``, Maven, Node and Go do not), the names are part of the signature too.

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
    "StallTracker",
    "enabled",
    "failure_signature",
    "normalise_output",
]

#: Identical failing runs *after the first* that make a stall: three identical runs, so two edits
#: that changed nothing.
REPEATS_TO_STALL = 2

# A duration is removed only where a runner reports one: ``Ran 8 tests in 0.006s`` (unittest),
# ``1 failed in 0.12s`` (pytest), ``(0.00s)`` / ``(12 ms)`` (Go, jest), ``0.01s call test_x``
# (``--durations``), ``Time: 1.2 s`` and ``Duration: 3 s`` (jest, dotnet) and Go's
# ``ok  pkg  0.123s``. The unit is part of every pattern, so a bare number never matches.
_UNIT = r"(?:ms|milliseconds|s|sec|secs|seconds)"
_NUM = r"\d+(?:\.\d+)?"
_DURATIONS = (
    re.compile(rf"(?<=\bin ){_NUM}\s?{_UNIT}\b"),
    re.compile(rf"\(\s?{_NUM}\s?{_UNIT}\s?\)"),
    re.compile(rf"\b{_NUM}s(?=\s+(?:call|setup|teardown)\b)"),
    re.compile(rf"(?i)(?<=\btime: )\s*{_NUM}\s?{_UNIT}\b"),
    re.compile(rf"(?i)(?<=\bduration: )\s*{_NUM}\s?{_UNIT}\b"),
    re.compile(rf"(?m)^((?:ok|FAIL)\s+\S+\s+){_NUM}s\b"),
)
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{6,}")
# A scratch directory the runner made: /tmp/pytest-of-u/pytest-3/..., macOS's
# /private/var/folders/..., and the Windows temp directory. What follows the root up to the next
# delimiter is the part that is unique per run.
_TEMP = re.compile(
    r"(?:/private)?/(?:tmp|var/folders|var/tmp)/[^\s:'\"<>)\],]+"
    r"|[A-Za-z]:\\Users\\[^\\\s]+\\AppData\\Local\\Temp\\[^\s:'\"<>)\],]+"
)
_PID = re.compile(r"(?i)\bpid[= :]+\d+")


def normalise_output(text: str) -> str:
    """The output with the parts that are never the same twice removed, and nothing else."""
    out = text
    for pattern in _DURATIONS:
        out = pattern.sub(_keep_prefix, out)
    out = _ADDRESS.sub("0x<addr>", out)
    out = _TEMP.sub("<tmp>", out)
    out = _PID.sub("pid <n>", out)
    return out.strip()


def _keep_prefix(match: re.Match[str]) -> str:
    """Replace a matched duration with a marker; the pattern for Go's package column keeps it."""
    prefix = match.group(1) if match.re.groups else ""
    return f"{prefix}<t>"


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
    a run that passed: a failure that comes back after a green run is a new failure, even if it
    reads the same.
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
