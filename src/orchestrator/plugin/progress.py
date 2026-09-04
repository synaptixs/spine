"""Progress for the long tools: the engine's own phases, sent to the host as it goes.

``sdlc_feature`` runs for minutes — spec, layout, design, implement, tests, refine, judge,
PR — and a host saw a spinner until the whole run ended. MCP has progress notifications
for exactly this: a tool that receives the SDK ``Context`` calls ``report_progress`` and
the host renders it (when the client sent a progress token; it is a no-op otherwise).

**The phases come from the engines' existing log hooks, not new instrumentation.** The
feature runner already names its stages in bracketed log prefixes (``[spec] …``,
``[implement] …``); :class:`Reporter` maps those to ordered steps, so the CLI and the
plugin describe the same stages. Progress is a high-water mark — a second ``[run_tests]``
after ``[refine]`` does not move it backwards — and a line whose prefix the table does not
know rides on the *current* step as its message, so the host still sees the text and the
bar stays monotonic. (MCP's separate logging capability is deprecated as of SEP-2577, so
nothing here uses it.)

The reporter is a no-op with no context (the tools stay callable as plain functions) and
outside a real request (the SDK raises there), so nothing a test or a script does can be
broken by progress.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("orchestrator.plugin.progress")

#: The feature runner's stages, in the order a run reaches them. A prefix's position is its
#: step; ``len(FEATURE_PHASES)`` is the total. Kept in one place so a new stage in the
#: runner is a one-line addition here.
FEATURE_PHASES: tuple[str, ...] = (
    "backlog",
    "spec",
    "jira",
    "workspace",
    "layout",
    "testenv",
    "persona",
    "design",
    "implement",
    "author_tests",
    "run_tests",
    "cover",
    "refine",
    "judge",
    "revise",
    "repair",
    "typecheck",
    "proof",
    "coverage",
    "gate",
    "pr",
    "commit",
)

_PREFIX = re.compile(r"^\[([a-z_]+)")


def phase_of(line: str) -> str | None:
    """The bracketed prefix of a runner log line — ``[run_tests #2] …`` → ``run_tests``."""
    m = _PREFIX.match(line.strip())
    return m.group(1) if m else None


class Reporter:
    """Send ordered progress (and stray log lines) to the host through a tool's ``Context``.

    ``ctx`` is the SDK context the tool received, or ``None``. Every method is safe to call
    in either case: without a context, or outside a request, it does nothing and never
    raises — a progress bar must not be able to fail the work it describes.
    """

    def __init__(self, ctx: Any = None, *, phases: tuple[str, ...] = FEATURE_PHASES) -> None:
        self._ctx = ctx
        self._phases = phases
        self._high = 0

    @property
    def total(self) -> int:
        return len(self._phases)

    @property
    def high_water(self) -> int:
        return self._high

    async def step(self, done: int, total: int, message: str) -> None:
        """Report an explicit step. Monotonic: a lower ``done`` than seen before is skipped."""
        if done < self._high:
            return
        self._high = done
        await self._send("report_progress", done, total, message)

    async def log(self, message: str) -> None:
        """A line for the host without advancing the bar: the current step, re-sent with this
        message. (Not MCP's logging capability — that is deprecated.)"""
        await self._send("report_progress", self._high, self.total, message)

    async def phase_line(self, line: str) -> None:
        """Route a runner log line: a known prefix advances the bar, anything else is logged."""
        name = phase_of(line)
        if name in self._phases:
            await self.step(self._phases.index(name) + 1, self.total, line.strip())
        else:
            await self.log(line.strip())

    def as_log(self) -> Callable[[str], None]:
        """A synchronous ``log`` callback for engines that take one (the feature runner,
        the bank builder). The runner is sync-called from async code; the notification is
        scheduled on the running loop rather than awaited."""
        import asyncio

        def emit(line: str) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:  # no loop — a plain function call; nothing to send to
                return
            loop.create_task(self.phase_line(line))

        return emit

    async def _send(self, method: str, *args: Any) -> None:
        if self._ctx is None:
            return
        fn = getattr(self._ctx, method, None)
        if fn is None:
            return
        try:
            await fn(*args)
        except Exception as exc:  # outside a request, or a closed session: never the tool's problem
            logger.debug("plugin.progress.dropped", extra={"method": method, "error": str(exc)[:120]})


__all__ = ["FEATURE_PHASES", "Reporter", "phase_of"]
