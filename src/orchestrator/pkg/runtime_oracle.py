"""The runtime oracle — ``CALLS`` recall from a repository's own test suite.

``accuracy.py`` scores against hand-labelled fixtures, which only contain the difficulty
their author thought to write down. This scores against **real execution**: run the repo's
tests under a call tracer, and ask how many calls that demonstrably happened have a ``CALLS``
edge. No labelling, no corpus, works on a repository nobody here has seen.

**It measures recall and only recall.** A call the trace never observed is *untested*, not
*false* — the test suite simply may not exercise it. Precision is not computable from a trace,
and the report says so on every run rather than leaving the reader to assume.

The number is a lower bound, bounded twice: by what the suite executes, and by what the
tracer can attribute to a node id. Both appear next to it.

Three things learned by measuring rather than reasoning, each of which would otherwise
produce a plausible wrong number:

- **A trace observes a pair; the graph holds one edge per call site.** ``Edge.key()`` includes
  provenance, so two calls from ``f`` to ``g`` on different lines are two edges. On this repo
  15,514 ``CALLS`` edges collapse to 12,824 unique pairs — compare pair-to-pair or the
  denominators disagree by ~17%.
- **``co_qualname`` embeds ``.<locals>.``** for anything defined inside a function, where the
  graph holds a flat dotted path. Stripped, not special-cased.
- **Out-of-tree and unmapped are different outcomes.** A call into the stdlib is *excluded* —
  it was never the graph's job. A call between two in-tree functions where one has no node is
  *unmapped*, and that is a finding. Collapsing them hides the second.

Tracing uses ``sys.monitoring`` (PEP 669), which ``requires-python = ">=3.12"`` guarantees.
``sys.settrace`` would give only the callee frame and force the caller to be inferred from the
stack; ``events.CALL`` hands over both ends.

**Not deterministic, by nature** — execution order, skips and timing all vary. Its output must
never reach ``episteme/`` or be read by ``understand``/``state``, whose value depends on
byte-identical reproduction (CLAUDE.md invariant 2).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.extractor import module_qualname

# PROFILER_ID. `coverage` takes 1 (COVERAGE_ID), so the two can coexist.
TOOL_ID = 2
_SKIP_PARTS = frozenset({".venv", "venv", "node_modules", "__pycache__", "site-packages"})
_MAX_EXAMPLES = 15


class OracleError(Exception):
    """The suite could not be traced. The only condition that fails the command."""


# ---- mapping: traced frame -> graph node id -------------------------------
#
# Validated by spike before implementation: 1,019 observed pairs across 304 tests,
# 100% mappable, zero unmapped. Everything here earns its place against a hazard.


def _in_tree(path: Path, root: Path) -> bool:
    """Is this file first-party source? Mirrors the walkers' dot-directory rule."""
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return False
    return not any(p in _SKIP_PARTS or p.startswith(".") for p in rel.parts)


_MODULE_CACHE: dict[tuple[str, str], str | None] = {}


def module_for_file(filename: str, root: Path) -> str | None:
    """Dotted module name for a traced code object's file, or None if out of tree.

    Uses ``module_qualname`` — the extractor's own function — because a module name derived
    any other way makes every id miss.

    **Memoised, and that is not an optimisation detail.** This runs inside the ``CALL``
    callback, once per call event: ~11.7M times over this repo's own ``tests/pkg``. Doing the
    ``Path.resolve()`` each time took the traced suite from 18 seconds to over ten minutes
    before the cache went back in. The key includes ``root`` so two repos never collide, and
    the cache is bounded by the number of distinct source files.
    """
    key = (filename, str(root))
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]
    result: str | None = None
    if filename.endswith(".py"):
        path = Path(filename)
        if path.is_absolute() and _in_tree(path, root):
            result = module_qualname(path, root)
    _MODULE_CACHE[key] = result
    return result


def _flatten(qualname: str) -> str | None:
    """``outer.<locals>.inner`` -> ``outer.inner``; None for anything anonymous.

    A lambda, comprehension or module body has no node in the graph, so it is not a miss —
    there is nothing it could have matched.
    """
    flat = qualname.replace(".<locals>", "")
    return None if "<" in flat else flat


