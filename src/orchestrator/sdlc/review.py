"""Block C/D: code-review seam + the semantic-correctness judge (Track 3.1).

The feature pipeline reviews the generated change through a ``ReviewAdapter``
before opening a PR. A BLOCKER stops the PR and escalates the feature.

``SemanticReviewAdapter`` is the real thing: an LLM judge that reads the
session's generated files and answers the one question regex verifiers can't —
**does this change actually satisfy the spec's acceptance criteria?** Each
criterion gets an explicit met/unmet/uncertain verdict with evidence; any
``unmet`` is a BLOCKER. The judge sees only spec + code — never the codegen
conversation — so it can't rationalise the generator's choices.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from orchestrator.core.llm import LLMClient, Message

logger = logging.getLogger("orchestrator.sdlc.review")

_JUDGE_MODEL = "claude-sonnet-4-6"
_MAX_SOURCE_BYTES = 60_000


@dataclass(frozen=True)
class ReviewResult:
    """A review verdict plus any blocking findings.

    ``verdict`` is one of ``approve`` / ``comment`` / ``request_changes``,
    mirroring the GitHub review states the PR-reviewer wedge uses. A non-empty
    ``blockers`` list (or a ``request_changes`` verdict) blocks the PR.
    """

    verdict: str = "comment"
    blockers: list[str] = field(default_factory=list)
    summary: str = ""
    # Criteria the judge could not confirm — the escalation policy's signal.
    uncertain: list[str] = field(default_factory=list)

    @property
    def has_blocker(self) -> bool:
        return self.verdict == "request_changes" or bool(self.blockers)


@runtime_checkable
class ReviewAdapter(Protocol):
    """Reviews the change in a worktree, returning a verdict + blockers.

    ``spec`` carries the feature spec (acceptance criteria) so a semantic
    reviewer can judge against it; adapters that don't need it ignore it.
    """

    async def review(
        self, *, path: str, issue_key: str, spec: dict[str, Any] | None = None
    ) -> ReviewResult: ...


class StubReviewAdapter:
    """Always COMMENT, never a BLOCKER — the skeleton's no-op reviewer."""

    async def review(self, *, path: str, issue_key: str, spec: dict[str, Any] | None = None) -> ReviewResult:
        _ = (path, issue_key, spec)
        return ReviewResult(verdict="comment", blockers=[], summary="stub review (no blockers)")


_JUDGE_SYSTEM = (
    "You are a strict acceptance reviewer. You are given a feature SPEC (with "
    "acceptance criteria) and the CHANGED FILES that claim to implement it. "
    "Judge ONLY whether the code satisfies each criterion — not style, not "
    "taste.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    '{"criteria": [{"criterion": "<text>", "status": "met"|"unmet"|"uncertain", '
    '"evidence": "<file/function or reason, one line>"}], '
    '"summary": "<one line>"}\n\n'
    "Be adversarial: a criterion is met only if you can point at code that "
    "satisfies it. If the code is missing, wrong, or you cannot tell, say "
    "unmet or uncertain — never give the benefit of the doubt."
)


class SemanticReviewAdapter:
    """LLM judge: does the change satisfy the spec's acceptance criteria?

    Verdict mapping (fail-closed): any ``unmet`` → ``request_changes`` with
    one blocker per unmet criterion; only ``uncertain`` → ``comment`` (human
    attention, not a hard stop); all ``met`` → ``approve``. A spec without
    criteria, an unparseable judge reply, or an empty change cannot approve —
    they fall to ``comment``/``request_changes``, never silently pass.
    """

    def __init__(self, llm: LLMClient, *, model: str = _JUDGE_MODEL) -> None:
        self._llm = llm
        self._model = model

    async def review(self, *, path: str, issue_key: str, spec: dict[str, Any] | None = None) -> ReviewResult:
        criteria = [str(c) for c in ((spec or {}).get("acceptance_criteria") or []) if str(c).strip()]
        if not criteria:
            return ReviewResult(
                verdict="comment",
                summary="semantic review skipped: spec has no acceptance criteria",
            )
        source = _read_source(Path(path))
        if not source:
            return ReviewResult(
                verdict="request_changes",
                blockers=["no source files found in the worktree to judge"],
                summary="semantic review: empty change",
            )

        user = (
            f"Issue: {issue_key}\n\nSPEC ACCEPTANCE CRITERIA:\n"
            + "\n".join(f"- {c}" for c in criteria)
            + f"\n\nCHANGED FILES:\n{source}"
        )
        result = await self._llm.complete(
            [Message(role="system", content=_JUDGE_SYSTEM), Message(role="user", content=user)],
            model=self._model,
        )
        return self._parse(result.text)

    def _parse(self, text: str) -> ReviewResult:
        payload = _loads_json_object(text)
        rows = (payload or {}).get("criteria")
        if not isinstance(rows, list) or not rows:
            logger.warning("sdlc.review.unparseable_judge_output")
            return ReviewResult(
                verdict="comment",
                summary="semantic review: judge output unparseable — needs human review",
            )
        unmet = [r for r in rows if isinstance(r, dict) and r.get("status") == "unmet"]
        uncertain = [r for r in rows if isinstance(r, dict) and r.get("status") == "uncertain"]
        summary = str((payload or {}).get("summary") or "").strip()
        if unmet:
            blockers = [
                f"acceptance criterion unmet: {r.get('criterion')} ({r.get('evidence', '')})" for r in unmet
            ]
            return ReviewResult(verdict="request_changes", blockers=blockers, summary=summary)
        if uncertain:
            names = [str(r.get("criterion")) for r in uncertain]
            return ReviewResult(
                verdict="comment",
                summary=f"{summary} · uncertain: {', '.join(names)}".strip(" ·"),
                uncertain=names,
            )
        return ReviewResult(verdict="approve", summary=summary or "all acceptance criteria met")


def _read_source(root: Path) -> str:
    """This change's ``.py`` files as a labeled prompt block.

    Prefers ``git status`` to find the session's new/changed files (the
    worktree is a real repo checkout); falls back to all ``.py`` files for
    bare directories (Block C's empty-worktree mode).
    """
    import subprocess

    files: list[Path] = []
    # ``-uall`` lists untracked files individually; without it git collapses a
    # brand-new untracked directory to a single ``path/`` entry (no .py
    # suffix), so a feature that creates a NEW package dir is invisible to the
    # reviewer — it sees only the tests and blocks every criterion as
    # "implementation missing" (run #20: src/orchestrator/notify/).
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        for line in proc.stdout.splitlines():
            rel = line[3:].strip().strip('"')
            candidate = root / rel
            if candidate.suffix == ".py" and candidate.exists():
                files.append(candidate)
    if not files:
        files = [p for p in sorted(root.rglob("*.py")) if ".git" not in p.parts]

    chunks: list[str] = []
    budget = _MAX_SOURCE_BYTES
    for file in files:
        try:
            body = file.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"--- {file.resolve().relative_to(root.resolve())} ---\n{body}\n"
        if len(block) > budget:
            break
        budget -= len(block)
        chunks.append(block)
    return "".join(chunks)


def _loads_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
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


__all__ = ["ReviewAdapter", "ReviewResult", "SemanticReviewAdapter", "StubReviewAdapter"]
