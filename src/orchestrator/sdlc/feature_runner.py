"""Reusable single-intent SDLC feature runner.

Extracted from the ``sdlc feature`` CLI so the same pipeline is callable from
the CLI, the MCP plugin, and elsewhere: source → spec → (Jira) → worktree →
grounded codegen → test/refine → commit → (push + PR). Emits progress via a
``log`` callback and returns a ``FeatureRunResult``; raises ``FeatureRunError``
(carrying a CLI-style exit ``code``) instead of printing/exiting, so callers
own presentation and error mapping.

``live=False`` (safe) makes **no external writes** — dry-run Jira, a local
commit, no push. ``live=True`` creates a real Jira issue, pushes a branch, and
opens a PR.
"""

from __future__ import annotations

import asyncio
import os
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


class FeatureRunError(RuntimeError):
    """A feature run can't proceed. ``code`` mirrors the CLI exit code."""

    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


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
            # doesn't cover them).
            if rel.split("/", 1)[0] in _BUILD_DIRS:
                continue
            changed.add(rel)
        return sorted(changed)
    # Not a git repo (e.g. a stubbed test worktree): best-effort source scan.
    return [str(p.relative_to(path)) for p in sorted(path.rglob("*.py"))]


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
    return "python"


async def run_feature(
    source: str,
    *,
    intent_id: str | None = None,
    repo: str | None = None,
    model: str | None = None,
    max_refine: int = 3,
    live: bool = False,
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
    """
    emit: Callable[[str], None] = log or (lambda _m: None)

    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient, RecordingLLMClient
    from orchestrator.intake.backlog_doc import backlog_path, write_backlog
    from orchestrator.intake.cache import analyze_cached, load_progress, set_progress
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.jira import IssueRequest, IssueTrackerError, JiraAdapter, JiraConfig
    from orchestrator.intake.service import parse_source_uri
    from orchestrator.sdlc.codegen import LLMCodegenAdapter, resolve_codegen_model
    from orchestrator.sdlc.forge import GhPRAdapter
    from orchestrator.sdlc.grounding import PKGCodegenGrounder
    from orchestrator.sdlc.layout import is_effectively_empty, resolve_layout
    from orchestrator.sdlc.scaffold import scaffold
    from orchestrator.sdlc.testenv import (
        dotnet_toolchain_available,
        java_toolchain_available,
        make_test_environment,
        make_test_runner,
        node_toolchain_available,
        run_with_autoheal,
    )
    from orchestrator.sdlc.testrunner import pytest_available
    from orchestrator.sdlc.workspace import WorkspaceManager

    load_local_env()
    # Wrap in RecordingLLMClient — the OTel chokepoint (emits an llm.complete span
    # per call) + per-stage token ledger. Drop-in (implements LLMClient), so the
    # linear CLI path is now traced like the worker path.
    llm = RecordingLLMClient(LiteLLMClient())

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
    jira = JiraAdapter(JiraConfig(dry_run=not live))
    issue = await jira.create_issue(
        IssueRequest(
            summary=spec["title"],
            description=f"{spec['summary']}\n\nAcceptance:\n"
            + "\n".join(f"- {c}" for c in spec["acceptance_criteria"]),
            issue_type="Story",
        )
    )
    issue_key = issue.key
    emit(f"[jira] {'created' if live else 'dry-run'} issue: {issue_key} {issue.url}".rstrip())

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
    codegen_kwargs: dict[str, Any] = {"grounder": grounder, "layout": layout, "persona": SOFTWARE_ENGINEER}
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

    files = await _changed_files(path)
    if not passed:
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
        pr = await GhPRAdapter(commit_prefix=f"{issue_key}: ").open_pr(
            issue_key=issue_key, path=str(path), branch=branch, title=title, body=body
        )
        pr_url = pr.url
        emit(f"[pr] opened: {pr.url}")
        # Now that the PR URL is known, record it and refresh the local ledger.
        set_progress(source, spec["intent_id"], status="in_progress", issue_key=issue_key, pr_url=pr.url)
        write_backlog(local_backlog, source, plan, load_progress(source))
        await jira.comment_issue(issue_key, f"PR opened for this story: {pr.url}")
        emit(f"[jira] commented PR link on {issue_key}")
        try:
            moved = await jira.transition_issue(issue_key, "In Progress")
            if moved:
                emit(f"[jira] moved {issue_key} → {moved}")
        except IssueTrackerError as exc:
            emit(f"[jira] could not move {issue_key} to In Progress: {exc}")
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
    )


__all__ = ["FeatureRunError", "FeatureRunResult", "run_feature"]
