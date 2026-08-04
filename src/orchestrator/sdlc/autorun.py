"""One ticket, driven through the stages Spine already has — the walking skeleton.

Every stage of this loop exists as a command: `investigate`, `design`, `sdlc feature`,
`codereview`. Nothing called them in order or carried state between them, so a human was the
connective tissue. This is the connective tissue, and nothing more: the happy path, in order,
with each stage's result recorded.

**What this deliberately does NOT do yet** (see `docs/specs/autonomous-run-agent.md`): it does
not judge whether the ticket is worth doing (phase 4), does not enforce budgets or survive a
crash (phase 3), and does not loop on review findings (phase 5). Those are the parts that make
autonomy safe, and each is its own story. A skeleton that pretended to have them would be
worse than one that says plainly where it stops — every stage records `skipped` with a reason
rather than quietly doing nothing.

**Additive by construction.** Every stage is a call into an existing entry point, unchanged: a
human can still run any of them by hand and get exactly what they get today. This module owns
sequencing and state, never behaviour.

**Artifacts go outside the repo.** The brief and design are markdown, and `understand` ingests
markdown from disk whether or not git tracks it — so writing them into the working tree would
turn a run's own notes into `Doc` nodes and change the graph the next stage reads. They land
under a run directory in the system temp dir unless ``SPINE_RUN_ARTIFACTS`` says otherwise.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

StageStatus = Literal["ok", "skipped", "failed"]

# The order is the contract: research before design, design before code, code before review.
STAGES: tuple[str, ...] = ("intake", "investigate", "design", "implement", "review")


class AutorunError(RuntimeError):
    """The run cannot continue. ``code`` mirrors the CLI exit code."""

    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StageResult:
    """What one stage did — including, explicitly, when it did nothing."""

    name: str
    status: StageStatus
    detail: str
    artifact: str = ""


@dataclass
class RunContext:
    """State carried between stages. The thing a supervisor will later persist.

    Phase 3 gives this a home in the registry DB, an idempotency key and a budget. For now it
    lives for the length of one process, which is exactly the limitation the next story fixes.
    """

    run_id: str
    source: str
    live: bool
    root: Path
    artifacts_dir: Path
    issue_key: str = ""
    branch: str = ""
    worktree: str = ""
    pr_url: str | None = None
    spec: dict[str, Any] | None = None
    stages: list[StageResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(stage.status != "failed" for stage in self.stages)

    def record(self, name: str, status: StageStatus, detail: str, artifact: str = "") -> StageResult:
        result = StageResult(name=name, status=status, detail=detail, artifact=artifact)
        self.stages.append(result)
        return result

    def write_artifact(self, name: str, body: str) -> str:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / name
        path.write_text(body, encoding="utf-8")
        return str(path)


def default_artifacts_dir(run_id: str) -> Path:
    """Where a run's markdown goes — outside the repo, on purpose.

    ``understand`` reads markdown from disk regardless of git, so a brief written into the
    working tree becomes a ``Doc`` node and changes the graph the next stage reads.
    """
    base = os.getenv("SPINE_RUN_ARTIFACTS") or str(Path(tempfile.gettempdir()) / "spine-runs")
    return Path(base) / run_id


async def autorun(
    source: str,
    *,
    issue: str | None = None,
    intent_id: str | None = None,
    repo: str | None = None,
    root: Path | str = ".",
    live: bool = False,
    max_refine: int = 3,
    base_branch: str | None = None,
    language: str = "auto",
    artifacts_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> RunContext:
    """Drive one ticket through the happy path. Safe mode by default.

    ``--safe`` makes no external write anywhere in the chain, exactly as `sdlc feature --safe`
    does today: the brief and design are written to the run directory, the code is committed
    locally, and no tracker or forge is touched.
    """
    emit: Callable[[str], None] = log or (lambda _m: None)
    run_id = uuid.uuid4().hex[:16]
    ctx = RunContext(
        run_id=run_id,
        source=source,
        live=live,
        root=Path(root),
        artifacts_dir=artifacts_dir or default_artifacts_dir(run_id),
    )
    emit(f"[autorun] run {run_id} · source {source} · {'live' if live else 'safe'}")

    await _stage_intake(ctx, intent_id=intent_id, emit=emit)
    store, overview = _load_graph(ctx, emit=emit)
    _stage_investigate(ctx, store=store, emit=emit)
    await _stage_design(ctx, store=store, overview=overview, emit=emit)
    await _stage_implement(
        ctx,
        issue=issue,
        intent_id=intent_id,
        repo=repo,
        max_refine=max_refine,
        base_branch=base_branch,
        language=language,
        emit=emit,
    )
    _stage_review(ctx, emit=emit)

    emit(f"[autorun] artifacts in {ctx.artifacts_dir}")
    return ctx


# ---- stages ----------------------------------------------------------------


async def _stage_intake(ctx: RunContext, *, intent_id: str | None, emit: Callable[[str], None]) -> None:
    """Resolve the source to one spec, once, and hand it to every later stage.

    ``run_feature`` can do its own intake, but then the brief and design would be written for
    a spec the implementation stage might re-derive differently. Resolving here and injecting
    it keeps all four stages talking about the same thing.
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.service import parse_source_uri

    load_local_env()
    parse_source_uri(ctx.source)
    try:
        service = build_service_for(ctx.source, dry_run=True)
    except IntakeNotConfiguredError as exc:
        ctx.record("intake", "failed", str(exc))
        raise AutorunError(str(exc), code=2) from exc

    plan = await analyze_cached(service, ctx.source, refresh=False, log=emit)
    if not plan.specs:
        ctx.record("intake", "failed", "no specs derived from the source")
        raise AutorunError("No specs derived from the source — nothing to implement.", code=3)

    chosen = next((s for s in plan.specs if s.intent_id == intent_id), None) if intent_id else plan.specs[0]
    if chosen is None:
        ids = ", ".join(s.intent_id for s in plan.specs)
        ctx.record("intake", "failed", f"intent {intent_id!r} not found")
        raise AutorunError(f"Intent {intent_id!r} not found. Available: {ids}", code=3)

    ctx.spec = chosen.model_dump()
    ctx.record("intake", "ok", f"spec: {ctx.spec.get('title', '')}")
    emit(f"[intake] {ctx.spec.get('title', '')} (intent {ctx.spec.get('intent_id', '')})")


