"""Reusable single-intent SDLC feature runner.

Extracted from the ``sdlc feature`` CLI so the same pipeline is callable from
the CLI, the MCP plugin, and elsewhere: source → spec → (Jira) → worktree →
grounded codegen → test/refine → commit → (push + PR). Emits progress via a
``log`` callback and returns a ``FeatureRunResult``; raises ``FeatureRunError``
(carrying a CLI-style exit ``code``) instead of printing/exiting, so callers
own presentation and error mapping.

``live=False`` (safe) makes **no external writes** — dry-run Jira, a local
commit, no push. ``live=True`` creates a real Jira issue, pushes a branch, and
opens a PR — unless ``issue`` names one that already exists, in which case the
run adopts it and creates nothing.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Test-run byproducts that should never appear in a feature's changed-files
# summary (or, ideally, its commit) regardless of the repo's .gitignore.
_BUILD_DIRS = {
    "target",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".gradle",
    ".idea",
    ".pytest_cache",
    ".mypy_cache",
}


# A tracker key: project part, hyphen, number ("SSPN-9"). Checked before intake
# runs so a typo costs nothing — the *existence* check needs the tracker and
# happens at the Jira step, still ahead of the workspace, codegen and any tests.
_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")


class FeatureRunError(RuntimeError):
    """A feature run can't proceed. ``code`` mirrors the CLI exit code."""

    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _validated_issue_key(issue_key: str | None) -> str:
    """Normalize an adopted issue key, or "" when the run should create one."""
    key = (issue_key or "").strip().upper()
    if key and not _ISSUE_KEY.match(key):
        raise FeatureRunError(f"{issue_key!r} is not an issue key (expected e.g. PROJ-123).", code=2)
    return key


@dataclass
class FeatureRunResult:
    passed: bool
    intent_id: str
    issue_key: str
    title: str
    branch: str
    worktree: str
    grounding_chars: int
    iterations: int
    live: bool
    files: list[str] = field(default_factory=list)
    pr_url: str | None = None
    # The adapter and runner that built this change, so a later stage can fix what review
    # finds *with the same tools* — same layout, same conventions, same test environment.
    # Rebuilding them elsewhere would review one change and fix a differently-configured one.
    codegen: Any = None
    tests: Any = None


async def _prove_the_tests_test_something(
    path: Path, files: list[str], runner: Any, emit: Callable[[str], None]
) -> None:
    """Revert the production change and re-run the model's own tests. They must fail.

    The model writes the tests that judge its work, so "tests pass" proves only that its
    tests agree with its code. A suite that passes *without* the change proves nothing at
    all — and a run that produced no behaviour change would sail through it.

    Deterministic and free: one test run, no model call. A check that cannot be performed
    (nothing stashable, a stash that will not apply) is reported and skipped rather than
    failing the run — an unproven suite is a weaker claim, not a broken change.
    """
    production = [f for f in files if not _is_test_path(f)]
    if not production or not any(_is_test_path(f) for f in files):
        return

    stashed, why = await _git_out(path, "stash", "push", "--include-untracked", "--quiet", "--", *production)
    if not stashed:
        emit(f"[proof] could not set the change aside — skipping the without-it test run: {why}")
        return
    try:
        result = await runner.run(path=str(path))
        if getattr(result, "passed", False):
            emit(
                "[proof] WARNING: the generated tests pass without the change — they do not "
                "exercise it, so a green suite says nothing about this work"
            )
        else:
            emit("[proof] the tests fail without the change, as they must")
    finally:
        await _git(path, "stash", "pop", "--quiet")


_MAX_COVERAGE_PROBES = 4  # files this check may probe in one pass
_MAX_COVERAGE_FIXES = 2  # attempts at writing the missing test before giving up


async def _files_no_test_exercises(
    path: Path, files: list[str], runner: Any, emit: Callable[[str], None]
) -> list[str]:
    """Revert each changed production file **on its own**. Any that leaves the suite green
    is a file nothing tests.

    ``_prove_the_tests_test_something`` reverts the whole change at once, which only proves
    the tests depend on *some* part of it. That passed on a change whose helper was correct,
    fully tested, and wired into the CLI through ``hasattr(t.handler, "tool")`` — a guard
    that is always False, so every argument printed ``any``. Reverting everything broke the
    helper's import and the suite went red, so the proof "held" while the line that mattered
    was covered by nothing.

    Per-file is the version that catches it: revert ``cli.py`` alone, and if the tests still
    pass, the wiring in ``cli.py`` is untested — which is exactly what "the criteria are
    behavioural and nothing runs the command" looks like from the outside.
    """
    production = [f for f in files if not _is_test_path(f)]
    if not production or not any(_is_test_path(f) for f in files):
        return []
    if len(production) > _MAX_COVERAGE_PROBES:
        emit(f"[coverage] {len(production)} production files — probing the first {_MAX_COVERAGE_PROBES}")
        production = production[:_MAX_COVERAGE_PROBES]

    unexercised: list[str] = []
    unprobed: list[str] = []
    for target in production:
        stashed, why = await _git_out(path, "stash", "push", "--include-untracked", "--quiet", "--", target)
        if not stashed:
            # Never a silent skip. This exact path reported "every changed file is
            # exercised" for a whole run while stashing nothing, because an earlier
            # stage had left intent-to-add entries in the index and every push failed.
            unprobed.append(f"{Path(target).name} ({why.splitlines()[0] if why else 'stash failed'})")
            continue
        try:
            result = await runner.run(path=str(path))
        finally:
            await _git(path, "stash", "pop", "--quiet")
        if getattr(result, "passed", False):
            unexercised.append(target)
    if unprobed:
        emit(f"[coverage] COULD NOT probe {len(unprobed)} file(s) — not a pass: {'; '.join(unprobed)}")
    if unexercised:
        names = ", ".join(Path(f).name for f in unexercised)
        emit(f"[coverage] nothing exercises the change in: {names}")
    elif not unprobed:
        emit("[coverage] every changed file is exercised by a test")
    return unexercised


