"""SemgrepVerifier: result mapping, failure posture, target selection."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.semgrep import SemgrepVerifier
from orchestrator.codereview.verifiers import Severity

PATCH = "@@ -0,0 +1,2 @@\n+import subprocess\n+subprocess.call(cmd, shell=True)\n"


def _diff(filename: str = "app/run.py") -> PRDiff:
    cf = ChangedFile(filename=filename, status="modified", additions=2, deletions=0, patch=PATCH)
    return PRDiff(repo="acme/app", pr_number=1, head_sha="sha", files=(cf,))


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "run.py").write_text("import subprocess\n", encoding="utf-8")
    return tmp_path


def _payload(tmp_path: Path) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "check_id": "python.lang.security.audit.subprocess-shell-true",
                    "path": str(tmp_path / "app" / "run.py"),
                    "start": {"line": 2},
                    "extra": {"severity": "ERROR", "message": "shell=True is dangerous"},
                }
            ]
        }
    )


def test_maps_results_to_anchored_findings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> tuple[int, str]:
        seen.append(argv)
        return 1, _payload(repo)  # rc=1 == findings exist

    (finding,) = SemgrepVerifier(repo, runner=runner).scan(_diff())
    assert finding.verifier_id == "security.semgrep"
    assert finding.severity is Severity.BLOCKER  # ERROR → BLOCKER
    assert (finding.path, finding.line) == ("app/run.py", 2)
    assert "shell=True" in finding.message and "subprocess-shell-true" in finding.message
    assert any(str(repo / "app" / "run.py") in a for a in seen[0])  # scanned the changed file


def test_missing_binary_is_silent(tmp_path: Path) -> None:
    def runner(argv: list[str]) -> tuple[int, str]:
        raise FileNotFoundError("semgrep")

    assert SemgrepVerifier(_repo(tmp_path), runner=runner).scan(_diff()) == []


def test_scanner_fault_surfaces_as_warning(tmp_path: Path) -> None:
    def runner(argv: list[str]) -> tuple[int, str]:
        return 2, "internal error"

    (finding,) = SemgrepVerifier(_repo(tmp_path), runner=runner).scan(_diff())
    assert finding.rule == "scanner_failure" and finding.severity is Severity.WARNING
    assert "did not run" in finding.message


def test_unparseable_output_surfaces_as_warning(tmp_path: Path) -> None:
    def runner(argv: list[str]) -> tuple[int, str]:
        return 0, "not json"

    (finding,) = SemgrepVerifier(_repo(tmp_path), runner=runner).scan(_diff())
    assert finding.rule == "scanner_failure"


def test_skips_removed_noncode_and_missing_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    files = (
        ChangedFile(filename="app/run.py", status="removed", additions=0, deletions=2, patch=PATCH),
        ChangedFile(filename="README.md", status="modified", additions=1, deletions=0, patch="@@ +1 @@\n+x"),
        ChangedFile(filename="ghost.py", status="added", additions=1, deletions=0, patch="@@ +1 @@\n+x"),
    )
    diff = PRDiff(repo="a/b", pr_number=1, head_sha="s", files=files)
    called = False

    def runner(argv: list[str]) -> tuple[int, str]:
        nonlocal called
        called = True
        return 0, "{}"

    assert SemgrepVerifier(repo, runner=runner).scan(diff) == []
    assert not called  # nothing scannable → semgrep never invoked