def _load_graph(ctx: RunContext, *, emit: Callable[[str], None]) -> tuple[Any, dict[str, Any]]:
    """One extraction, shared by investigate and design.

    Both stages want the same graph of the same commit; extracting twice would be slower and,
    on a tree that changed underneath, inconsistent.
    """
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.overview import build_overview

    batch = load_or_extract(ctx.root)
    emit(f"[graph] {len(batch.nodes)} nodes, {len(batch.edges)} edges")
    return FactStore(batch), build_overview(batch)


def _stage_investigate(ctx: RunContext, *, store: Any, emit: Callable[[str], None]) -> None:
    from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

    spec = ctx.spec or {}
    investigation = build_investigation(
        str(spec.get("title", "")),
        str(spec.get("summary", "")),
        store=store,
        root=ctx.root,
    )
    path = ctx.write_artifact("investigation.md", render_investigation_md(investigation))
    landed = len(getattr(investigation, "landing", []) or [])
    ctx.record("investigate", "ok", f"{landed} symbol(s) this ticket lands on", path)
    emit(f"[investigate] {landed} symbol(s) · {path}")


async def _stage_design(
    ctx: RunContext, *, store: Any, overview: dict[str, Any], emit: Callable[[str], None]
) -> None:
    from orchestrator.sdlc.design import produce_design, render_design_md

    spec = ctx.spec or {}
    # No LLM here yet: the deterministic design is the honest skeleton default, and it keeps
    # this stage runnable with no provider configured. Wiring the model in is phase 2 work,
    # not skeleton work.
    design = await produce_design(spec, overview=overview, store=store, llm=None)
    path = ctx.write_artifact("design.md", render_design_md(spec, design))
    touched = len(design.get("files_to_touch") or [])
    ctx.record("design", "ok", f"{touched} file(s) proposed", path)
    emit(f"[design] {touched} file(s) proposed · {path}")


async def _stage_implement(
    ctx: RunContext,
    *,
    issue: str | None,
    intent_id: str | None,
    repo: str | None,
    max_refine: int,
    base_branch: str | None,
    language: str,
    emit: Callable[[str], None],
) -> None:
    """Codegen, tests and (live) the PR — `sdlc feature`, unchanged, with our spec injected."""
    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature

    try:
        result = await run_feature(
            ctx.source,
            intent_id=intent_id,
            repo=repo,
            max_refine=max_refine,
            live=ctx.live,
            issue=issue,
            base_branch=base_branch,
            language=language,
            spec=ctx.spec,
            log=emit,
        )
    except FeatureRunError as exc:
        ctx.record("implement", "failed", str(exc))
        raise AutorunError(str(exc), code=exc.code) from exc

    ctx.issue_key = result.issue_key
    ctx.branch = result.branch
    ctx.worktree = result.worktree
    ctx.pr_url = result.pr_url
    ctx.record(
        "implement",
        "ok",
        f"{len(result.files)} file(s) changed on {result.branch} after {result.iterations} test run(s)",
    )


def _stage_review(ctx: RunContext, *, emit: Callable[[str], None]) -> None:
    """Grounded review of the change.

    Skipped in safe mode with a reason rather than silently: the reviewer reads a *pull
    request*, and safe mode opens none. Closing the review→fix→re-test loop is its own story;
    this stage exists so the skeleton's shape is honest about where that loop will attach.
    """
    if not ctx.pr_url:
        ctx.record("review", "skipped", "no PR to review (safe mode opens none)")
        emit("[review] skipped — safe mode opens no PR")
        return
    ctx.record("review", "skipped", f"review of {ctx.pr_url} not wired yet (SSPN-24)")
    emit(f"[review] skipped — {ctx.pr_url} awaits the review loop (SSPN-24)")


def render_summary(ctx: RunContext) -> str:
    """One table: what ran, what it did, where the artifact is."""
    lines = [
        f"run {ctx.run_id} · {'live' if ctx.live else 'safe'} · {ctx.source}",
        "",
        "| Stage | Status | Detail |",
        "|---|---|---|",
    ]
    for stage in ctx.stages:
        lines.append(f"| {stage.name} | {stage.status} | {stage.detail} |")
    if ctx.issue_key:
        lines += ["", f"issue: {ctx.issue_key}"]
    if ctx.pr_url:
        lines.append(f"PR: {ctx.pr_url}")
    lines.append(f"artifacts: {ctx.artifacts_dir}")
    return "\n".join(lines)


__all__ = [
    "STAGES",
    "AutorunError",
    "RunContext",
    "StageResult",
    "autorun",
    "default_artifacts_dir",
    "render_summary",
]
