"""Doctor command: check environment readiness.

The environment groups are defined once in ``ENV_GROUPS`` and shared with
``orchestrator init`` (which scaffolds them) so the readiness check and the
scaffold can never drift apart.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


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
