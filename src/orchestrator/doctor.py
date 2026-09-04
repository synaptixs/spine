"""Doctor command: check environment readiness.

The environment groups are defined once in ``ENV_GROUPS`` and shared with
``orchestrator init`` (which scaffolds them) so the readiness check and the
scaffold can never drift apart.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EnvVar:
    """One environment variable plus a one-line hint for the scaffold."""

    name: str
    hint: str = ""


@dataclass(frozen=True)
class EnvGroup:
    """A related set of env vars checked together.

    ``any_of`` groups pass when at least one var is set (e.g. an LLM provider);
    otherwise every var is required. ``optional`` groups never block readiness —
    when unset they report as skipped (e.g. Mode-B-only config the local CLI
    doesn't need), so a developer using only the CLI still sees a green report.
    """

    name: str
    variables: tuple[EnvVar, ...]
    any_of: bool = False
    optional: bool = False
    note: str = ""


# Single source of truth for environment configuration — doctor checks these,
# init scaffolds them.
ENV_GROUPS: tuple[EnvGroup, ...] = (
    EnvGroup(
        "LLM provider",
        (
            EnvVar("ANTHROPIC_API_KEY", "Anthropic API key (claude-* models)"),
            EnvVar("OPENAI_API_KEY", "OpenAI API key (gpt-* models)"),
            EnvVar(
                "OLLAMA_API_BASE",
                "Ollama endpoint for ollama/* models — http://localhost:11434 (local) "
                "or a hosted URL (cloud); no API key needed",
            ),
        ),
        any_of=True,
        note=(
            "At least one provider is required. Ollama (local or cloud) needs no key — set "
            "OLLAMA_API_BASE and use an ollama/* model (e.g. ORCHESTRATOR_INTAKE_MODEL=ollama/qwen2.5-coder)."
        ),
    ),
    EnvGroup(
        "Orchestrator API",
        (
            EnvVar("ORCHESTRATOR_API_URL", "Base URL of the orchestrator API, e.g. http://localhost:8000"),
            EnvVar("ORCHESTRATOR_API_KEY", "API key clients present as X-API-Key"),
        ),
        optional=True,
        note="only needed for Mode B (the REST API + console); the local CLI doesn't use it",
    ),
    EnvGroup(
        "Confluence",
        (
            EnvVar("CONFLUENCE_BASE_URL", "e.g. https://your-org.atlassian.net/wiki"),
            EnvVar("CONFLUENCE_EMAIL", "Atlassian account email"),
            EnvVar("CONFLUENCE_API_TOKEN", "Atlassian API token"),
        ),
    ),
    EnvGroup(
        "Jira",
        (
            EnvVar("JIRA_BASE_URL", "e.g. https://your-org.atlassian.net"),
            EnvVar("JIRA_EMAIL", "Atlassian account email"),
            EnvVar("JIRA_API_TOKEN", "Atlassian API token"),
            EnvVar("JIRA_PROJECT_KEY", "Target Jira project key, e.g. ENG"),
        ),
    ),
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    optional: bool = False  # an unset optional group: passed (non-blocking) + skipped


def check_group(group: EnvGroup, env: Mapping[str, str]) -> CheckResult:
    """Evaluate one ``EnvGroup`` against ``env``.

    An optional group that isn't (fully) configured still ``passed`` — it can't
    block readiness — but is flagged ``optional`` so the report shows it as
    skipped rather than OK.
    """
    names = [v.name for v in group.variables]
    if group.any_of:
        present = [n for n in names if env.get(n)]
        if present:
            return CheckResult(group.name, True, f"Found: {', '.join(present)}")
        if group.optional:
            return CheckResult(group.name, True, f"Not set — {group.note}", optional=True)
        return CheckResult(group.name, False, f"Missing: {', '.join(names)} (at least one required)")
    missing = [n for n in names if not env.get(n)]
    if not missing:
        return CheckResult(group.name, True, f"All {group.name} variables are set")
    if group.optional:
        unset = (
            f"Not set — {group.note}"
            if len(missing) == len(names)
            else f"Missing: {', '.join(missing)} ({group.note})"
        )
        return CheckResult(group.name, True, unset, optional=True)
    return CheckResult(group.name, False, f"Missing: {', '.join(missing)}")


def run_env_checks(env: Mapping[str, str] | None = None) -> list[CheckResult]:
    """Return a ``CheckResult`` for each configured environment group."""
    if env is None:
        env = os.environ
    return [check_group(group, env) for group in ENV_GROUPS]


#: Which importable module proves an optional extra is installed. One probe per extra —
#: the first package the extra pulls in that nothing else does — so the answer is
#: "is this extra present", not "is some dependency present".
EXTRA_PROBES: Mapping[str, str] = {
    "mcp": "mcp",
    "java": "tree_sitter_java",
    "typescript": "tree_sitter_typescript",
    "csharp": "tree_sitter_c_sharp",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "go": "tree_sitter_go",
    "sql": "sqlglot",
    "docs": "pypdf",
    "office": "docx",
    "sdlc": "pytest",
    "security": "semgrep",
    "otel": "opentelemetry.sdk",
}

PACKAGE = "synaptixs-spine"


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def server_identity() -> dict[str, Any]:
    """Who is answering: the package version, the interpreter, the SDK, the extras.

    The readiness checks say whether the *environment* is ready; this says which
    *install* is doing the checking. The case it exists for: a host launched an
    ``orchestrator-mcp`` console script left behind by an older checkout's venv —
    Spine 3.9.3 with no ``mcp`` module — and the only symptom was "Connection
    closed". With the interpreter path and the version in the report, that is a
    one-line diagnosis instead of an afternoon.
    """
    return {
        "package": PACKAGE,
        "version": _dist_version(PACKAGE),
        "python": platform.python_version(),
        "interpreter": sys.executable,
        "mcp_sdk": _dist_version("mcp"),
        "extras": {extra: _importable(module) for extra, module in EXTRA_PROBES.items()},
    }


def _importable(module: str) -> bool:
    """``find_spec`` without the trap: for a dotted name it imports the parent first, and
    raises ``ModuleNotFoundError`` when that parent is absent — which is exactly the case
    an "is this extra installed" probe exists to answer with ``False``."""
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def render_identity(identity: Mapping[str, Any]) -> str:
    """Three lines a human can compare against ``pip show`` / ``which``."""
    version = identity.get("version") or "not installed as a distribution"
    sdk = identity.get("mcp_sdk")
    extras = identity.get("extras") or {}
    present = ", ".join(sorted(k for k, v in extras.items() if v)) or "none"
    return "\n".join(
        [
            f"{identity.get('package')} {version} · python {identity.get('python')} · "
            f"mcp sdk {sdk or 'not installed'}",
            f"interpreter: {identity.get('interpreter')}",
            f"extras: {present}",
        ]
    )


def render_report(results: Sequence[CheckResult]) -> str:
    """Render a human-readable diagnostic report string."""
    lines: list[str] = ["Orchestrator environment report", "=" * 40]
    all_passed = all(r.passed for r in results)
    for r in results:
        status = "FAIL" if not r.passed else ("SKIP" if r.optional else "OK ")
        line = f"[{status}] {r.name}"
        if r.detail:
            line += f": {r.detail}"
        lines.append(line)
    lines.append("=" * 40)
    skipped = [r.name for r in results if r.passed and r.optional]
    if all_passed:
        lines.append("All checks passed.")
        if skipped:
            lines.append(f"Optional (not set): {', '.join(skipped)}")
    else:
        failed = [r.name for r in results if not r.passed]
        lines.append(f"{len(failed)} check(s) failed: {', '.join(failed)}")
    return "\n".join(lines)
