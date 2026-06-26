"""TypeScript toolchain integration: scaffold → real npm install → Vitest green/red.

Exercises the real NodeToolEnvironment + NodeTestRunner against a scaffolded
Vitest project. Skips cleanly when Node + npm aren't on PATH, so it runs in CI
(setup-node) and on a dev box with the toolchain, but never blocks the offline
suite. It shells out to ``npm install`` (network), mirroring the Java
integration test's real ``mvn test``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import NodeToolEnvironment, node_toolchain_available
from orchestrator.sdlc.testrunner import NodeTestRunner

pytestmark = pytest.mark.skipif(not node_toolchain_available(), reason="needs Node.js + npm on PATH")


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


async def _scaffold_and_install(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, mode="new", language="typescript", package_name="demo")
    scaffold(tmp_path, layout)
    await NodeToolEnvironment().ensure(tmp_path)  # npm install (vitest + tsc)


_SOURCE = "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
_TEST = (
    'import { describe, it, expect } from "vitest";\n'
    'import { add } from "./calc.js";\n\n'
    'describe("add", () => {\n  it("sums", () => {\n    expect(add(2, 3)).toBe(5);\n  });\n});\n'
)


async def test_vitest_passes_for_correct_ts(tmp_path: Path) -> None:
    await _scaffold_and_install(tmp_path)
    _write(tmp_path / "src/calc.ts", _SOURCE)
    _write(tmp_path / "src/calc.test.ts", _TEST)
    result = await NodeTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output


async def test_vitest_fails_for_wrong_ts(tmp_path: Path) -> None:
    await _scaffold_and_install(tmp_path)
    _write(tmp_path / "src/calc.ts", _SOURCE.replace("a + b", "a - b"))
    _write(tmp_path / "src/calc.test.ts", _TEST)
    result = await NodeTestRunner().run(path=str(tmp_path))
    assert not result.passed  # the failing Vitest assertion fails the run
