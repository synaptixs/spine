"""A held-out suite must pass a correct solution (B51, SSPN-97).

The benchmark's held-out suites are the independent judge of every codegen run, and
nothing checked that the judge could be satisfied. `NEW-DRIFTMD-1`'s suite built a
hand-made stand-in for `DocDriftFinding` (a `str` kind, no `message`), so code written
against the real type raised on `kind.value` — every run of every tool on every model
failed that ticket, and three measurement runs recorded it as a model failure.

Each case here runs a suite in-process against a small reference solution, placed
inside the package the suite scans, so an unpassable suite fails CI instead of every
run. A deliberately wrong solution must fail the same suite — a judge that passes
everything is as useless as one that passes nothing.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]


def _load_benchmark() -> ModuleType:
    for p in (str(_REPO / "src"), str(_REPO / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    if "codegen_benchmark" in sys.modules:
        return sys.modules["codegen_benchmark"]
    spec = importlib.util.spec_from_file_location(
        "codegen_benchmark", _REPO / "scripts" / "codegen_benchmark.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so frozen dataclasses can resolve their own module.
    sys.modules["codegen_benchmark"] = mod
    spec.loader.exec_module(mod)
    return mod


_BENCH = _load_benchmark()


def _suite(key: str) -> str:
    (ticket,) = [t for t in _BENCH.TICKETS if t.key == key]
    (source,) = ticket.held_out_tests.values()
    return str(source)


def _run_suite(
    suite: str,
    *,
    package: str,
    reference: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run every ``test_*`` in ``suite`` with ``reference`` importable inside ``package``."""
    pkg = importlib.import_module(package)
    name = "_heldout_reference"
    (tmp_path / f"{name}.py").write_text(reference, encoding="utf-8")
    monkeypatch.setattr(pkg, "__path__", [*pkg.__path__, str(tmp_path)])
    monkeypatch.delitem(sys.modules, f"{package}.{name}", raising=False)
    try:
        held = types.ModuleType("heldout_suite")
        exec(compile(suite, "heldout_suite.py", "exec"), held.__dict__)  # noqa: S102 — the benchmark's own suite, run as the benchmark runs it
        tests = [fn for n, fn in vars(held).items() if n.startswith("test_") and callable(fn)]
        assert tests, "the suite defines no tests"
        for test in tests:
            test()
    finally:
        sys.modules.pop(f"{package}.{name}", None)


_DRIFTMD_REFERENCE = """
from orchestrator.pkg.docs import DocDriftFinding


def render_drift_markdown(findings: list[DocDriftFinding]) -> str:
    if not findings:
        return "No drift found."
    by_page: dict[str, list[DocDriftFinding]] = {}
    for f in findings:
        by_page.setdefault(f.page_title, []).append(f)
    lines = ["# Documentation drift"]
    for page, items in by_page.items():
        lines += ["", f"## {page}", ""]
        lines += [f"- {ESCAPE(f.mention)} ({f.kind.value}): {f.message}" for f in items]
    return "\\n".join(lines) + "\\n"
"""

# Plain, and Markdown-escaped (`missing\_symbol`) — both are correct renderings.
_PLAIN = "ESCAPE = str\n"
_ESCAPED = "def ESCAPE(s):\n    return s.replace('_', '\\\\_').replace('.', '\\\\.')\n"


@pytest.mark.parametrize("escape", [_PLAIN, _ESCAPED], ids=["plain", "markdown-escaped"])
def test_driftmd_suite_passes_a_correct_solution(
    escape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_suite(
        _suite("NEW-DRIFTMD-1"),
        package="orchestrator.pkg",
        reference=_DRIFTMD_REFERENCE.replace("\nfrom", escape + "\nfrom", 1),
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


def test_driftmd_suite_fails_a_solution_that_drops_the_page_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = _DRIFTMD_REFERENCE.replace('f"## {page}"', '"## Page"').replace("\nfrom", _PLAIN + "\nfrom", 1)
    with pytest.raises(AssertionError):
        _run_suite(
            _suite("NEW-DRIFTMD-1"),
            package="orchestrator.pkg",
            reference=wrong,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
