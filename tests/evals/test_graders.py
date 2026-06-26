"""Independent graders for the persona-skill measurement (P0).

The pure decision logic (finding-count parse, symbol-reuse) is exercised exactly;
the subprocess shells (held-out pytest) are exercised end-to-end against a tiny
real worktree so the runner's write/run/cleanup contract is verified.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.evals.graders import (
    count_semgrep_findings,
    read_source,
    reused_existing_symbols,
    run_held_out_tests,
)


class TestCountSemgrepFindings:
    def test_counts_results(self) -> None:
        assert count_semgrep_findings('{"results": [{"a": 1}, {"b": 2}], "errors": []}') == 2

    def test_empty_results_is_zero(self) -> None:
        assert count_semgrep_findings('{"results": [], "errors": []}') == 0

    def test_tolerates_surrounding_noise(self) -> None:
        # semgrep prints progress lines around its JSON body.
        noisy = 'scanning...\n{"results": [{"x": 1}], "errors": []}\ndone\n'
        assert count_semgrep_findings(noisy) == 1

    def test_garbage_and_empty_degrade_to_zero(self) -> None:
        assert count_semgrep_findings("") == 0
        assert count_semgrep_findings("not json at all") == 0
        assert count_semgrep_findings("[1, 2, 3]") == 0  # a list, not the expected object


class TestReusedExistingSymbols:
    def test_detects_package_import(self) -> None:
        assert reused_existing_symbols("from orchestrator.pkg.stats import GraphStats\n")
        assert reused_existing_symbols("import orchestrator.approval\n")

    def test_detects_relative_import(self) -> None:
        assert reused_existing_symbols("from . import helpers\n")
        assert reused_existing_symbols("from .stats import median\n")

    def test_parallel_reimplementation_is_not_reuse(self) -> None:
        # A self-contained module that imports nothing from the package.
        assert not reused_existing_symbols("import math\n\n\ndef add(a, b):\n    return a + b\n")

    def test_respects_package_argument(self) -> None:
        assert reused_existing_symbols("from myapp.core import x\n", package="myapp")
        assert not reused_existing_symbols("from myapp.core import x\n", package="orchestrator")


class TestReadSource:
    def test_concatenates_and_skips_tests(self, tmp_path: Path) -> None:
        (tmp_path / "feature.py").write_text("from orchestrator import x\n", encoding="utf-8")
        (tmp_path / "test_feature.py").write_text("import orchestrator\n", encoding="utf-8")
        src = read_source([tmp_path / "feature.py", tmp_path / "test_feature.py", tmp_path / "missing.py"])
        assert "from orchestrator import x" in src
        assert "import orchestrator\n" not in src  # the test file was skipped


class TestRunHeldOutTests:
    def test_no_tests_is_not_a_signal(self, tmp_path: Path) -> None:
        result = run_held_out_tests(tmp_path, {})
        assert result.ran is False
        assert result.is_signal is False

    def test_passing_suite_against_a_real_impl(self, tmp_path: Path) -> None:
        # The model's "implementation" lives under src/; the held-out suite (never
        # shown to the model) imports and exercises it.
        src = tmp_path / "src"
        src.mkdir()
        (src / "feature.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        suite = "from feature import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
        result = run_held_out_tests(tmp_path, {"test_held.py": suite})
        assert result.ran is True
        assert result.passed is True
        # The held-out dir is cleaned up — it must not pollute the fit grader.
        assert not (tmp_path / "_heldout").exists()

    def test_failing_held_out_catches_a_thin_impl(self, tmp_path: Path) -> None:
        # A thin impl that only handles the happy path fails the held-out edge case.
        src = tmp_path / "src"
        src.mkdir()
        (src / "feature.py").write_text("def mean(xs):\n    return sum(xs) / len(xs)\n", encoding="utf-8")
        suite = "from feature import mean\n\n\ndef test_empty() -> None:\n    assert mean([]) == 0.0\n"
        result = run_held_out_tests(tmp_path, {"test_held.py": suite})
        assert result.ran is True
        assert result.passed is False
