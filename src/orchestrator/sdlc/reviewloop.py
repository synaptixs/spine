"""Review the change, fix what it found, prove the fix — bounded, and honest about stopping.

The pipeline could already review a pull request and revise one on demand. What it could not
do is *close the loop*: nothing took a review's findings, fixed them, re-ran the tests, and
reviewed again. A human sat in that gap.

Three rules shape this, and they are the difference between a loop and a spin:

**Bounded.** Rounds are capped. A model that cannot fix something in two attempts is not
going to fix it in five; it is going to rewrite more each time, which is how a small
correction becomes an unreviewable diff.

**A round that changes nothing ends it.** Identical to the refine loop's rule, for the same
reason: if the fixer edited no file, the next review sees the same code and returns the same
findings. Spending a second call to watch that happen is waste, and spending three is a bug.

**A fix that breaks the tests is not a fix.** If the suite was green and a review fix turns it
red, the loop stops and says so rather than continuing to "improve" a broken change. The
regression is the finding at that point.

Some findings are not the fixer's to act on — a design objection, a "this whole approach is
wrong". Those are returned as *deferred* with their evidence, for a human or a ticket, rather
than fed to a model that will dutifully paper over them.

Reviewing works on the **worktree diff**, not a GitHub pull request: the loop has to run
before a PR exists, which is the whole point of fixing things first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.verifiers import Finding, Severity, run_verifiers

# Two rounds. The first fixes what review found; the second proves the fix and catches what
# the fix broke. A third has, in practice, meant the model is rewriting rather than repairing.
DEFAULT_MAX_ROUNDS = 2

# Severities worth interrupting a run for. A nit is real feedback and belongs on the PR for a
# human to weigh — it is not worth an LLM call and a re-test cycle.
_ACTIONABLE = (Severity.BLOCKER, Severity.WARNING)


class Reviewer(Protocol):
    """The LLM half of a review. Optional: verifiers alone still find real defects."""

    async def review(self, diff: PRDiff) -> tuple[str, list[Finding]]: ...


class Fixer(Protocol):
    """Whatever can edit the worktree — in practice the codegen adapter's ``refine``."""

    async def refine(self, *, spec: dict[str, Any], path: str, issue_key: str, failures: str) -> Any: ...


class Tests(Protocol):
    async def run(self, *, path: str) -> Any: ...


@dataclass(frozen=True)
class Round:
    """What one pass of review-and-fix did."""

    number: int
    findings: int
    fixed_files: tuple[str, ...] = ()
    tests_passed: bool | None = None
    note: str = ""


@dataclass
class LoopResult:
    """Where the loop stopped, and why — never just 'done'."""

    rounds: list[Round] = field(default_factory=list)
    remaining: list[Finding] = field(default_factory=list)
    deferred: list[Finding] = field(default_factory=list)
    stopped: str = ""
    clean: bool = False

    def render(self) -> str:
        lines = [f"**Review loop:** {self.stopped}", ""]
        for r in self.rounds:
            detail = f"{r.findings} finding(s)"
            if r.fixed_files:
                detail += f" · edited {', '.join(r.fixed_files)}"
            if r.tests_passed is not None:
                detail += f" · tests {'passed' if r.tests_passed else 'FAILED'}"
            if r.note:
                detail += f" · {r.note}"
            lines.append(f"- round {r.number}: {detail}")
        if self.remaining:
            lines += ["", "**Unresolved:**"]
            lines += [f"- {f.severity.value} {f.path}:{f.line} — {f.message}" for f in self.remaining]
        if self.deferred:
            lines += ["", "**Not the fixer's to decide** (for a human or a ticket):"]
            lines += [f"- {f.severity.value} {f.path}:{f.line} — {f.message}" for f in self.deferred]
        return "\n".join(lines)


async def worktree_diff(path: Path | str, *, base: str = "HEAD~1") -> PRDiff:
    """The change under review, read from git rather than from a forge.

    ``pr_number=0`` marks it as a local diff: the reviewer only reads files and patches, and
    a loop that waited for a pull request could never fix anything *before* opening one.
    """
    files: list[ChangedFile] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--unified=3",
            base,
            cwd=str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        # The worktree is gone, or was never a directory. Nothing to review is a fact, not a
        # crash — a reaped or relocated worktree must not take the run down with it.
        return PRDiff(repo="", pr_number=0, head_sha="", files=())
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return PRDiff(repo="", pr_number=0, head_sha="", files=())

    current: str | None = None
    buffer: list[str] = []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("diff --git "):
            if current:
                files.append(_changed_file(current, buffer))
            current = line.split(" b/", 1)[-1]
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        files.append(_changed_file(current, buffer))
    return PRDiff(repo="", pr_number=0, head_sha="", files=tuple(files))


