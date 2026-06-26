"""Block A.4: code-aware verifiers for the PR reviewer.

Three deterministic scanners that back the LLM reviewer's judgment with
fast, regex-based checks over a PR's added lines:

  - ``SecretsVerifier``: hardcoded credentials (AWS keys, GitHub/Slack
    tokens, private-key headers, generic ``secret = "..."`` assignments).
    Findings are BLOCKERs.
  - ``SecurityVerifier``: dangerous constructs — eval/exec, shell=True,
    unsafe yaml.load / pickle, TLS verification disabled, f-string SQL.
    The clearly-exploitable ones are BLOCKERs; the heuristic ones WARN.
  - ``StyleVerifier``: advisory only — long lines, leftover TODO/FIXME,
    stray ``print(`` in non-test code, trailing whitespace. All NITs.

These are intentionally *not* the runtime ``Verifier`` protocol: Block A
is a standalone, webhook-driven flow with no orchestrator task or
``VerifierContext``. They scan a ``PRDiff`` and return ``Finding``s that
map 1:1 onto GitHub inline review comments (path + line + body). The
review orchestration (Block A.5) merges these with the LLM agent's
findings and derives the overall verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from orchestrator.codereview.diff_utils import iter_added_lines
from orchestrator.codereview.github_client import PRDiff


class Severity(str, Enum):
    BLOCKER = "blocker"  # should block merge → REQUEST_CHANGES
    WARNING = "warning"  # worth fixing, doesn't block
    NIT = "nit"  # advisory / style

    @property
    def rank(self) -> int:
        return {"nit": 0, "warning": 1, "blocker": 2}[self.value]


@dataclass(frozen=True)
class Finding:
    """One issue at a file + line. Maps onto a GitHub inline comment."""

    verifier_id: str
    rule: str
    severity: Severity
    path: str
    line: int
    message: str


class CodeVerifier(Protocol):
    """Lightweight scanner contract for the standalone PR reviewer."""

    verifier_id: str

    def scan(self, diff: PRDiff) -> list[Finding]: ...


# --- helpers ----------------------------------------------------------------

# Extensions we treat as "code" for security/style scans. Secrets scan
# everything (creds hide in config / yaml / env files too).
_CODE_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rb",
    ".java",
    ".rs",
    ".php",
    ".sh",
)


def _is_code_file(path: str) -> bool:
    return path.endswith(_CODE_SUFFIXES)


def _is_test_file(path: str) -> bool:
    low = path.lower()
    return "test" in low or "spec" in low or low.endswith(".test.ts") or low.endswith(".test.js")


# --- SecretsVerifier --------------------------------------------------------


class SecretsVerifier:
    verifier_id = "secrets"

    _PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
        ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}"), "Hardcoded AWS access key id."),
        ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "Hardcoded GitHub token."),
        ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Hardcoded Slack token."),
        (
            "private_key",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
            "Private key committed to the repo.",
        ),
        (
            "generic_secret",
            re.compile(
                r"""(?i)(?:api[_-]?key|secret|token|password|passwd|pwd)\s*[=:]\s*['"]([^'"]{8,})['"]"""
            ),
            "Looks like a hardcoded credential assignment.",
        ),
    )

    # Values that are clearly placeholders, not live credentials. Substring
    # match except ``test``, which needs a word-ish boundary so real secrets
    # containing e.g. "latest" aren't suppressed. Applies only to the
    # ``generic_secret`` heuristic — the structured token rules (AWS/GitHub/
    # Slack/private key) match real credential formats and always block.
    _PLACEHOLDER_VALUE = re.compile(
        r"(?i)(?:dummy|fake|example|sample|placeholder|change[-_]?me|your[-_]"
        r"|not[-_]?a[-_]?real|redacted|xxxx|\.\.\.|<[^>]*>|\$\{|\{\{"
        r"|(?:^|[^a-z])test)"
    )

    def scan(self, diff: PRDiff) -> list[Finding]:
        findings: list[Finding] = []
        for f in diff.files:
            if f.status == "removed" or not f.patch:
                continue
            in_test_file = _is_test_file(f.filename)
            for line_no, content in iter_added_lines(f.patch):
                for rule, pattern, message in self._PATTERNS:
                    match = pattern.search(content)
                    if not match:
                        continue
                    severity = Severity.BLOCKER
                    if rule == "generic_secret":
                        if self._PLACEHOLDER_VALUE.search(match.group(1)):
                            continue
                        if in_test_file:
                            severity = Severity.WARNING
                            message = (
                                "Looks like a hardcoded credential in a test; "
                                "fine if it's a fixture, but never commit live creds."
                            )
                    findings.append(
                        Finding(
                            verifier_id=self.verifier_id,
                            rule=rule,
                            severity=severity,
                            path=f.filename,
                            line=line_no,
                            message=message,
                        )
                    )
        return findings


# --- SecurityVerifier -------------------------------------------------------


class SecurityVerifier:
    verifier_id = "security"

    _RULES: tuple[tuple[str, re.Pattern[str], Severity, str], ...] = (
        (
            "eval_exec",
            re.compile(r"\b(?:eval|exec)\s*\("),
            Severity.BLOCKER,
            "Avoid eval/exec — arbitrary code execution risk.",
        ),
        (
            "shell_true",
            re.compile(r"shell\s*=\s*True"),
            Severity.BLOCKER,
            "subprocess with shell=True invites command injection; pass an args list.",
        ),
        (
            "yaml_load_unsafe",
            re.compile(r"yaml\.load\s*\((?![^)]*Loader)"),
            Severity.BLOCKER,
            "yaml.load without a safe Loader executes arbitrary tags; use yaml.safe_load.",
        ),
        (
            "pickle_loads",
            re.compile(r"pickle\.loads?\s*\("),
            Severity.WARNING,
            "Unpickling untrusted data can execute code; prefer a safe format.",
        ),
        (
            "tls_verify_off",
            re.compile(r"verify\s*=\s*False"),
            Severity.WARNING,
            "TLS verification disabled (verify=False).",
        ),
        (
            "sql_fstring",
            re.compile(r"""(?i)(?:execute|query)\s*\(\s*f['"]"""),
            Severity.WARNING,
            "Possible SQL injection: f-string interpolated into a query; use parameters.",
        ),
    )

    def scan(self, diff: PRDiff) -> list[Finding]:
        findings: list[Finding] = []
        for f in diff.files:
            if f.status == "removed" or not f.patch or not _is_code_file(f.filename):
                continue
            for line_no, content in iter_added_lines(f.patch):
                for rule, pattern, severity, message in self._RULES:
                    if pattern.search(content):
                        findings.append(
                            Finding(
                                verifier_id=self.verifier_id,
                                rule=rule,
                                severity=severity,
                                path=f.filename,
                                line=line_no,
                                message=message,
                            )
                        )
        return findings


