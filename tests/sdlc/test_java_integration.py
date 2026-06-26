"""Java toolchain integration: scaffold → real Maven build → JUnit green/red.

Exercises the real MavenTestRunner against a scaffolded Maven project. Skips
cleanly when a JDK + Maven aren't on PATH, so it runs in CI (setup-java + the
runner's pre-installed Maven) and on a dev box with the toolchain, but never
blocks the offline suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import java_toolchain_available
from orchestrator.sdlc.testrunner import MavenTestRunner

pytestmark = pytest.mark.skipif(not java_toolchain_available(), reason="needs a JDK + Maven on PATH")


def _scaffold_java(tmp_path: Path) -> str:
    layout = resolve_layout(tmp_path, mode="new", language="java", package_name="com.demo")
    scaffold(tmp_path, layout)
    return layout.source_dir  # src/main/java/com/demo


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


async def test_maven_build_passes_for_correct_java(tmp_path: Path) -> None:
    src = _scaffold_java(tmp_path)
    _write(
        tmp_path / src / "Calc.java",
        "package com.demo;\n\npublic class Calc {\n    public int add(int a, int b) { return a + b; }\n}\n",
    )
    _write(
        tmp_path / "src/test/java/com/demo" / "CalcTest.java",
        "package com.demo;\n\nimport org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
        "class CalcTest {\n    @Test void adds() { assertEquals(5, new Calc().add(2, 3)); }\n}\n",
    )
    result = await MavenTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output


async def test_maven_build_fails_for_wrong_java(tmp_path: Path) -> None:
    src = _scaffold_java(tmp_path)
    _write(
        tmp_path / src / "Calc.java",
        "package com.demo;\n\npublic class Calc {\n    public int add(int a, int b) { return a - b; }\n}\n",
    )
    _write(
        tmp_path / "src/test/java/com/demo" / "CalcTest.java",
        "package com.demo;\n\nimport org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
        "class CalcTest {\n    @Test void adds() { assertEquals(5, new Calc().add(2, 3)); }\n}\n",
    )
    result = await MavenTestRunner().run(path=str(tmp_path))
    assert not result.passed  # the failing JUnit assertion fails the Maven build