def _changed_file(filename: str, lines: list[str]) -> ChangedFile:
    patch = "\n".join(lines)
    return ChangedFile(
        filename=filename,
        status="modified",
        additions=sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
        deletions=sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
        patch=patch,
    )


def _is_deferred(finding: Finding) -> bool:
    """Findings a fixer should not be handed.

    A design objection ("this belongs in another module", "the approach is wrong") is not
    something to patch — asking a model to satisfy it produces a change that answers the
    words rather than the concern. It goes to a human with its evidence intact.
    """
    text = f"{finding.rule} {finding.message}".lower()
    return any(
        marker in text
        for marker in ("design", "architecture", "approach", "belongs in", "out of scope", "rethink")
    )


async def review_and_fix(
    *,
    path: Path | str,
    spec: dict[str, Any],
    issue_key: str,
    fixer: Fixer | None = None,
    tests: Tests | None = None,
    reviewer: Reviewer | None = None,
    base: str = "HEAD~1",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> LoopResult:
    """Review the worktree, fix what is actionable, re-test, and review again — bounded."""
    result = LoopResult()

    for number in range(1, max_rounds + 1):
        diff = await worktree_diff(path, base=base)
        if not diff.files:
            result.stopped = "nothing to review — the worktree has no diff"
            result.clean = True
            return result

        findings = list(run_verifiers(diff))
        if reviewer is not None:
            _, llm_findings = await reviewer.review(diff)
            findings.extend(llm_findings)

        deferred = [f for f in findings if _is_deferred(f)]
        actionable = [f for f in findings if f.severity in _ACTIONABLE and f not in deferred]
        result.deferred = deferred

        if not actionable:
            result.rounds.append(Round(number=number, findings=len(findings), note="nothing to fix"))
            result.remaining = [f for f in findings if f not in deferred]
            result.stopped = "review clean" if not findings else "only advisory findings remain"
            result.clean = True
            return result

        if fixer is None:
            result.rounds.append(Round(number=number, findings=len(findings), note="no fixer"))
            result.remaining = actionable
            result.stopped = "findings reported — no fixer wired"
            return result

        change = await fixer.refine(
            spec=spec, path=str(path), issue_key=issue_key, failures=_render_findings(actionable)
        )
        edited = tuple(getattr(change, "files", ()) or ())
        if not edited:
            # Identical to the refine loop's rule: nothing changed, so the next review sees
            # the same code and says the same thing.
            result.rounds.append(Round(number=number, findings=len(findings), note="fix changed nothing"))
            result.remaining = actionable
            result.stopped = "the fixer edited nothing — not fixable by editing code"
            return result

        passed: bool | None = None
        if tests is not None:
            outcome = await tests.run(path=str(path))
            passed = bool(getattr(outcome, "passed", False))
            if not passed:
                result.rounds.append(
                    Round(number=number, findings=len(findings), fixed_files=edited, tests_passed=False)
                )
                result.remaining = actionable
                result.stopped = "a review fix broke the tests — stopping rather than building on it"
                return result

        result.rounds.append(
            Round(number=number, findings=len(findings), fixed_files=edited, tests_passed=passed)
        )

    # Out of rounds with work still outstanding. Say so: silence here reads as success.
    diff = await worktree_diff(path, base=base)
    findings = list(run_verifiers(diff))
    result.remaining = [f for f in findings if f.severity in _ACTIONABLE]
    result.stopped = f"review budget spent after {max_rounds} round(s)"
    result.clean = not result.remaining
    return result


def _render_findings(findings: list[Finding]) -> str:
    """Findings as the fixer's input. Typed data rendered at the boundary, not prose passed
    around — the loop needs to count and compare them, a model needs to read them."""
    lines = ["The reviewer found these issues in the change. Fix them:"]
    for f in findings:
        lines.append(f"- [{f.severity.value}] {f.path}:{f.line} ({f.rule}) — {f.message}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "LoopResult",
    "Round",
    "review_and_fix",
    "worktree_diff",
]