async def _release_the_ticket(move: Callable[[str], Any], emit: Callable[[str], None]) -> None:
    """Hand a ticket back after a run that produced nothing.

    Best-effort and never fatal, like every other transition: a board that has no "To Do"
    to return to is a fact about the board, not a reason to fail work that already failed.
    Says plainly when it could not, so nobody assumes the ticket was tidied up.
    """
    try:
        await move("To Do")
    except Exception as exc:  # noqa: BLE001 — a tracker quirk must not mask the real failure
        emit(f"[jira] left In Progress — could not hand the ticket back: {exc}")


async def _typecheck_the_change(
    path: Path, testenv: Any, files: list[str], emit: Callable[[str], None]
) -> str:
    """Run the target repo's own type checker over the lines this change touched.

    The pipeline generated code for a repo whose stated gate is ``mypy src tests`` and never
    ran it. Two separate runs committed a file missing ``from typing import Any``, and a
    third wired the display through ``t.handler.input_schema`` on a class that has no such
    attribute — every one of them a single deterministic line of mypy output, and every one
    of them shipped past green tests, because the generated tests exercise new helpers
    directly and never import the CLI path they were supposed to change.

    Scoped to **changed lines**, not the repo. A worktree's venv carries runtime deps only,
    so a whole-project run there reports hundreds of pre-existing errors that no generated
    change caused and no refine pass could fix — a gate that fails every run is a gate that
    gets removed. Returns "" when the change is clean or the check cannot be made.
    """
    if not any(f.endswith(".py") for f in files):
        return ""
    ok, _ = await _exec(path, testenv.python, "-c", "import mypy")
    if not ok:
        # The worktree venv is built from runtime deps only, so the repo's own checker is
        # never in it — which made the first version of this check skip on every real run
        # and catch precisely nothing. Install it once, the way a missing test dep is
        # installed, rather than quietly doing nothing.
        emit("[typecheck] installing mypy into the worktree env")
        await testenv.install(["mypy"])
        ok, _ = await _exec(path, testenv.python, "-c", "import mypy")
    if not ok:
        emit("[typecheck] mypy unavailable — skipping (not failing) the check")
        return ""

    touched = await _changed_line_ranges(path)
    if not touched:
        return ""
    _, output = await _exec(path, testenv.python, "-m", "mypy", *sorted(touched))
    hits = [
        line
        for line in output.splitlines()
        if ": error:" in line and _error_is_on_a_changed_line(line, touched) and not _is_typing_hygiene(line)
    ]
    if not hits:
        emit("[typecheck] clean on the changed lines")
        return ""
    emit(f"[typecheck] {len(hits)} error(s) introduced by this change")
    return "TYPE ERRORS introduced by this change (fix these):\n" + "\n".join(hits)


async def _exec(path: Path, *argv: str) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode == 0, out.decode("utf-8", "replace")