# --- StyleVerifier ----------------------------------------------------------


class StyleVerifier:
    verifier_id = "style"

    def __init__(self, *, max_line_length: int = 120) -> None:
        self._max_line = max_line_length
        self._todo = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
        self._print = re.compile(r"(?<![\w.])print\s*\(")

    def scan(self, diff: PRDiff) -> list[Finding]:
        findings: list[Finding] = []
        for f in diff.files:
            if f.status == "removed" or not f.patch or not _is_code_file(f.filename):
                continue
            for line_no, content in iter_added_lines(f.patch):
                if len(content) > self._max_line:
                    findings.append(
                        self._nit(
                            f.filename, line_no, "line_too_long", f"Line exceeds {self._max_line} chars."
                        )
                    )
                if self._todo.search(content):
                    findings.append(
                        self._nit(
                            f.filename, line_no, "leftover_todo", "Leftover TODO/FIXME/XXX in the diff."
                        )
                    )
                if not _is_test_file(f.filename) and self._print.search(content):
                    findings.append(
                        self._nit(
                            f.filename, line_no, "stray_print", "Stray print() in non-test code; use logging."
                        )
                    )
                if content != content.rstrip():
                    findings.append(
                        self._nit(f.filename, line_no, "trailing_whitespace", "Trailing whitespace.")
                    )
        return findings

    def _nit(self, path: str, line: int, rule: str, message: str) -> Finding:
        return Finding(
            verifier_id=self.verifier_id,
            rule=rule,
            severity=Severity.NIT,
            path=path,
            line=line,
            message=message,
        )


def default_code_verifiers() -> list[CodeVerifier]:
    """The standard scan set the PR reviewer runs."""
    return [SecretsVerifier(), SecurityVerifier(), StyleVerifier()]


def run_verifiers(diff: PRDiff, verifiers: list[CodeVerifier] | None = None) -> list[Finding]:
    """Run every verifier over the diff; concatenate findings."""
    chain = verifiers if verifiers is not None else default_code_verifiers()
    findings: list[Finding] = []
    for v in chain:
        findings.extend(v.scan(diff))
    return findings


def worst_severity(findings: list[Finding]) -> Severity | None:
    """Highest severity across findings, or None when there are none."""
    if not findings:
        return None
    return max((f.severity for f in findings), key=lambda s: s.rank)
