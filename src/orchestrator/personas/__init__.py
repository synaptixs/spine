"""Personas — non-SWE jobs on the same catalog + agentic-loop machinery (Bet 4).

The agentic loop, gates, and comprehension aren't codegen-specific. A *persona*
supplies its own tools, verify step, and delivered artifact. First: the
codebase ``auditor`` (read-only loop → findings report).
"""

from orchestrator.personas.auditor import (
    AuditResult,
    AuditScore,
    ExpectedFinding,
    Finding,
    make_audit_arm,
    render_findings_markdown,
    run_audit,
    score_findings,
)

__all__ = [
    "AuditResult",
    "AuditScore",
    "ExpectedFinding",
    "Finding",
    "make_audit_arm",
    "render_findings_markdown",
    "run_audit",
    "score_findings",
]