async def _changed_line_ranges(path: Path) -> dict[str, set[int]]:
    """``{relative path: {line numbers this change added}}``, from the worktree diff.

    Reads the index; never writes it. The first version ran ``git add -N -A`` so untracked
    files would appear in ``git diff``, and intent-to-add entries make every subsequent
    ``git stash push`` fail with *"Entry ... not uptodate. Cannot merge."* That silently
    disabled both checks that stash — the proof pass reported "could not set the change
    aside" for three runs, and the coverage probe skipped every file and announced that all
    of them were exercised. A check that cannot run must never look like a check that
    passed, and the surest way to avoid it is not to touch shared state at all.
    """
    untracked = await _untracked_python_files(path)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        "-U0",
        # Against HEAD, not the index: the proof check stashes and pops, and the gate may run
        # after something has been staged. Plain `git diff` would see none of that and report
        # an empty change, which reads as "nothing to check" rather than "checked nothing".
        "HEAD",
        cwd=str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    ranges: dict[str, set[int]] = {}
    current = ""
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            if current.endswith(".py"):
                ranges.setdefault(current, set())
        elif line.startswith("@@") and current in ranges:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                ranges[current].update(range(start, start + int(match.group(2) or 1)))
    # A brand-new file has no diff against HEAD, and it is exactly where generated code
    # lands — so every line of it counts as changed.
    for rel in untracked:
        try:
            total = len((path / rel).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        if total:
            ranges[rel] = set(range(1, total + 1))
    return {k: v for k, v in ranges.items() if v}


async def _untracked_python_files(path: Path) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "ls-files",
        "--others",
        "--exclude-standard",
        cwd=str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return [f for f in out.decode("utf-8", "replace").split("\n") if f.endswith(".py")]


# Codes that say "this annotation could be tidier", not "this code is wrong". A worktree venv
# has runtime deps only, so `import-*` there reports the environment rather than the change;
# the rest are hygiene. A run spent two of its three refine passes chasing an unused
# `type: ignore` in a generated test while the real `attr-defined` bug went unfixed.
_TYPING_HYGIENE = frozenset(
    {
        "unused-ignore",
        "no-untyped-def",
        "no-untyped-call",
        "no-any-return",
        "type-arg",
        "import-not-found",
        "import-untyped",
        "misc",
    }
)


def _is_typing_hygiene(line: str) -> bool:
    match = re.search(r"\[([a-z-]+)\]\s*$", line)
    return match is not None and match.group(1) in _TYPING_HYGIENE


def _error_is_on_a_changed_line(line: str, touched: dict[str, set[int]]) -> bool:
    match = re.match(r"^([^:]+):(\d+):", line)
    if not match:
        return False
    return int(match.group(2)) in touched.get(match.group(1), set())


async def _judge_against_the_criteria(
    llm: Any, *, path: Path, issue_key: str, spec: dict[str, Any], emit: Callable[[str], None]
) -> Any:
    """Does the change satisfy the ticket — not merely its own tests?

    Regex verifiers check for secrets and style; the test suite checks the code against tests
    the same model wrote. Neither asks the only question that matters to the person who filed
    the ticket. A run once shipped a green, reviewed change that touched the wrong module
    entirely and left the requested behaviour exactly as it was.

    The judge reads spec and code, never the codegen conversation, so it cannot rationalise
    the generator's choices. Returns the verdict; acting on it is the caller's job.
    """
    from orchestrator.sdlc.review import SemanticReviewAdapter

    with llm.stage("semantic_review"):
        verdict = await SemanticReviewAdapter(llm).review(path=str(path), issue_key=issue_key, spec=spec)
    if verdict.uncertain:
        emit(f"[judge] uncertain on {len(verdict.uncertain)}: {'; '.join(verdict.uncertain[:2])}")
    if verdict.verdict == "request_changes":
        emit(f"[judge] REQUEST_CHANGES — {'; '.join(verdict.blockers) or verdict.summary}")
    else:
        emit(f"[judge] {verdict.verdict} — {verdict.summary or 'criteria met'}")
    return verdict


async def _satisfy_the_ticket(
    llm: Any,
    *,
    codegen: Any,
    runner: Any,
    testenv: Any,
    path: Path,
    issue_key: str,
    spec: dict[str, Any],
    max_revisions: int,
    max_repairs: int,
    emit: Callable[[str], None],
) -> None:
    """Judge the change, and give it a bounded chance to answer.

    The judge arrived able only to veto: a rejection ended the run outright. That made any
    criterion the generator was never told to satisfy an unwinnable ticket — most visibly a
    request for documentation, which every implement prompt forbids ("write source files
    only") and which the judge then failed the run for missing. Re-running walked into the
    same wall, deterministically, forever.

    A red suite already had this: failures go back to ``refine``. This is the same shape for
    the other kind of failure, with two conditions a test-failure loop does not need — the
    suite is re-run after each revision, because a change made to satisfy a reviewer must not
    break the code, and a revision that edits nothing stops the loop, because the next judge
    call would be asked about byte-identical files.

    A revision that *does* break the suite is repaired rather than fatal (see
    ``_repair_after_revision``); only a break that survives repair ends the run.
    """
    from orchestrator.sdlc.testenv import run_with_autoheal

    blockers: list[str] = []
    for attempt in range(max_revisions + 1):
        verdict = await _judge_against_the_criteria(llm, path=path, issue_key=issue_key, spec=spec, emit=emit)
        if getattr(verdict, "unreviewed", False):
            # No verdict at all is not a pass. This ran: the judge's reply was unreadable,
            # mapped to `comment`, and the run committed a change whose only new function
            # was never called. Revising cannot help — there is nothing to revise toward —
            # so it stops here for a human rather than looping.
            raise FeatureRunError(
                "VERDICT: FAILED — the acceptance judge returned no readable verdict, so "
                "this change is unreviewed. Not proceeding.",
                code=1,
            )
        if verdict.verdict != "request_changes":
            return
        blockers = list(verdict.blockers) or [verdict.summary]
        if attempt >= max_revisions:
            break
        with llm.stage("revise"):
            change = await codegen.revise(spec=spec, path=str(path), issue_key=issue_key, blockers=blockers)
        emit(f"[revise] {[Path(f).name for f in change.files]} - {change.summary}")
        if not change.files:
            emit("[revise] no file changes — the judge's points are not answerable by editing; stopping")
            break
        result = await run_with_autoheal(runner, testenv, str(path), emit=emit)
        emit(f"[run_tests after revise] passed={result.passed} rc={result.returncode}")
        if not result.passed:
            # Shipping here would trade a documentation gap for broken code — but ending
            # the run is not the only alternative. A red suite after a revision is the
            # ordinary refine case, and refine is right there: the live failure was one
            # stray keyword argument to a model that forbids extras, a one-line repair
            # the run had every tool to make and no path to making.
            repaired = await _repair_after_revision(
                llm,
                codegen=codegen,
                runner=runner,
                testenv=testenv,
                path=path,
                issue_key=issue_key,
                spec=spec,
                failures=result.output,
                max_passes=max_repairs,
                emit=emit,
            )
            if not repaired:
                raise FeatureRunError(
                    "VERDICT: FAILED — the revision answering the reviewer broke the test "
                    "suite and could not be repaired.",
                    code=1,
                )
    raise FeatureRunError(
        f"VERDICT: FAILED — the change does not satisfy the ticket: {'; '.join(blockers)}", code=1
    )


async def _repair_after_revision(
    llm: Any,
    *,
    codegen: Any,
    runner: Any,
    testenv: Any,
    path: Path,
    issue_key: str,
    spec: dict[str, Any],
    failures: str,
    max_passes: int,
    emit: Callable[[str], None],
) -> bool:
    """Get the suite green again after a revision broke it. True if it recovered.

    Deliberately ``refine`` and not another ``revise``: the reviewer's point is already
    addressed in the worktree, and what is wrong now is that the code does not run. Asking
    the acceptance-criteria prompt to fix a stack trace aims the model at the wrong problem
    — and refine already forbids editing tests to make a broken implementation look right,
    which is the failure mode that matters when a model is under pressure to stay green.
    """
    from orchestrator.sdlc.testenv import run_with_autoheal

    for _ in range(max_passes):
        with llm.stage("refine"):
            change = await codegen.refine(spec=spec, path=str(path), issue_key=issue_key, failures=failures)
        emit(f"[repair] {[Path(f).name for f in change.files]} - {change.summary}")
        if not change.files:
            emit("[repair] no file changes — the break is not fixable by editing; stopping")
            return False
        result = await run_with_autoheal(runner, testenv, str(path), emit=emit)
        emit(f"[run_tests after repair] passed={result.passed} rc={result.returncode}")
        if result.passed:
            return True
        failures = result.output
    return False


def _is_test_path(rel: str) -> bool:
    name = Path(rel).name
    return name.startswith("test_") or name.endswith("_test.py") or "tests/" in rel.replace("\\", "/")


async def _git(path: Path, *args: str) -> bool:
    ok, _ = await _git_out(path, *args)
    return ok


async def _git_out(path: Path, *args: str) -> tuple[bool, str]:
    """Run git, and keep what it said when it failed.

    stderr used to go to ``DEVNULL``, so "could not set the change aside" was the whole
    diagnosis for three runs. Git's own message named the cause in one line.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    return proc.returncode == 0, err.decode("utf-8", "replace").strip()


async def _local_commit(path: Path, message: str) -> None:
    """Stage + commit everything in the worktree via exec (no shell)."""
    for argv in (["git", "add", "-A"], ["git", "commit", "-m", message]):
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        await proc.communicate()


async def _changed_files(path: Path) -> list[str]:
    """Files this run actually added/changed in the worktree (scaffold + generated),
    via ``git status`` — so the summary excludes pre-existing repo content that a
    whole-tree scan would surface (e.g. loose files already in the target repo).
    Falls back to a ``*.py`` scan when ``path`` isn't a git repo."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=all",  # expand new dirs to individual files, not just "src/"
        cwd=str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode == 0:
        changed: set[str] = set()
        for line in out.decode("utf-8", "replace").splitlines():
            if len(line) <= 3:
                continue
            rel = line[3:]  # strip the two-char status + space
            if " -> " in rel:  # a rename reports "old -> new"; keep the new path
                rel = rel.split(" -> ", 1)[1]
            rel = rel.strip().strip('"')
            # Drop build/cache/venv output — these are test-run byproducts, not the
            # change (e.g. Maven target/, a stray .sdlc-venv when a repo's .gitignore
            # doesn't cover them, or .NET bin/ obj/ nested under a project dir).
            segments = rel.split("/")
            if segments[0] in _BUILD_DIRS or "bin" in segments or "obj" in segments:
                continue
            changed.add(rel)
        return sorted(changed)
    # Not a git repo (e.g. a stubbed test worktree): best-effort source scan.
    return [str(p.relative_to(path)) for p in sorted(path.rglob("*.py"))]


# Languages the codegen pipeline supports (each has a layout resolver, scaffold, prompt
# set, test env + runner). `--language auto` detects from the worktree. Anything outside
# this set is rejected at the CLI — historically an unknown value silently fell through
# to the Python branch and scaffolded a Python project.
SUPPORTED_LANGUAGES = frozenset({"python", "java", "typescript", "csharp", "c", "cpp", "go", "sql"})


def unsupported_language_error(language: str) -> str | None:
    """A user-facing error message if ``language`` is neither ``auto`` nor supported, else
    ``None``. Lives here (next to ``SUPPORTED_LANGUAGES``) so the constant is *used* within
    this module — the CLI calls this rather than importing the raw frozenset."""
    if language == "auto" or language in SUPPORTED_LANGUAGES:
        return None
    return (
        f"--language '{language}' is not supported. "
        f"Choose auto or one of: {', '.join(sorted(SUPPORTED_LANGUAGES))}."
    )


def _resolve_language(path: Path, requested: str) -> str:
    """Resolve ``--language`` (``auto`` → detect from the worktree).

    A non-Python language wins only when it's present and Python isn't, so
    mixed/empty repos default to python. Java is checked before TypeScript."""
    if requested != "auto":
        return requested
    from orchestrator.catalog.profile import ProjectProfile

    langs = ProjectProfile.from_repo(path).languages
    if "python" not in langs:
        if "java" in langs:
            return "java"
        if "typescript" in langs:
            return "typescript"
        if "csharp" in langs:
            return "csharp"
        if "go" in langs:
            return "go"
        if "cpp" in langs:
            return "cpp"
        if "c" in langs:
            return "c"
    return "python"


async def run_feature(
    source: str,
    *,
    intent_id: str | None = None,
    repo: str | None = None,
    model: str | None = None,
    # Raised from 3 when the type checker joined this loop: a red suite and a type error now
    # draw on the same budget, and a run that fixed its tests on the first pass used to be
    # done where it now still has the checker to satisfy. Three was exactly one short.
    max_refine: int = 5,
    # Answering a reviewer is a smaller job than debugging a red suite, and each pass costs a
    # codegen call plus a full test run. Two is enough for the common case (one missed
    # criterion, one correction) without letting a model argue with the judge indefinitely.
    max_judge_revisions: int = 2,
    # A revision that reddens the suite gets a repair pass rather than ending the run. Small
    # on purpose: this is "undo the one thing you just broke", not a second debugging budget,
    # and it is spent inside a revision that has already cost a codegen call and a test run.
    max_revision_repairs: int = 2,
    # Asked once, after every automatic check has passed and before the first write. Returning
    # False stops the run with nothing committed. ``None`` keeps the pipeline unattended.
    gate: Callable[[Path, list[str]], Awaitable[bool]] | None = None,
    live: bool = False,
    issue: str | None = None,
    design: str = "",
    budget: Any = None,
    ledger: Any = None,
    post_worklog: bool = True,
    base_branch: str | None = None,
    layout_mode: str = "auto",
    package_name: str | None = None,
    language: str = "auto",
    refresh: bool = False,
    spec: dict[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> FeatureRunResult:
    """Build one intent end to end. See module docstring for safe vs live.

    ``spec`` injects a pre-built spec (title / summary / acceptance_criteria) and
    skips intake — used by Spine remediation (drift → governed run from a task spec).

    ``issue`` adopts an issue that already exists instead of creating one, so
    a run can be pointed at the ticket the work is actually for. Without it the
    issue is created as before.

    ``design`` is what an earlier stage already produced (`sdlc autorun`), carried
    into every codegen prompt. Empty by default: a standalone run builds exactly the
    prompt it builds today.
    """
    emit: Callable[[str], None] = log or (lambda _m: None)
    adopt_key = _validated_issue_key(issue)

    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient, RecordingLLMClient
    from orchestrator.intake.backlog_doc import backlog_path, write_backlog
    from orchestrator.intake.cache import analyze_cached, load_progress, set_progress
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.jira import IssueTrackerError, JiraAdapter, JiraConfig
    from orchestrator.intake.service import parse_source_uri
    from orchestrator.sdlc.codegen import LLMCodegenAdapter, resolve_codegen_model
    from orchestrator.sdlc.forge import GhPRAdapter
    from orchestrator.sdlc.grounding import PKGCodegenGrounder
    from orchestrator.sdlc.layout import is_effectively_empty, resolve_layout
    from orchestrator.sdlc.scaffold import scaffold
    from orchestrator.sdlc.telemetry import jira_duration, render_worklog
    from orchestrator.sdlc.testenv import (
        c_toolchain_available,
        cpp_toolchain_available,
        detect_dotnet_tfm,
        dotnet_toolchain_available,
        go_toolchain_available,
        java_toolchain_available,
        make_test_environment,
        make_test_runner,
        meson_toolchain_available,
        node_toolchain_available,
        run_with_autoheal,
    )
    from orchestrator.sdlc.testrunner import pytest_available
    from orchestrator.sdlc.workspace import WorkspaceManager

    started_at = time.monotonic()
    load_local_env()
    # Wrap in RecordingLLMClient — the OTel chokepoint (emits an llm.complete span
    # per call) + per-stage token ledger. Drop-in (implements LLMClient), so the
    # linear CLI path is now traced like the worker path.
    # A supervisor may hand us a spend cap. Wrapping *under* the recorder keeps the ledger
    # and the OTel spans intact — the budget refuses the call, the recorder still sees every
    # call that happened. Without a budget this is exactly the client it always was.
    inner: Any = LiteLLMClient()
    if budget is not None:
        from orchestrator.core.llm import BudgetedLLMClient

        inner = BudgetedLLMClient(inner, budget)
    # A supervisor may own the ledger across several stages — the review loop's fixes are
    # LLM calls too, and they happen after this function returns. Sharing the ledger is what
    # lets one worklog account for the whole run instead of the middle of it.
    llm = RecordingLLMClient(inner, ledger=ledger) if ledger is not None else RecordingLLMClient(inner)

    # 1. Obtain the spec. Normally: source → intents → specs (intake, cached +
    #    temperature-0 so a pinned --intent stays addressable). When a spec is
    #    injected (Spine remediation: drift → governed run), skip intake entirely.
    # Bound before the branch, because the live path below reads them either way. An
    # injected spec (Spine remediation, `sdlc autorun`) skips intake entirely, so there is no
    # backlog *plan* behind it — the ledger is an intake artifact, and refreshing it from
    # nothing would be inventing one.
    local_backlog = backlog_path()
    plan: Any = None

    if spec is None:
        parse_source_uri(source)  # validate the source URI early
        try:
            service = build_service_for(source, dry_run=True)
        except IntakeNotConfiguredError as exc:
            raise FeatureRunError(str(exc), code=2) from exc
        plan = await analyze_cached(service, source, refresh=refresh, log=emit)
        if not plan.specs:
            raise FeatureRunError("No specs derived from the source — nothing to implement.", code=3)
        # Refresh the local canonical backlog ledger (BACKLOG.md) with progress.
        write_backlog(local_backlog, source, plan, load_progress(source))
        emit(f"[backlog] {local_backlog}")
        spec_obj = (
            next((s for s in plan.specs if s.intent_id == intent_id), None) if intent_id else plan.specs[0]
        )
        if spec_obj is None:
            ids = ", ".join(s.intent_id for s in plan.specs)
            raise FeatureRunError(f"Intent {intent_id!r} not found. Available: {ids}", code=3)
        spec = spec_obj.model_dump()
    else:
        spec = dict(spec)
        spec.setdefault("intent_id", intent_id or "remediation")
        spec.setdefault("summary", "")
        spec.setdefault("acceptance_criteria", [])
        emit(f"[spec] injected (intake skipped): {spec.get('title', '')}")

    # Fail fast BEFORE creating a Jira issue we couldn't follow through on
    # (a live run with no repo to push to would otherwise orphan the issue).
    repo_url = repo or os.getenv("SDLC_REPO_URL") or None
    if live and not repo_url:
        raise FeatureRunError("live needs a repo to push to (pass repo or set SDLC_REPO_URL).", code=2)

    emit("=" * 70)
    emit(f"SPEC: {spec['title']}")
    emit(f"  intent: {spec['intent_id']}")
    emit(f"  summary: {spec['summary']}")
    for criterion in spec["acceptance_criteria"]:
        emit(f"    - {criterion}")
    emit("=" * 70)

    # 2. Jira issue (real only with live; otherwise a synthetic dry-run key).
    #    With an adopted key the run creates nothing: the branch, PR, comment and
    #    transition all land on the ticket the work is already tracked under.
    #    Creating unconditionally is what minted duplicate, epic-less issues — and
    #    stranded a real one whenever a run failed after the create.
    jira = JiraAdapter(JiraConfig(dry_run=not live))
    # Same renderer the intake path uses, so a spec produces the same issue body
    # whichever command created it (this used to hand-roll a summary-plus-criteria
    # version that dropped technical notes, NFRs and dependencies on the floor).
    from orchestrator.intake.service import spec_to_issue_request

    if adopt_key:
        try:
            tracker_issue = await jira.get_issue(adopt_key)
        except IssueTrackerError as exc:
            # Before the workspace, before codegen, before a test run: an
            # unreachable ticket is a wrong run, not a late surprise.
            raise FeatureRunError(f"cannot adopt {adopt_key}: {exc}", code=2) from exc
        emit(
            f"[jira] adopted issue: {tracker_issue.key} {tracker_issue.url}".rstrip()
            if live
            else f"[jira] adopted issue (safe — not verified): {tracker_issue.key}"
        )
    else:
        tracker_issue = await jira.create_issue(spec_to_issue_request(spec))
        emit(
            f"[jira] {'created' if live else 'dry-run'} issue: "
            f"{tracker_issue.key} {tracker_issue.url}".rstrip()
        )
    issue_key = tracker_issue.key

    async def move(status: str) -> None:
        """Drive the ticket's status. Never fatal: a tracker's workflow is not the run's to
        fail on, and a missing status says something about the board rather than the work."""
        if not live:
            return
        try:
            moved = await jira.transition_issue(issue_key, status)
            if moved:
                emit(f"[jira] moved {issue_key} → {moved}")
        except IssueTrackerError as exc:
            emit(f"[jira] could not move {issue_key} to {status}: {exc}")

    # In Progress *now*, not when the work is finished. A ticket that sits in To Do through
    # design, codegen and the whole test loop tells nobody that anything is happening — and
    # a run that dies at codegen leaves no sign it was ever picked up.
    await move("In Progress")

    async def log_run_cost(verdict: str) -> None:
        """Post what this run spent onto the issue it was working.

        Telemetry never fails the work: a tracker that rejects the worklog leaves a line in
        the log and nothing else. Safe mode posts nothing — ``add_worklog`` honors dry-run,
        and the guard keeps even the render off the path.
        """
        if not live or not post_worklog:
            # A supervisor that owns the ledger posts once, at the end of the whole run.
            # Posting here as well would bill the ticket twice for the same tokens.
            return
        try:
            await jira.add_worklog(
                issue_key,
                time_spent=jira_duration(time.monotonic() - started_at),
                comment=render_worklog(llm.ledger, seconds=time.monotonic() - started_at, verdict=verdict),
            )
            total = llm.ledger.total()
            emit(f"[jira] worklog on {issue_key}: {total.total_tokens:,} tokens, {total.calls} call(s)")
        except (IssueTrackerError, OSError) as exc:
            emit(f"[jira] could not log run cost on {issue_key}: {exc}")

    # 3. worktree branch off the real repo (or a scratch repo in safe/no-repo mode).
    sdlc_id = uuid.uuid4().hex[:16]
    ws_root = Path(os.getenv("SDLC_WORKSPACE_ROOT", "/tmp/sdlc-workspaces"))
    path = await WorkspaceManager(root=ws_root, repo_url=repo_url).create(sdlc_id, issue_key)
    branch = f"feat/{sdlc_id}/{issue_key}"
    emit(f"[workspace] worktree {path} on {branch}")

    # 3b. Resolve the target layout; scaffold a fresh structure for greenfield
    #     repos (auto/new) so generated files land coherently. Brownfield
    #     (existing package) is detected and reused — never scaffolded.
    lang = _resolve_language(path, language)
    layout = resolve_layout(path, mode=layout_mode, package_name=package_name, repo=repo_url, language=lang)
    if lang == "csharp":
        # Target the installed SDK so a greenfield scaffold builds AND runs (a TFM
        # with no matching runtime fails at the test host, not at build).
        layout = replace(layout, target_framework=detect_dotnet_tfm())
    if layout.mode == "new":
        was_empty = is_effectively_empty(path)
        created = scaffold(path, layout)
        layout = replace(layout, scaffolded=bool(created))
        emit(
            f"[scaffold] created {created}" if created else "[scaffold] skeleton already present — no changes"
        )
        if created and not was_empty:
            emit(
                f"[scaffold] note: added a new '{layout.source_dir}/' structure into a non-empty "
                "repo; existing files were left untouched"
            )
    emit(f"[layout] mode={layout.mode} package={layout.package_name} src={layout.source_dir}")

    # Build an isolated test environment for the worktree — a per-project venv
    # with the project's own deps — so generated tests don't depend on (or run
    # in) the orchestrator's interpreter. SDLC_TEST_ISOLATION=local opts out.
    testenv = make_test_environment(lang, build_tool=layout.build_tool)
    if lang == "java":
        if not java_toolchain_available():
            raise FeatureRunError(
                "Java codegen needs a JDK + Maven on PATH (install both, then retry).",
                code=2,
            )
    elif lang == "typescript":
        pm = layout.build_tool or "npm"
        if not node_toolchain_available(pm):
            raise FeatureRunError(
                f"TypeScript codegen needs Node.js + {pm} on PATH (install both, then retry).",
                code=2,
            )
    elif lang == "csharp" and not dotnet_toolchain_available():
        raise FeatureRunError(
            "C# codegen needs the .NET SDK (`dotnet`) on PATH (install it, then retry).",
            code=2,
        )
    elif lang == "go" and not go_toolchain_available():
        raise FeatureRunError(
            "Go codegen needs the Go toolchain (`go`) on PATH (install it, then retry).",
            code=2,
        )
    elif lang in ("c", "cpp"):
        # Greenfield always scaffolds a CMake project; brownfield uses the repo's own
        # build system (CMake or Meson). Preflight the matching toolchain and fail
        # fast with a clear message rather than a cryptic build error in refine.
        label = "C++" if lang == "cpp" else "C"
        cmake_ok = cpp_toolchain_available if lang == "cpp" else c_toolchain_available
        build_tool = layout.build_tool if layout.mode == "existing" else "cmake"
        if build_tool == "meson":
            if not meson_toolchain_available():
                raise FeatureRunError(
                    f"Meson {label} codegen needs meson + ninja + a compiler on PATH "
                    "(install them, then retry).",
                    code=2,
                )
        elif build_tool in ("cmake", ""):
            if not cmake_ok():
                raise FeatureRunError(
                    f"{label} codegen needs CMake + a {label} compiler on PATH (install both, then retry).",
                    code=2,
                )
            if layout.mode == "existing" and not (path / "CMakeLists.txt").is_file():
                raise FeatureRunError(
                    f"{label} codegen builds with CMake or Meson, but this repo has neither a "
                    "CMakeLists.txt nor a recognized meson.build.",
                    code=2,
                )
        else:  # make or another unrecognized build system
            raise FeatureRunError(
                f"{label} codegen builds with CMake or Meson, but this repo uses "
                f"{build_tool} (not supported yet).",
                code=2,
            )
    # ``ensure`` may install deps (Node ``<pm> install``); run it after the
    # toolchain preflight so a missing toolchain fails fast with a clear message.
    await testenv.ensure(path)
    emit(f"[testenv] {testenv.describe()}")
    if lang == "python" and not await pytest_available(testenv.python):
        raise FeatureRunError(
            "pytest is required to run the generated tests but isn't available in the test "
            "environment. Install it: pip install 'synaptixs-spine[sdlc]' (or pip install pytest).",
            code=2,
        )

    # 4. grounded code generation + 5. test/refine loop.
    # Spine Seam 1: domain-true ontomesh grounding composed with code-true PKG
    # grounding when SPINE_ONTOMESH_URL is set (else just the PKG grounder).
    from orchestrator.spine.grounder import compose_with_ontomesh

    grounder = compose_with_ontomesh(PKGCodegenGrounder.from_repo(path))
    grounding_chars = len(grounder.context_for_spec(spec))
    emit(
        f"[grounding] target-KG context: {grounding_chars} chars"
        + ("  (greenfield — nothing relevant yet)" if not grounding_chars else "")
    )
    # Drive the run as the software-engineer persona: its role leads the prompt and
    # its skills are resolved through the vetting gate, scoped to the capability plan
    # selected from this project's profile (so the single-shot CLI run is persona- and
    # profile-aware, matching the agentic/Temporal path).
    from orchestrator.catalog import ProjectProfile, plan_capabilities
    from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER

    profile = ProjectProfile.from_repo(path, intent_title=spec.get("title", ""))
    capability_plan = plan_capabilities(profile)
    emit(f"[persona] software_engineer · skills: {', '.join(capability_plan.skills) or '(none selected)'}")

    codegen_model = resolve_codegen_model(model)
    codegen_kwargs: dict[str, Any] = {
        "grounder": grounder,
        "layout": layout,
        "persona": SOFTWARE_ENGINEER,
        # Research and design are worthless to a model that never sees them.
        "design": design,
    }
    if design:
        emit(f"[design] carrying {len(design)} chars of agreed design into codegen")
    if codegen_model:
        codegen_kwargs["model"] = codegen_model
    codegen = LLMCodegenAdapter(llm, **codegen_kwargs)
    runner = make_test_runner(lang, testenv)

    # Attribute each leg's LLM spans + token ledger to a named stage, so the trace
    # reads implement / author_tests / refine instead of "unattributed".
    #
    # Everything from here to the PR runs inside the ticket-release guard. Handing the
    # ticket back was wired only into the "tests stayed red" path, so a codegen error —
    # the thing most likely to end a run early — sailed past it and left the ticket
    # In Progress anyway. A live run did exactly that, twice.
    try:
        with llm.stage("implement"):
            impl = await codegen.implement(
                spec=spec, path=str(path), issue_key=issue_key, skills=capability_plan.skills
            )
        emit(f"[implement] {[Path(f).name for f in impl.files]} - {impl.summary}")
        # SQL is single-phase: the migration IS the artifact and is validated by applying
        # it to an ephemeral database, so there is no separate test-authoring leg.
        if lang != "sql":
            with llm.stage("author_tests"):
                tests = await codegen.author_tests(spec=spec, path=str(path), issue_key=issue_key)
            emit(f"[author_tests] {[Path(f).name for f in tests.files]} - {tests.summary}")
    except Exception:
        # Release and re-raise: the caller still sees the real failure, and the board no
        # longer claims someone is working the ticket.
        await _release_the_ticket(move, emit)
        raise

    passed = False
    iterations = 0
    # One allowance per kind of problem, not one pool for all three. A live run spent
    # iterations 1-2 on real test failures and 3-4 on type errors, so when the coverage
    # probe found its gap on iteration 5 there was nothing left to answer it with — the
    # change was fixable and the run reported FAILED. Each check now gets guaranteed room,
    # with a hard ceiling so a pathological run still terminates.
    spent = {"tests": 0, "types": 0, "coverage": 0}
    budgets = {"tests": max_refine, "types": max_refine, "coverage": _MAX_COVERAGE_FIXES}
    ceiling = sum(budgets.values()) + 1
    while iterations < ceiling:
        result = await run_with_autoheal(runner, testenv, str(path), emit=emit)
        iterations += 1
        emit(f"[run_tests #{iterations}] passed={result.passed} rc={result.returncode}")
        failures = result.output
        kind = "tests"
        if result.passed:
            # Green is necessary and not sufficient. The generated tests are written by the
            # same model as the code and routinely exercise a new helper directly while never
            # importing the module the ticket was about — so they pass over a NameError or a
            # wrong attribute sitting on the line that matters. The type checker does not.
            changed = await _changed_files(path)
            failures = await _typecheck_the_change(path, testenv, changed, emit)
            kind = "types"
            if not failures:
                # Type-clean and green still allows a change nothing tests. Probe each
                # production file on its own; a gap is answered by writing the missing
                # test, not by editing the implementation, so it goes to author_tests.
                gaps = await _files_no_test_exercises(path, changed, runner, emit)
                if not gaps:
                    passed = True
                    break
                if spent["coverage"] >= budgets["coverage"]:
                    emit("[cover] out of coverage attempts — the change is not proven tested")
                    break
                spent["coverage"] += 1
                with llm.stage("author_tests"):
                    covered = await codegen.author_tests(
                        spec=spec, path=str(path), issue_key=issue_key, gaps=gaps
                    )
                emit(f"[cover] {[Path(f).name for f in covered.files]} - {covered.summary}")
                if not covered.files:
                    emit("[cover] no tests written for the gap — stopping rather than looping")
                    break
                continue
        if spent[kind] >= budgets[kind]:
            emit(f"[refine] out of {kind} attempts — stopping")
            break
        spent[kind] += 1
        with llm.stage("refine"):
            change = await codegen.refine(spec=spec, path=str(path), issue_key=issue_key, failures=failures)
        emit(f"[refine] {[Path(f).name for f in change.files]} - {change.summary}")
        if not change.files:
            # Refine only edits files. Having changed none, the next run is
            # byte-identical to the one that just failed, so another iteration
            # cannot change the verdict — it just spends an LLM call to watch the
            # same error. The classic case is an environment fault the model
            # diagnoses correctly and structurally cannot act on ("install the
            # missing dependency"): left alone it burns every remaining iteration.
            emit("[refine] no file changes — not fixable by editing code; stopping early")
            break

    files = await _changed_files(path)

    if passed:
        await _prove_the_tests_test_something(path, files, runner, emit)
        await _satisfy_the_ticket(
            llm,
            codegen=codegen,
            runner=runner,
            testenv=testenv,
            path=path,
            issue_key=issue_key,
            spec=spec,
            max_revisions=max_judge_revisions,
            max_repairs=max_revision_repairs,
            emit=emit,
        )
        # A revision may have added a file no earlier stage touched (a doc, most often),
        # so the PR's file list has to be taken after the judge is satisfied, not before.
        files = await _changed_files(path)

    if not passed:
        # A failed run is where the spend most needs explaining — it bought no PR.
        await log_run_cost("FAILED")
        # Put the ticket back. It was moved to In Progress before codegen so the board would
        # show the work being picked up; a run that then fails has produced no branch and no
        # PR, and leaving the ticket In Progress tells everyone looking at the board that
        # someone is on it. A live run did exactly this and the ticket sat there afterwards.
        await _release_the_ticket(move, emit)
        raise FeatureRunError(f"VERDICT: FAILED after {iterations} test run(s) — not opening a PR.", code=1)

    # The last gate before anything is written, and the only one that is not a model. Every
    # automatic check upstream can be satisfied by a change that does nothing — a run reached
    # this line having committed a helper function it never called, with green tests, a passing
    # proof check and a judge whose reply was unreadable. A person reading the diff is the one
    # check none of that can fool.
    if gate is not None:
        emit("[gate] waiting for human approval — nothing has been committed yet")
        if not await gate(path, files):
            await log_run_cost("DECLINED")
            raise FeatureRunError(
                "VERDICT: DECLINED at the human gate — nothing committed, nothing pushed.", code=1
            )
        emit("[gate] approved")

    # 6. commit + 7. push + PR.
    title = f"{issue_key}: {spec['title']}"
    body = (
        f"{spec['summary']}\n\nAcceptance criteria:\n"
        + "\n".join(f"- {c}" for c in spec["acceptance_criteria"])
        + f"\n\nGenerated by the SDLC orchestrator (intent {spec['intent_id']})."
    )
    pr_url: str | None = None
    if live:
        # Mark in-progress and drop BACKLOG.md into the worktree BEFORE open_pr so
        # the PR carries the updated progress ledger (the "both locations" rule).
        set_progress(source, spec["intent_id"], status="in_progress", issue_key=issue_key)
        if plan is not None:
            write_backlog(path / local_backlog.name, source, plan, load_progress(source))
        # Without an explicit base, ``gh`` targets the repo's *default* branch — which
        # for a repo whose contributing guide says "work off develop, never commit to
        # main" is precisely the wrong branch, with no way to say so from the CLI.
        pr = await GhPRAdapter(
            commit_prefix=f"{issue_key}: ",
            base_branch=base_branch or os.getenv("SDLC_PR_BASE") or None,
        ).open_pr(issue_key=issue_key, path=str(path), branch=branch, title=title, body=body)
        pr_url = pr.url
        emit(f"[pr] opened: {pr.url}")
        # Now that the PR URL is known, record it and refresh the local ledger.
        set_progress(source, spec["intent_id"], status="in_progress", issue_key=issue_key, pr_url=pr.url)
        if plan is not None:
            write_backlog(local_backlog, source, plan, load_progress(source))
        await jira.comment_issue(issue_key, f"PR opened for this story: {pr.url}")
        emit(f"[jira] commented PR link on {issue_key}")
        # The work is done and waiting on a human. Done is never the agent's to set —
        # that is `sdlc complete`, after someone has actually looked at the change.
        await move("In Review")
        await log_run_cost("PASSED")
    else:
        await _local_commit(path, title)
        emit("[commit] committed locally (safe mode — no push/PR)")

    return FeatureRunResult(
        passed=True,
        intent_id=spec["intent_id"],
        issue_key=issue_key,
        title=spec["title"],
        branch=branch,
        worktree=str(path),
        grounding_chars=grounding_chars,
        iterations=iterations,
        live=live,
        files=files,
        pr_url=pr_url,
        codegen=codegen,
        tests=runner,
    )


__all__ = ["FeatureRunError", "FeatureRunResult", "run_feature"]
