"""Block A.5: review orchestration.

Turns a PR into a posted GitHub review by composing the pieces from
A.1-A.4:

  - ``LLMReviewer``: one LLM call over the diff, parsed into ``Finding``s.
    The judgment layer — correctness/design/clarity issues the regex
    verifiers can't see.
  - ``build_review_submission``: merges LLM findings with the A.4 verifier
    findings, drops/relocates comments that don't anchor to a changed line
    (GitHub rejects out-of-diff comments and would fail the whole review),
    and derives the verdict (any BLOCKER → REQUEST_CHANGES, else COMMENT —
    never auto-APPROVE from a bot).
  - ``ReviewService``: fetch diff → LLM + verifiers → submit review →
    audit. This is what ``_dispatch_review`` (the A.1 webhook stub) calls.

The LLM call is direct (like the planner's), not via the orchestrator
task machinery — Block A is standalone. The full SDLC pipeline can later
run agent.code_reviewer through the runtime instead; the template
(examples/templates/code_reviewer.yaml) documents that contract.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from orchestrator.codereview.diff_utils import iter_added_lines
from orchestrator.codereview.github_client import (
    GitHubClient,
    PRDiff,
    ReviewComment,
    ReviewSubmission,
    ReviewVerdict,
)
from orchestrator.codereview.verifiers import (
    CodeVerifier,
    Finding,
    Severity,
    default_code_verifiers,
    worst_severity,
)
from orchestrator.core.llm import CompletionResult, LLMClient, Message

logger = logging.getLogger("orchestrator.codereview.reviewer")

_REVIEWER_MODEL = "claude-sonnet-4-6"
_MAX_INLINE_COMMENTS = 30  # cap so a noisy diff doesn't spam one review

_SYSTEM_PROMPT = (
    "You are a senior code reviewer. You are given a pull request's diff. "
    "Review ONLY the added/changed lines. Identify issues across correctness, "
    "design, security, and clarity.\n\n"
    "Output a single JSON object, no prose, no code fences:\n"
    '{"summary": "<one paragraph>", "findings": [{"path": "<file>", '
    '"line": <int line in NEW file on a changed line>, '
    '"severity": "blocker"|"warning"|"nit", '
    '"category": "correctness"|"design"|"security"|"clarity", '
    '"message": "<specific, actionable>"}]}\n\n'
    "blocker = must fix (bug/security/data loss); warning = should fix; "
    "nit = optional. Anchor each finding to a line that appears as added in "
    "the diff. Be concise; no praise. Empty findings is correct when clean."
)


class ReviewGrounder(Protocol):
    """Supplies PKG-grounded impact context for a diff (see ``grounding.py``)."""

    def brief_for_diff(self, diff: PRDiff) -> str: ...


class ImpactFindingSource(Protocol):
    """Supplies anchored PKG impact findings for a diff (see ``grounding.py``)."""

    def findings_for_diff(self, diff: PRDiff) -> list[Finding]: ...


class LLMReviewer:
    """Runs the code_reviewer agent as a single structured LLM call.

    When a ``grounder`` is supplied, the prompt is augmented with a
    Product-Knowledge-Graph impact brief — the cross-file callers of the
    changed symbols — so the model can flag breaking changes it could never
    see from the diff alone.
    """

    def __init__(
        self, llm: LLMClient, *, model: str = _REVIEWER_MODEL, grounder: ReviewGrounder | None = None
    ) -> None:
        self._llm = llm
        self._model = model
        self._grounder = grounder

    async def review(self, diff: PRDiff) -> tuple[str, list[Finding]]:
        """Return (summary, findings) from the model's read of the diff."""
        user = self._build_user_message(diff)
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user),
        ]
        result: CompletionResult = await self._llm.complete(messages, model=self._model)
        return self._parse(result.text)

    def _build_user_message(self, diff: PRDiff) -> str:
        header = f"PR {diff.repo}#{diff.pr_number} (head {diff.head_sha[:8]})"
        if diff.truncated:
            header += " — NOTE: file list truncated; some files not shown."
        grounding = self._grounder.brief_for_diff(diff) if self._grounder is not None else ""
        prefix = f"{grounding}\n\n" if grounding else ""
        return f"{prefix}{header}\n\nDiff:\n{diff.diff_text}"

    def _parse(self, text: str) -> tuple[str, list[Finding]]:
        """Parse the model's JSON. Malformed output degrades gracefully to
        an empty review rather than crashing the webhook path."""
        payload = _loads_json_object(text)
        if payload is None:
            logger.warning("codereview.reviewer.unparseable_output")
            return ("Reviewer produced no parseable output.", [])
        summary = str(payload.get("summary") or "")
        findings: list[Finding] = []
        for raw in payload.get("findings") or []:
            finding = _finding_from_llm(raw)
            if finding is not None:
                findings.append(finding)
        return summary, findings


def _loads_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        # tolerate a fenced block despite the instruction not to use one
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            loaded = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return loaded if isinstance(loaded, dict) else None


def _finding_from_llm(raw: Any) -> Finding | None:
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path") or "").strip()
    if not path:
        return None
    raw_line = raw.get("line")
    if not isinstance(raw_line, (int, str)):
        return None
    try:
        line = int(raw_line)
    except (TypeError, ValueError):
        return None
    severity = _coerce_severity(raw.get("severity"))
    category = str(raw.get("category") or "review")
    message = str(raw.get("message") or "").strip()
    if not message:
        return None
    return Finding(
        verifier_id="code_reviewer",
        rule=category,
        severity=severity,
        path=path,
        line=line,
        message=message,
    )


