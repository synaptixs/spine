"""Track 3.2: Semgrep security verifier — AST-aware scanning beyond regexes.

``SemgrepVerifier`` plugs into the review chain as a ``CodeVerifier``. For a
PR it scans the *changed files* (resolved against a local checkout) with the
``semgrep`` CLI and maps results onto anchored ``Finding`` rows: ERROR →
BLOCKER, WARNING → WARNING, INFO → NIT.

Failure posture, chosen deliberately:
- **semgrep not installed** → log + no findings. An optional scanner that
  isn't deployed must not block every review (install via the ``security``
  extra: ``pip install "agent-orchestrator[security]"``).
- **semgrep present but errored** → one WARNING finding. A scanner that was
  supposed to run and didn't is itself a reviewable fact, never silent.
- Exit code 1 is semgrep's "findings exist" — success, not an error.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.codereview.github_client import PRDiff
from orchestrator.codereview.verifiers import Finding, Severity

logger = logging.getLogger("orchestrator.codereview.semgrep")

_SEVERITY_MAP = {"ERROR": Severity.BLOCKER, "WARNING": Severity.WARNING, "INFO": Severity.NIT}
_SCAN_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".php", ".c", ".cpp")

# (returncode, stdout) — injectable for tests; FileNotFoundError = no binary.
Runner = Callable[[list[str]], tuple[int, str]]


def _default_runner(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=300, check=False)
    return proc.returncode, proc.stdout


class SemgrepVerifier:
    """Run semgrep over a PR's changed files in a local checkout."""

    verifier_id = "security.semgrep"

    def __init__(
        self,
        root: Path | str,
        *,
        config: str = "auto",
        runner: Runner = _default_runner,
    ) -> None:
        self._root = Path(root)
        self._config = config
        self._runner = runner

    def scan(self, diff: PRDiff) -> list[Finding]:
        targets = self._targets(diff)
        if not targets:
            return []
        argv = [
            "semgrep",
            "scan",
            "--json",
            "--quiet",
            "--disable-version-check",
            "--config",
            self._config,
            *[str(self._root / t) for t in targets],
        ]
        try:
            returncode, stdout = self._runner(argv)
        except FileNotFoundError:
            logger.info("codereview.semgrep.not_installed")
            return []
        except (subprocess.TimeoutExpired, OSError) as exc:
            return [self._failure_finding(targets[0], f"semgrep did not complete: {exc}")]

        if returncode not in (0, 1):  # 1 = findings exist; anything else is a scanner fault
            return [self._failure_finding(targets[0], f"semgrep exited {returncode}")]
        payload = self._loads(stdout)
        if payload is None:
            return [self._failure_finding(targets[0], "semgrep produced unparseable JSON")]
        return self._findings(payload)

    # ---- internals ----------------------------------------------------------

    def _targets(self, diff: PRDiff) -> list[str]:
        out: list[str] = []
        for f in diff.files:
            if f.status == "removed" or not f.patch:
                continue
            if not f.filename.endswith(_SCAN_SUFFIXES):
                continue
            if (self._root / f.filename).exists():
                out.append(f.filename)
        return out

    def _findings(self, payload: dict[str, Any]) -> list[Finding]:
        findings: list[Finding] = []
        for item in payload.get("results") or []:
            extra = item.get("extra") or {}
            check_id = str(item.get("check_id") or "semgrep")
            path = str(item.get("path") or "")
            rel = self._relative(path)
            findings.append(
                Finding(
                    verifier_id=self.verifier_id,
                    rule=check_id.rsplit(".", 1)[-1],
                    severity=_SEVERITY_MAP.get(str(extra.get("severity", "")).upper(), Severity.WARNING),
                    path=rel,
                    line=int((item.get("start") or {}).get("line") or 1),
                    message=f"{str(extra.get('message') or check_id).strip()} [{check_id}]",
                )
            )
        return findings

    def _relative(self, path: str) -> str:
        try:
            return str(Path(path).resolve().relative_to(self._root.resolve()))
        except ValueError:
            return path

    def _failure_finding(self, path: str, message: str) -> Finding:
        logger.warning("codereview.semgrep.failed", extra={"detail": message})
        return Finding(
            verifier_id=self.verifier_id,
            rule="scanner_failure",
            severity=Severity.WARNING,
            path=path,
            line=1,
            message=f"Security scan did not run: {message}. Review without semgrep coverage.",
        )

    @staticmethod
    def _loads(text: str) -> dict[str, Any] | None:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None


__all__ = ["SemgrepVerifier"]
