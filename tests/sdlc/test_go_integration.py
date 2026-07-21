"""Go toolchain integration: scaffold → real `go build`/`go test` green/red.

Exercises the real GoTestRunner against a scaffolded Go module. Skips cleanly when
the `go` toolchain isn't on PATH, so it runs in CI (setup-go) and on a dev box with Go,
but never blocks the offline suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import go_toolchain_available
from orchestrator.sdlc.testrunner import GoTestRunner

pytestmark = pytest.mark.skipif(not go_toolchain_available(), reason="needs the `go` toolchain on PATH")


def _scaffold_go(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, mode="new", language="go", package_name="calc")
    scaffold(tmp_path, layout)  # go.mod (module calc) + calc.go stub at the root


async def test_scaffold_alone_is_green(tmp_path: Path) -> None:
    # A freshly scaffolded module with no tests still builds and `go test` passes.
    _scaffold_go(tmp_path)
    result = await GoTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output


async def test_go_build_and_test_pass_for_correct_code(tmp_path: Path) -> None:
    _scaffold_go(tmp_path)
    (tmp_path / "add.go").write_text("package calc\n\nfunc Add(a, b int) int { return a + b }\n")
    (tmp_path / "add_test.go").write_text(
        'package calc\n\nimport "testing"\n\n'
        "func TestAdd(t *testing.T) {\n"
        '\tif Add(2, 3) != 5 {\n\t\tt.Fatalf("want 5, got %d", Add(2, 3))\n\t}\n}\n'
    )
    result = await GoTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output


async def test_go_test_fails_for_wrong_code(tmp_path: Path) -> None:
    _scaffold_go(tmp_path)
    (tmp_path / "add.go").write_text("package calc\n\nfunc Add(a, b int) int { return a - b }\n")
    (tmp_path / "add_test.go").write_text(
        'package calc\n\nimport "testing"\n\n'
        "func TestAdd(t *testing.T) {\n"
        '\tif Add(2, 3) != 5 {\n\t\tt.Fatalf("want 5, got %d", Add(2, 3))\n\t}\n}\n'
    )
    result = await GoTestRunner().run(path=str(tmp_path))
    assert not result.passed  # the failing assertion fails `go test`


async def test_go_build_fails_for_compile_error(tmp_path: Path) -> None:
    _scaffold_go(tmp_path)
    (tmp_path / "bad.go").write_text("package calc\n\nfunc Broken() int { return undefinedSymbol }\n")
    result = await GoTestRunner().run(path=str(tmp_path))
    assert not result.passed  # `go build ./...` fails before tests run