def _coerce_severity(raw: Any) -> Severity:
    try:
        return Severity(str(raw).lower())
    except ValueError:
        return Severity.WARNING


def _valid_anchors(diff: PRDiff) -> set[tuple[str, int]]:
    """The (path, line) pairs that GitHub will accept an inline comment on —
    i.e. lines added in the diff."""
    anchors: set[tuple[str, int]] = set()
    for f in diff.files:
        if not f.patch:
            continue
        for line_no, _ in iter_added_lines(f.patch):
            anchors.add((f.filename, line_no))
    return anchors


def build_review_submission(diff: PRDiff, findings: list[Finding], summary: str) -> ReviewSubmission:
    """Merge findings into a single review: inline comments for findings that
    anchor to a changed line, a summary body for everything else + the verdict.
    """
    anchors = _valid_anchors(diff)
    inline: list[ReviewComment] = []
    floating: list[Finding] = []
    for f in sorted(findings, key=lambda x: (-x.severity.rank, x.path, x.line)):
        if (f.path, f.line) in anchors and len(inline) < _MAX_INLINE_COMMENTS:
            inline.append(ReviewComment(path=f.path, line=f.line, body=_format_comment(f)))
        else:
            floating.append(f)

    verdict = _verdict(findings)
    body = _format_summary(summary, findings, floating, truncated=diff.truncated)
    return ReviewSubmission(verdict=verdict, summary=body, comments=inline)


def _verdict(findings: list[Finding]) -> ReviewVerdict:
    worst = worst_severity(findings)
    if worst is Severity.BLOCKER:
        return ReviewVerdict.REQUEST_CHANGES
    # Warnings/nits/clean don't block — and a bot never auto-APPROVEs
    # (would satisfy branch protection on its own; humans keep that gate).
    return ReviewVerdict.COMMENT


def _format_comment(f: Finding) -> str:
    return f"**[{f.severity.value}/{f.verifier_id}:{f.rule}]** {f.message}"


def _format_summary(
    summary: str, findings: list[Finding], floating: list[Finding], *, truncated: bool
) -> str:
    counts = {sev: sum(1 for f in findings if f.severity is sev) for sev in Severity}
    lines = ["## Automated review"]
    if summary:
        lines += ["", summary]
    lines += [
        "",
        f"**Findings:** {counts[Severity.BLOCKER]} blocker, "
        f"{counts[Severity.WARNING]} warning, {counts[Severity.NIT]} nit.",
    ]
    if floating:
        lines += ["", "Issues not anchored to a diff line:"]
        lines += [f"- `{f.path}` {f.message}" for f in floating]
    if truncated:
        lines += ["", "_Note: the PR's file list was truncated; some files were not reviewed._"]
    return "\n".join(lines)


# Audit callback shape: (action, resource_id, payload) -> awaitable.
AuditFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class ReviewService:
    """Orchestrates a full PR review and posts it back to GitHub."""

    def __init__(
        self,
        *,
        github: GitHubClient,
        llm_reviewer: LLMReviewer,
        verifiers: list[CodeVerifier] | None = None,
        impact_source: ImpactFindingSource | None = None,
        audit: AuditFn | None = None,
    ) -> None:
        self._github = github
        self._reviewer = llm_reviewer
        self._verifiers = verifiers if verifiers is not None else default_code_verifiers()
        self._impact_source = impact_source
        self._audit = audit

    async def _compute(
        self, *, installation_id: int, repo: str, pr_number: int
    ) -> tuple[PRDiff, ReviewSubmission, list[Finding]]:
        """Fetch the diff and compute the review (LLM + verifiers). No write."""
        diff = await self._github.fetch_pr_diff(
            installation_id=installation_id, repo=repo, pr_number=pr_number
        )
        summary, llm_findings = await self._reviewer.review(diff)
        verifier_findings: list[Finding] = []
        for v in self._verifiers:
            verifier_findings.extend(v.scan(diff))
        impact_findings = self._impact_source.findings_for_diff(diff) if self._impact_source else []
        all_findings = [*impact_findings, *verifier_findings, *llm_findings]
        return diff, build_review_submission(diff, all_findings, summary), all_findings

    async def preview_pull_request(
        self, *, installation_id: int, repo: str, pr_number: int
    ) -> tuple[PRDiff, ReviewSubmission]:
        """Compute the review *without* posting it.

        Safe for live-integration probing — no write to the PR.
        ``review_pull_request`` reuses the same computation, then posts +
        audits.
        """
        diff, submission, _ = await self._compute(
            installation_id=installation_id, repo=repo, pr_number=pr_number
        )
        return diff, submission

    async def review_pull_request(
        self, *, installation_id: int, repo: str, pr_number: int
    ) -> ReviewSubmission:
        diff, submission, all_findings = await self._compute(
            installation_id=installation_id, repo=repo, pr_number=pr_number
        )

        await self._github.submit_review(
            installation_id=installation_id,
            repo=repo,
            pr_number=pr_number,
            head_sha=diff.head_sha,
            submission=submission,
        )

        if self._audit is not None:
            await self._audit(
                "pr_reviewed",
                f"{repo}#{pr_number}",
                {
                    "verdict": submission.verdict.value,
                    "inline_comments": len(submission.comments),
                    "blocker": sum(1 for f in all_findings if f.severity is Severity.BLOCKER),
                    "warning": sum(1 for f in all_findings if f.severity is Severity.WARNING),
                    "nit": sum(1 for f in all_findings if f.severity is Severity.NIT),
                    "head_sha": diff.head_sha,
                    "truncated": diff.truncated,
                },
            )
        return submission
