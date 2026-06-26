"""Codebase-auditor persona (Bet 4) — the same machinery, a non-codegen job.

Proves the catalog + agentic loop generalize beyond software-engineering: the
auditor runs a **read-only** think→act→observe loop (query the PKG, read files —
no writes), then submits findings via a terminal tool. Its ``verify`` step is
"every finding resolves to a real ``file:line``"; its delivered artifact is a
findings report. It reuses ``orchestrator.agentic`` wholesale — only the system
prompt, the terminal ``submit_findings`` tool, and the verify/deliver shape are
new.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.agentic import AgentLoop, LoopResult, Tool, build_readonly_tools
from orchestrator.core.llm import LLMClient, ToolSpec

_SEVERITIES = ("blocker", "warning", "note")
_MAX_AUDIT_STEPS = 16


@dataclass(frozen=True)
class Finding:
    title: str
    file: str
    line: int
    severity: str  # blocker | warning | note
    detail: str = ""

    def resolves(self, root: Path) -> bool:
        """True when file:line points at a real location in the repo."""
        target = (root / self.file).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return False
        if not target.is_file():
            return False
        if self.line <= 0:
            return False
        return self.line <= len(target.read_text(encoding="utf-8", errors="replace").splitlines())


@dataclass
class AuditResult:
    summary: str
    findings: list[Finding] = field(default_factory=list)  # validated (resolve to real file:line)
    unresolved: list[Finding] = field(default_factory=list)  # claimed but didn't resolve
    steps: int = 0
    stopped_reason: str = ""
    loop_result: LoopResult | None = None  # the raw run, for export/replay (Bet 2b)

    def by_severity(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.severity, []).append(f)
        return out


@dataclass
class _AuditSession:
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    submitted: bool = False


_FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "file": {"type": "string", "description": "repo-relative path"},
                    "line": {"type": "integer"},
                    "severity": {"type": "string", "enum": list(_SEVERITIES)},
                    "detail": {"type": "string"},
                },
                "required": ["title", "file", "line", "severity"],
            },
        },
    },
    "required": ["summary", "findings"],
}


def _submit_findings_tool(session: _AuditSession) -> Tool:
    async def _run(args: dict[str, object]) -> str:
        session.summary = str(args.get("summary") or "").strip()
        raw = args.get("findings")
        if not isinstance(raw, list):
            return "error: 'findings' must be an array"
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            severity = str(entry.get("severity") or "note")
            session.findings.append(
                Finding(
                    title=str(entry.get("title") or "").strip(),
                    file=str(entry.get("file") or "").strip(),
                    line=int(entry.get("line") or 0),
                    severity=severity if severity in _SEVERITIES else "note",
                    detail=str(entry.get("detail") or "").strip(),
                )
            )
        session.submitted = True
        return f"recorded {len(session.findings)} finding(s)"

    return Tool(
        ToolSpec(
            "submit_findings",
            "Finish the audit: submit findings (each grounded in a real file:line) with a one-line summary.",
            _FINDINGS_SCHEMA,
        ),
        _run,
        terminal=True,
    )


_AUDITOR_SYSTEM = (
    "You are a meticulous code auditor. Investigate the repository using the "
    "read-only tools: start with list_files to see what exists, read_file to "
    "inspect the relevant ones, and pkg_relevant_symbols / pkg_api_surface / "
    "pkg_callers_of to navigate. You may NOT modify anything. When done, call "
    "submit_findings with concrete findings, each anchored to a real file:line "
    "you verified by reading the file. Prefer fewer, well-evidenced findings "
    "over speculation; severity is blocker / warning / note."
)


async def run_audit(
    root: Path | str,
    *,
    llm: LLMClient,
    model: str,
    focus: str = "general code quality, correctness risks, and security",
    max_steps: int = _MAX_AUDIT_STEPS,
) -> AuditResult:
    """Run the read-only audit loop over ``root`` and validate the findings."""
    root_path = Path(root).resolve()
    session = _AuditSession()
    tools = build_readonly_tools(root_path) + [_submit_findings_tool(session)]
    loop = AgentLoop(llm, model=model, tools=tools, max_steps=max_steps, require_terminal=True)
    task = f"Audit this repository. Focus: {focus}."
    result = await loop.run(_AUDITOR_SYSTEM, task)

    resolved = [f for f in session.findings if f.resolves(root_path)]
    unresolved = [f for f in session.findings if not f.resolves(root_path)]
    return AuditResult(
        summary=session.summary or "audit complete",
        findings=resolved,
        unresolved=unresolved,
        steps=result.steps,
        stopped_reason=result.stopped_reason,
        loop_result=result,
    )


@dataclass(frozen=True)
class ExpectedFinding:
    """Ground truth for the eval: an issue the auditor should catch."""

    file: str
    line: int
    label: str = ""


@dataclass
class AuditScore:
    matched: list[ExpectedFinding] = field(default_factory=list)
    missed: list[ExpectedFinding] = field(default_factory=list)
    spurious: int = 0  # findings not matching any expected issue

    @property
    def accepted(self) -> bool:
        return not self.missed  # caught every seeded issue


def score_findings(
    findings: list[Finding],
    expected: list[ExpectedFinding],
    *,
    line_tolerance: int = 3,
) -> AuditScore:
    """Did the auditor catch the seeded issues? Match by file + nearby line."""
    used: set[int] = set()
    score = AuditScore()
    for exp in expected:
        hit = next(
            (
                i
                for i, f in enumerate(findings)
                if i not in used and f.file == exp.file and abs(f.line - exp.line) <= line_tolerance
            ),
            None,
        )
        if hit is None:
            score.missed.append(exp)
        else:
            used.add(hit)
            score.matched.append(exp)
    score.spurious = len(findings) - len(used)
    return score


def make_audit_arm(llm: LLMClient, *, model: str, line_tolerance: int = 3):  # type: ignore[no-untyped-def]
    """An eval arm (Bet 1) for the auditor — task payload: root / focus / expected.

    Acceptance = every seeded ``ExpectedFinding`` is caught. Cost is read from a
    ``RecordingLLMClient`` ledger when one is supplied.
    """
    from orchestrator.core.llm import RecordingLLMClient
    from orchestrator.evals import ArmOutcome, EvalTask

    async def arm(task: EvalTask) -> ArmOutcome:
        expected = [ExpectedFinding(**e) for e in task.payload.get("expected", [])]
        focus = str(task.payload.get("focus") or "correctness risks and security")
        cost0 = llm.ledger.total().cost_usd if isinstance(llm, RecordingLLMClient) else 0.0
        result = await run_audit(task.payload["root"], llm=llm, model=model, focus=focus)
        score = score_findings(result.findings, expected, line_tolerance=line_tolerance)
        cost = (llm.ledger.total().cost_usd - cost0) if isinstance(llm, RecordingLLMClient) else 0.0
        return ArmOutcome(
            accepted=score.accepted,
            cost_usd=cost,
            iterations=result.steps,
            intervened=not score.accepted,
            failure_mode=None if score.accepted else "missed",
            detail=f"{len(score.matched)}/{len(expected)} seeded caught · {score.spurious} other",
        )

    return arm


def render_findings_markdown(result: AuditResult, *, title: str = "Audit findings") -> str:
    lines = [f"# {title}", "", result.summary, ""]
    by_sev = result.by_severity()
    if not result.findings:
        lines.append("No findings.")
    for severity in _SEVERITIES:
        items = by_sev.get(severity, [])
        if not items:
            continue
        lines.append(f"## {severity.capitalize()} ({len(items)})")
        for f in items:
            lines.append(f"- **{f.title}** — `{f.file}:{f.line}`" + (f" — {f.detail}" if f.detail else ""))
        lines.append("")
    if result.unresolved:
        lines.append(
            f"_{len(result.unresolved)} claimed finding(s) dropped — did not resolve to a real file:line._"
        )
    return "\n".join(lines).rstrip() + "\n"


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
