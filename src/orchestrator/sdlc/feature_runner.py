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
from collections.abc import Callable
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
    max_refine: int = 3,
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
        local_backlog = backlog_path()
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

    passed = False
    iterations = 0
    while iterations < max_refine:
        result = await run_with_autoheal(runner, testenv, str(path), emit=emit)
        iterations += 1
        emit(f"[run_tests #{iterations}] passed={result.passed} rc={result.returncode}")
        if result.passed:
            passed = True
            break
        if iterations >= max_refine:
            break
        with llm.stage("refine"):
            change = await codegen.refine(
                spec=spec, path=str(path), issue_key=issue_key, failures=result.output
            )
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
    if not passed:
        # A failed run is where the spend most needs explaining — it bought no PR.
        await log_run_cost("FAILED")
        raise FeatureRunError(f"VERDICT: FAILED after {iterations} test run(s) — not opening a PR.", code=1)

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