def caller_id(code: object, root: Path) -> str | None:
    """Node id for the *calling* code object, or None if it cannot be one."""
    filename = getattr(code, "co_filename", None)
    qualname = getattr(code, "co_qualname", None)
    if not isinstance(filename, str) or not isinstance(qualname, str):
        return None
    module = module_for_file(filename, root)
    if module is None:
        return None
    flat = _flatten(qualname)
    return None if flat is None else f"py:{module}.{flat}"


def callee_id(fn: object, root: Path) -> tuple[str | None, str]:
    """Node id for the *called* object, plus why it was dropped if it has none.

    Reasons are distinct on purpose: ``builtin`` and ``out-of-tree`` were never the graph's
    responsibility, while a missing node for in-tree code is a finding (counted by the caller
    as *unmapped*, not as a miss).
    """
    module_name = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if not isinstance(module_name, str) or not isinstance(qualname, str):
        return None, "builtin"

    module = sys.modules.get(module_name)
    filename = getattr(module, "__file__", None) if module is not None else None
    if not isinstance(filename, str):
        return None, "out-of-tree"

    # Derive the module name from the file, exactly as the caller side does, rather than
    # trusting __module__ — one source of truth for the id scheme, not two.
    resolved = module_for_file(filename, root)
    if resolved is None:
        return None, "out-of-tree"

    flat = _flatten(qualname)
    if flat is None:
        return None, "anonymous"
    return f"py:{resolved}.{flat}", "ok"


# ---- the tracer ----------------------------------------------------------


class CallTracer:
    """Records first-party ``(caller_id, callee_id)`` pairs via ``sys.monitoring``.

    A context manager because the tool id is a scarce global resource — it must come back
    whatever happens inside.
    """

    def __init__(self, root: Path, *, tool_id: int = TOOL_ID) -> None:
        self.root = root.resolve()
        self.tool_id = tool_id
        self.pairs: set[tuple[str, str]] = set()
        self.dropped: Counter[str] = Counter()

    def _on_call(self, code: object, offset: int, fn: object, arg0: object) -> None:
        # Cheapest rejection first. The overwhelming majority of call events have an
        # out-of-tree caller (10.9M of 11.7M on this repo), and `module_for_file` is memoised
        # so this costs a dict lookup rather than a stat.
        filename = getattr(code, "co_filename", None)
        if not isinstance(filename, str) or module_for_file(filename, self.root) is None:
            return
        src = caller_id(code, self.root)
        if src is None:
            return
        dst, why = callee_id(fn, self.root)
        if dst is None:
            self.dropped[why] += 1
            return
        self.pairs.add((src, dst))

    def __enter__(self) -> CallTracer:
        mon = sys.monitoring
        mon.use_tool_id(self.tool_id, "spine-runtime-oracle")
        mon.register_callback(self.tool_id, mon.events.CALL, self._on_call)
        mon.set_events(self.tool_id, mon.events.CALL)
        return self

    def __exit__(self, *exc: object) -> None:
        mon = sys.monitoring
        mon.set_events(self.tool_id, 0)
        mon.register_callback(self.tool_id, mon.events.CALL, None)
        mon.free_tool_id(self.tool_id)


# ---- subprocess entry point ---------------------------------------------
#
# Tracing has to happen in the process that runs the tests, and the tests run in a child so
# that executing someone else's code is an explicit, isolated act (see the CLI's --oracle
# help). The child writes its observations as JSON; the parent owns all the scoring.


def _child_main(argv: list[str]) -> int:
    import io

    import pytest

    root, out_path, *targets = argv
    repo = Path(root).resolve()

    # Coverage is the *second* bound on the number and belongs next to it: "recall ≥ 0.70"
    # means little without "over the 43% of statements these tests reach". Best-effort —
    # coverage takes sys.monitoring tool id 1 and we take 2, but a version that disagrees
    # should degrade to an honest `null`, not take the measurement down with it.
    coverage_pct: float | None = None
    cov = None
    try:
        import coverage

        cov = coverage.Coverage(data_file=None, source=[str(repo)])
        cov.start()
    except Exception:  # noqa: BLE001 - any failure here is non-fatal by design
        cov = None

    tracer = CallTracer(repo)
    with tracer:
        code = pytest.main([*(targets or ["tests"]), "-q", "--no-header", "-p", "no:cacheprovider"])

    if cov is not None:
        try:
            cov.stop()
            coverage_pct = float(cov.report(file=io.StringIO()))
        except Exception:  # noqa: BLE001 - same
            coverage_pct = None

    Path(out_path).write_text(
        json.dumps(
            {
                "pairs": sorted([s, d] for s, d in tracer.pairs),
                "dropped": dict(tracer.dropped),
                "pytest_exit": int(code),
                "coverage_pct": coverage_pct,
            }
        ),
        encoding="utf-8",
    )
    return 0


# ---- scoring -------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeReport:
    """What real execution says about ``CALLS`` recall. Recall only — see the module docstring."""

    observed: int
    matched: int
    unmapped: int
    missing: tuple[str, ...]
    unmapped_examples: tuple[str, ...]
    dropped: dict[str, int]
    pytest_exit: int
    command: str
    coverage_pct: float | None = None

    @property
    def recall(self) -> float | None:
        """Lower bound. ``None`` when nothing was observed — never 1.0 for an empty trace."""
        return self.matched / self.observed if self.observed else None

    @property
    def precision(self) -> None:
        """Always ``None``. Not measurable from a trace, and named so nobody adds it later."""
        return None


def score_observations(
    pairs: set[tuple[str, str]],
    node_ids: set[str],
    call_pairs: set[tuple[str, str]],
    *,
    dropped: dict[str, int] | None = None,
    pytest_exit: int = 0,
    command: str = "",
    coverage_pct: float | None = None,
) -> RuntimeReport:
    """Compare observed pairs against the graph. Pure — no tracing, no subprocess."""
    mappable = {(s, d) for s, d in pairs if s in node_ids and d in node_ids}
    unmapped = pairs - mappable
    hit = mappable & call_pairs
    return RuntimeReport(
        observed=len(mappable),
        matched=len(hit),
        unmapped=len(unmapped),
        missing=tuple(f"{s} -CALLS-> {d}" for s, d in sorted(mappable - call_pairs))[:_MAX_EXAMPLES],
        unmapped_examples=tuple(f"{s} -> {d}" for s, d in sorted(unmapped))[:_MAX_EXAMPLES],
        dropped=dict(dropped or {}),
        pytest_exit=pytest_exit,
        command=command,
        coverage_pct=coverage_pct,
    )


def score_runtime(
    repo: Path | str, *, targets: list[str] | None = None, timeout: int = 1800
) -> RuntimeReport:
    """Trace ``repo``'s test suite in a subprocess and score ``CALLS`` recall against its graph.

    Raises ``OracleError`` if the suite cannot be run — never a low score.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.facts import EdgeKind

    root = Path(repo).resolve()
    if not root.is_dir():
        raise OracleError(f"{root}: not a directory")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "trace.json"
        cmd = [sys.executable, "-m", __name__, str(root), str(out), *(targets or [])]
        printable = " ".join(cmd)
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise OracleError(f"tracing timed out after {timeout}s: {printable}") from exc
        if not out.is_file():
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise OracleError(f"tracer produced no output.\n  command: {printable}\n  " + "\n  ".join(tail))
        payload = json.loads(out.read_text(encoding="utf-8"))

    batch = RepoCodeExtractor().extract(root)
    node_ids = {n.id for n in batch.nodes}
    # Pair-level on both sides: the graph keys provenance into an edge, a trace cannot see it.
    call_pairs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}

    return score_observations(
        {(s, d) for s, d in payload["pairs"]},
        node_ids,
        call_pairs,
        dropped=payload.get("dropped", {}),
        pytest_exit=int(payload.get("pytest_exit", 0)),
        command=printable,
        coverage_pct=payload.get("coverage_pct"),
    )


if __name__ == "__main__":  # pragma: no cover - the child process
    sys.exit(_child_main(sys.argv[1:]))


__all__ = [
    "TOOL_ID",
    "CallTracer",
    "OracleError",
    "RuntimeReport",
    "callee_id",
    "caller_id",
    "module_for_file",
    "score_observations",
    "score_runtime",
]
