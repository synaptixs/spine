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

import contextlib
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

StageStatus = Literal["ok", "skipped", "failed"]

# The order is the contract: research before design, design before code, code before review.
STAGES: tuple[str, ...] = ("intake", "investigate", "validity", "design", "implement", "review")


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
    # The design, rendered — handed to codegen so the implement stage acts on what the
    # design stage decided rather than re-deriving its own view of the ticket.
    plan: str = ""
    # Where the investigation says this ticket lands. Shared with the validity gate so the
    # two cannot disagree about what the ticket is even about.
    landing: list[str] = field(default_factory=list)
    # What the design said to touch — kept structured so implement can be compared against
    # it. The rendered design (``plan``) is prose and cannot be diffed.
    design_files: list[str] = field(default_factory=list)
    verdict: str = ""
    # Set by the implement stage: the same adapter and runner that built the change also fix
    # what review finds, so the fixer knows the repo's conventions and the layout it chose.
    fixer: Any = None
    tests: Any = None
    approvals_dir: Path | None = None
    stages: list[StageResult] = field(default_factory=list)
    # Durable state. Written after every stage, so a crash leaves a findable run rather than
    # a mystery worktree and a ticket stuck In Progress.
    record: Any = None
    store: Any = None

    @property
    def passed(self) -> bool:
        return all(stage.status != "failed" for stage in self.stages)

    @contextlib.contextmanager
    def stage_span(self, name: str) -> Any:
        """One span per stage, under the run's own span.

        Every LLM call already emits a span; what was missing is the thing that joins them.
        Without a parent, a run that went wrong is a scatter of calls with no story — which
        stage was slow, which one failed, and what the run was doing at the time.
        """
        from orchestrator.obs import tracing

        # The phase is stamped on entry, not on result: a run that died inside `implement`
        # reported `design` — the last stage that managed to *finish* — so the record named
        # the wrong stage for the failure and nobody could tell where the money went.
        self.checkpoint(phase=name)
        with tracing.span(
            "autorun.stage",
            **{"autorun.run_id": self.run_id, "autorun.stage": name, "autorun.issue": self.issue_key},
        ):
            yield

    def record_stage(self, name: str, status: StageStatus, detail: str, artifact: str = "") -> StageResult:
        result = StageResult(name=name, status=status, detail=detail, artifact=artifact)
        self.stages.append(result)
        self.checkpoint(phase=name, status="failed" if status == "failed" else None)
        self.journal(name, status, detail)
        return result

    def journal(self, stage: str, status: str, detail: str, *, tokens: int = 0, usd: float = 0.0) -> None:
        """Append one line to this ticket's journey, beside its plan.

        Never fatal. A run that failed because it could not write its own diary would be
        the diary costing more than it is worth — and the run record already holds the
        same facts for the length of the process.
        """
        intent = str((self.spec or {}).get("intent_id") or "")
        if not intent:
            return
        try:
            from datetime import UTC, datetime

            from orchestrator.sdlc.builddoc import JourneyEntry, append_journey

            append_journey(
                JourneyEntry(
                    run_id=self.run_id,
                    stage=stage,
                    status=status,
                    detail=detail,
                    at=datetime.now(UTC).isoformat(timespec="seconds"),
                    tokens=tokens,
                    usd=usd,
                ),
                intent_id=intent,
                root=self.root,
            )
        except (OSError, ValueError):
            return

    def park(self, *, kind: str, title: str, reason: str) -> Any:
        """Stop, and put the decision in front of a human.

        A parked run that nobody is told about is just a stopped run, so raising the approval
        and notifying are the same step. Notification failure never propagates: the run has
        already stopped, and an un-notified approval is still findable in `sdlc runs list`.
        """
        from orchestrator.sdlc.escalate import ApprovalStore, default_approval_dir, raise_approval

        approval = raise_approval(
            run_id=self.run_id,
            issue_key=self.issue_key,
            title=title,
            reason=reason,
            store=ApprovalStore(root=self.approvals_dir or default_approval_dir()),
        )
        self.checkpoint(status="parked", parked_reason=reason, approval_id=approval.approval_id)
        return approval

    def checkpoint(self, *, phase: str | None = None, status: str | None = None, **fields: Any) -> None:
        """Persist what we know. Cheap, and the only thing standing between a kill -9 and an
        orphaned worktree nobody can attribute."""
        if self.record is None or self.store is None:
            return
        if phase is not None:
            self.record.phase = phase
        if status is not None:
            self.record.status = status
        self.record.issue_key = self.issue_key or self.record.issue_key
        self.record.branch = self.branch or self.record.branch
        self.record.worktree = self.worktree or self.record.worktree
        self.record.pr_url = self.pr_url or self.record.pr_url
        for key, value in fields.items():
            setattr(self.record, key, value)
        self.store.save(self.record)

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
    max_refine: int = 5,
    gate: Any = None,
    review_rounds: int = 2,
    base_branch: str | None = None,
    language: str = "auto",
    issue_type: str = "",
    artifacts_dir: Path | None = None,
    approvals_dir: Path | None = None,
    resume: str | None = None,
    max_cost_usd: float | None = None,
    spec: dict[str, Any] | None = None,
    plan_gate: bool = True,
    store: Any = None,
    log: Callable[[str], None] | None = None,
) -> RunContext:
    """Drive one ticket through the happy path. Safe mode by default.

    ``--safe`` makes no external write anywhere in the chain, exactly as `sdlc feature --safe`
    does today: the brief and design are written to the run directory, the code is committed
    locally, and no tracker or forge is touched.

    ``resume`` continues a run that already exists: it keeps the run id, and adopts the
    tracker issue that run already created rather than creating a second one. ``max_cost_usd``
    caps LLM spend for the run; exhausting it parks the run instead of shipping half a change.

    ``plan_gate`` refuses to build a ticket whose build document nobody approved. On by
    default: the whole point of the document is that it is read *before* code, and a gate
    that must be switched on is one nobody switches on.
    """
    from orchestrator.core.llm import RunBudget
    from orchestrator.sdlc.runstate import RunRecord, RunStore

    emit: Callable[[str], None] = log or (lambda _m: None)
    started = time.monotonic()
    store = store or RunStore()

    record = store.load(resume) if resume else None
    if resume and record is None:
        raise AutorunError(f"no run {resume!r} to resume", code=2)
    if record is not None:
        _refuse_undecided_resume(record, approvals_dir, emit)
        emit(f"[autorun] resuming {record.run_id} · phase {record.phase or 'start'}")
        if record.issue_key:
            # The half that must not happen twice. A crashed run that had already created a
            # ticket is why `--issue` adoption exists at all.
            issue = issue or record.issue_key
            emit(f"[autorun] adopting {record.issue_key} from the previous attempt")
        record.status = "running"
        record.pid = os.getpid()
    run_id = record.run_id if record else uuid.uuid4().hex[:16]

    ctx = RunContext(
        run_id=run_id,
        source=source,
        live=live,
        root=Path(root),
        artifacts_dir=artifacts_dir or default_artifacts_dir(run_id),
    )
    if record is None:
        record = RunRecord(
            run_id=run_id,
            source=source,
            live=live,
            started_at=time.time(),
            pid=os.getpid(),
            artifacts_dir=str(ctx.artifacts_dir),
        )
    # One run owns one ticket. A second run against a ticket someone else is driving would
    # race it for the same branch and the same issue — the duplicate-ticket failure again,
    # this time with two live processes.
    if issue:
        held = store.active_for_issue(issue)
        if held is not None and held.run_id != run_id:
            raise AutorunError(
                f"{issue} is already held by run {held.run_id} ({held.status}). "
                f"Resume it with --resume {held.run_id}, or reap it if its process is gone.",
                code=2,
            )
        record.issue_key = issue

    ctx.approvals_dir = approvals_dir
    ctx.record, ctx.store = record, store
    ctx.issue_key = record.issue_key
    ctx.checkpoint(phase="start", status="running")

    from orchestrator.core.llm.recording import TokenLedger

    # One ledger for the run. The implement stage fills most of it and the review loop adds
    # to it afterwards, so the account is of the whole run rather than its middle.
    ledger = TokenLedger()
    budget = RunBudget(max_cost_usd=max_cost_usd) if max_cost_usd else None
    emit(f"[autorun] run {run_id} · source {source} · {'live' if live else 'safe'}")
    if budget is not None:
        emit(f"[budget] cap ${max_cost_usd:.2f} for this run")

    from orchestrator.obs import tracing

    # One span for the run, one per stage beneath it. Every LLM call already emitted a span;
    # what was missing was the thing that joins them — without a parent, a run that went
    # wrong is a scatter of calls with no story.
    with tracing.span(
        "autorun.run", **{"autorun.run_id": run_id, "autorun.source": source, "autorun.live": live}
    ):
        try:
            with ctx.stage_span("intake"):
                await _stage_intake(ctx, intent_id=intent_id, spec=spec, emit=emit)
            # Before the graph, before any spend: was this plan read and approved? The gate
            # is here rather than before intake because it needs the spec to know which plan
            # it is asking about.
            await _require_plan(ctx, enabled=plan_gate, emit=emit)
            store_graph, overview = _load_graph(ctx, emit=emit)
            with ctx.stage_span("investigate"):
                _stage_investigate(ctx, store=store_graph, emit=emit)
            with ctx.stage_span("validity"):
                _stage_validity(ctx, store=store_graph, issue_type=issue_type, emit=emit)
            with ctx.stage_span("design"):
                await _stage_design(ctx, store=store_graph, overview=overview, emit=emit)
            with ctx.stage_span("implement"):
                await _stage_implement(
                    ctx,
                    issue=issue,
                    intent_id=intent_id,
                    repo=repo,
                    max_refine=max_refine,
                    gate=gate,
                    base_branch=base_branch,
                    language=language,
                    budget=budget,
                    ledger=ledger,
                    emit=emit,
                )
            with ctx.stage_span("review"):
                await _stage_review(
                    ctx, fixer=ctx.fixer, tests=ctx.tests, max_rounds=review_rounds, emit=emit
                )
        except AutorunError:
            # Already recorded by the stage that raised it: re-raise untouched so exit codes,
            # parking and approvals survive.
            raise
        except Exception as exc:
            # Everything nobody anticipated — a model returning unparseable JSON, a git
            # failure, a network blip. Without this the error escapes the supervisor
            # entirely: no stage recorded, no checkpoint, a run left claiming to be running
            # for the reaper to find hours later, and a traceback where a verdict belongs.
            phase = ctx.record.phase if ctx.record is not None else "run"
            ctx.record_stage(phase or "run", "failed", f"unexpected {type(exc).__name__}: {exc}")
            # Spend, on the way out. A run that died is exactly the one whose cost you need:
            # the record used to say $0.00 for a run that had made three LLM calls, which
            # makes "what does this cost" unanswerable from the thing built to answer it.
            ctx.checkpoint(status="failed", spent_usd=_spent(budget, ctx.run_id))
            emit(f"[autorun] {type(exc).__name__}: {exc}")
            await _log_run_cost(ctx, ledger=ledger, started=started, verdict="FAILED", emit=emit)
            _journal_outcome(ctx, ledger=ledger, budget=budget, verdict="FAILED")
            raise AutorunError(f"{type(exc).__name__}: {exc}", code=1) from exc

    ctx.checkpoint(phase="done", status="done", spent_usd=_spent(budget, run_id))
    await _log_run_cost(ctx, ledger=ledger, started=started, verdict="PASSED", emit=emit)
    _journal_outcome(ctx, ledger=ledger, budget=budget, verdict="PASSED")
    emit(f"[autorun] artifacts in {ctx.artifacts_dir}")
    return ctx


def _journal_outcome(ctx: RunContext, *, ledger: Any, budget: Any, verdict: str) -> None:
    """The line the estimate is eventually judged against: what this run cost, and where it got.

    Section 11 promises a cost *estimate*; this is the actual, recorded per run so the two
    can be compared later instead of the estimate being graded by the thing that produced it.
    """
    spent = _spent(budget, ctx.run_id)
    try:
        tokens = ledger.total().total_tokens
    except (AttributeError, TypeError):  # pragma: no cover — a ledger that cannot total
        tokens = 0
    where = f" · {ctx.pr_url}" if ctx.pr_url else (f" · {ctx.branch}" if ctx.branch else "")
    ctx.journal(
        "run",
        "ok" if verdict == "PASSED" else "failed",
        f"{verdict} — {tokens:,} tokens, ${spent:.2f}{where}",
        tokens=tokens,
        usd=spent,
    )


async def _log_run_cost(
    ctx: RunContext, *, ledger: Any, started: float, verdict: str, emit: Callable[[str], None]
) -> None:
    """Post one worklog for the whole run. Live only, and never fatal.

    Telemetry is not the work: a tracker that rejects the worklog leaves a line in the log,
    not a failed run whose change was already built.
    """
    if not ctx.live or not ctx.issue_key:
        return
    from orchestrator.intake.jira import IssueTrackerError, JiraAdapter, JiraConfig
    from orchestrator.sdlc.telemetry import jira_duration, render_run_worklog

    elapsed = time.monotonic() - started
    body = render_run_worklog(
        ledger,
        seconds=elapsed,
        verdict=verdict,
        stages=[(s.name, s.status, s.detail) for s in ctx.stages],
        review=next((s.detail for s in ctx.stages if s.name == "review"), ""),
    )
    jira = JiraAdapter(JiraConfig(dry_run=False))
    try:
        await jira.add_worklog(ctx.issue_key, time_spent=jira_duration(elapsed), comment=body)
        emit(f"[jira] worklog on {ctx.issue_key}: {ledger.total().total_tokens:,} tokens across the run")
    except (IssueTrackerError, OSError) as exc:
        emit(f"[jira] could not log run cost on {ctx.issue_key}: {exc}")
    finally:
        await jira.aclose()


def _spent(budget: Any, run_id: str) -> float:
    return float(budget.spent(run_id)) if budget is not None else 0.0


def _refuse_undecided_resume(record: Any, approvals_dir: Path | None, emit: Callable[[str], None]) -> None:
    """A parked run resumes when a human has answered, and not before.

    Resuming past an undecided approval would make parking decorative — the run would stop,
    ask, and then carry on regardless the moment anyone retried it.
    """
    from orchestrator.sdlc.escalate import ApprovalStore, Decision, default_approval_dir

    store = ApprovalStore(root=approvals_dir or default_approval_dir())
    approval = store.for_run(record.run_id)
    if approval is None or not approval.pending:
        if approval is not None and approval.decision == Decision.REJECTED.value:
            raise AutorunError(
                f"run {record.run_id} was rejected by {approval.decided_by or 'a human'}"
                + (f": {approval.note}" if approval.note else ""),
                code=6,
            )
        return
    overdue = " (overdue)" if approval.overdue else ""
    raise AutorunError(
        f"run {record.run_id} is waiting on approval {approval.approval_id}{overdue}: "
        f"{approval.reason}. Decide it with `sdlc runs approve {approval.approval_id}` "
        "(or --reject) before resuming.",
        code=6,
    )


async def _require_plan(ctx: RunContext, *, enabled: bool, emit: Callable[[str], None]) -> None:
    """Refuse to build a ticket whose plan nobody approved.

    On by default, because a gate that has to be switched on is one nobody switches on.
    ``--no-plan-gate`` exists for the flows that predate it and for a repo that has not
    adopted plans yet; skipping is said out loud rather than passing in silence.

    A refusal parks rather than fails: the ticket is fine, the review has not happened.
    """
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan

    if not enabled:
        # Said, not recorded: a skipped gate is not a stage that ran, and putting it in the
        # stage list would misreport the shape of every run that opts out.
        emit("[plan] gate skipped (--no-plan-gate) — nothing was reviewed before this run")
        return
    try:
        approval = await require_approved_plan(ctx.spec or {}, root=ctx.root)
    except PlanNotApprovedError as exc:
        ctx.record_stage("plan", "failed", str(exc))
        ctx.checkpoint(status="parked", parked_reason=str(exc))
        emit(f"[plan] {exc}")
        raise AutorunError(str(exc), code=6) from exc
    ctx.record_stage("plan", "ok", f"approved by {approval.decided_by} on {approval.decided_at}")
    emit(f"[plan] approved by {approval.decided_by or 'a human'} on {approval.decided_at}")


# ---- stages ----------------------------------------------------------------


async def _stage_intake(
    ctx: RunContext,
    *,
    intent_id: str | None,
    spec: dict[str, Any] | None = None,
    emit: Callable[[str], None],
) -> None:
    """Resolve the source to one spec, once, and hand it to every later stage.

    ``run_feature`` can do its own intake, but then the brief and design would be written for
    a spec the implementation stage might re-derive differently. Resolving here and injecting
    it keeps all four stages talking about the same thing.

    A caller-supplied ``spec`` replaces that derivation outright: intake does not run, and
    the stage records *skipped* rather than *ok*, so the summary never implies a source
    document was read. The source URI is still what the run is filed against.
    """
    if spec is not None:
        ctx.spec = dict(spec)
        ctx.spec.setdefault("intent_id", intent_id or "injected")
        ctx.record_stage("intake", "skipped", "spec supplied by the caller")
        emit(f"[intake] skipped — spec supplied: {ctx.spec.get('title', '')}")
        return

    from orchestrator.core.env import load_local_env
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.service import parse_source_uri

    load_local_env()
    parse_source_uri(ctx.source)
    try:
        service = build_service_for(ctx.source, dry_run=True)
    except IntakeNotConfiguredError as exc:
        ctx.record_stage("intake", "failed", str(exc))
        raise AutorunError(str(exc), code=2) from exc

    plan = await analyze_cached(service, ctx.source, refresh=False, log=emit)
    if not plan.specs:
        ctx.record_stage("intake", "failed", "no specs derived from the source")
        raise AutorunError("No specs derived from the source — nothing to implement.", code=3)

    chosen = next((s for s in plan.specs if s.intent_id == intent_id), None) if intent_id else plan.specs[0]
    if chosen is None:
        ids = ", ".join(s.intent_id for s in plan.specs)
        ctx.record_stage("intake", "failed", f"intent {intent_id!r} not found")
        raise AutorunError(f"Intent {intent_id!r} not found. Available: {ids}", code=3)

    ctx.spec = chosen.model_dump()
    ctx.record_stage("intake", "ok", f"spec: {ctx.spec.get('title', '')}")
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
    ctx.landing = []
    for land in getattr(investigation, "landing", []) or []:
        where = str(getattr(land, "where", "")).split(":", 1)[0]
        if where and where not in ctx.landing:
            ctx.landing.append(where)
    landed = len(getattr(investigation, "landing", []) or [])
    ctx.record_stage("investigate", "ok", f"{landed} symbol(s) this ticket lands on", path)
    emit(f"[investigate] {landed} symbol(s) · {path}")


def _stage_validity(ctx: RunContext, *, store: Any, issue_type: str, emit: Callable[[str], None]) -> None:
    """Is this ticket worth building, and is what it says about the code true?

    The only stage that can stop a run before any code is written, and the reason the agent
    is trustworthy at all: a ticket claiming eleven entities where the source has seven
    would otherwise be built to a false premise, pass its own tests, and be wrong.
    """
    from orchestrator.sdlc.codegen import _MAX_CONTEXT_BYTES
    from orchestrator.sdlc.validity import Verdict, assess

    assessment = assess(
        ctx.spec or {},
        store=store,
        landing=ctx.landing,
        issue_type=issue_type or str((ctx.spec or {}).get("issue_type", "")),
        issue_key=ctx.issue_key,
        prior_runs=ctx.store.all() if ctx.store is not None else [],
        # The gate can only weigh the context budget if it can measure the files. Passing
        # codegen's own constant keeps the two from drifting: raise the budget there and
        # the gate follows.
        root=ctx.root,
        context_budget=_MAX_CONTEXT_BYTES,
    )
    ctx.verdict = assessment.verdict.value
    path = ctx.write_artifact("validity.md", assessment.render())

    if assessment.verdict is Verdict.PROCEED:
        ctx.record_stage("validity", "ok", "PROCEED — nothing contradicts the code", path)
        emit("[validity] PROCEED")
        return

    detail = "; ".join(f.detail for f in assessment.findings) or assessment.verdict.value
    ctx.record_stage("validity", "failed", f"{assessment.verdict.value}: {detail}", path)
    # Parked, not failed: a refused ticket is a decision waiting for a human, and the run
    # keeps its evidence so the human is not asked to take the agent's word for it.
    ctx.checkpoint(verdict=ctx.verdict)
    approval = ctx.park(kind="verdict", title=f"{assessment.verdict.value} — build anyway?", reason=detail)
    emit(f"[validity] {assessment.verdict.value} — {detail}")
    emit(f"[approval] {approval.approval_id} raised{' and notified' if approval.notified else ''}")
    emit(f"[validity] evidence in {path}")
    raise AutorunError(f"{assessment.verdict.value}: {detail} — run parked, nothing was built.", code=5)


async def _stage_design(
    ctx: RunContext, *, store: Any, overview: dict[str, Any], emit: Callable[[str], None]
) -> None:
    from orchestrator.sdlc.design import produce_design, render_design_md

    spec = ctx.spec or {}
    # No LLM here yet: the deterministic design is the honest skeleton default, and it keeps
    # this stage runnable with no provider configured. Wiring the model in is phase 2 work,
    # not skeleton work.
    design = await produce_design(spec, overview=overview, store=store, llm=None, root=ctx.root)
    rendered = render_design_md(spec, design)
    path = ctx.write_artifact("design.md", rendered)
    ctx.design_files = [str(f) for f in (design.get("files_to_touch") or [])]
    touched = len(design.get("files_to_touch") or [])
    # Carried into the implement stage. Writing an artifact nobody reads is the difference
    # between chaining commands and connecting them.
    ctx.plan = rendered
    ctx.record_stage("design", "ok", f"{touched} file(s) proposed", path)
    emit(f"[design] {touched} file(s) proposed · {path}")


async def _stage_implement(
    ctx: RunContext,
    *,
    issue: str | None,
    intent_id: str | None,
    repo: str | None,
    max_refine: int,
    gate: Any,
    base_branch: str | None,
    language: str,
    budget: Any,
    ledger: Any,
    emit: Callable[[str], None],
) -> None:
    """Codegen, tests and (live) the PR — `sdlc feature`, unchanged, with our spec injected."""
    import contextlib

    from orchestrator.core.llm import BudgetExceededError
    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature

    # Attribute spend to this run. Without it every charge lands on the shared "unscoped"
    # bucket, the cap is enforced against the wrong total, and the run record reports $0.00
    # for a run that spent real money.
    scope = budget.activate(ctx.run_id) if budget is not None else contextlib.nullcontext()
    try:
        with scope:
            result = await run_feature(
                ctx.source,
                intent_id=intent_id,
                repo=repo,
                max_refine=max_refine,
                gate=gate,
                live=ctx.live,
                issue=issue,
                design=ctx.plan,
                base_branch=base_branch,
                language=language,
                spec=ctx.spec,
                budget=budget,
                ledger=ledger,
                # This run's worklog covers every stage and is posted once, at the end.
                post_worklog=False,
                log=emit,
            )
    except BudgetExceededError as exc:
        # Out of money mid-change. Park it: the work so far is on a branch and a human can
        # decide whether to raise the cap or drop it. Shipping half a change would be worse.
        ctx.record_stage("implement", "failed", f"budget exhausted: {exc}")
        ctx.checkpoint(spent_usd=_spent(budget, ctx.run_id))
        approval = ctx.park(
            kind="budget", title="budget exhausted — raise the cap or drop the run?", reason=str(exc)
        )
        emit(f"[approval] {approval.approval_id} raised{' and notified' if approval.notified else ''}")
        raise AutorunError(f"budget exhausted — run parked: {exc}", code=4) from exc
    except FeatureRunError as exc:
        ctx.record_stage("implement", "failed", str(exc))
        ctx.checkpoint(status="failed")
        raise AutorunError(str(exc), code=exc.code) from exc

    ctx.issue_key = result.issue_key
    ctx.branch = result.branch
    ctx.worktree = result.worktree
    ctx.pr_url = result.pr_url
    ctx.fixer, ctx.tests = result.codegen, result.tests
    ctx.record_stage(
        "implement",
        "ok",
        f"{len(result.files)} file(s) changed on {result.branch} after {result.iterations} test run(s)",
    )
    # The disagreement, if there is one, is the most valuable line in the journey: a run
    # that quietly edited three files nobody planned is visible today only by reading the
    # diff. It is journalled, never used to fail the run — implement may well be right.
    from orchestrator.sdlc.builddoc import design_disagreement

    drift = design_disagreement(ctx.design_files, [str(f) for f in result.files])
    if drift:
        ctx.journal("implement", "ok", drift)
        emit(f"[implement] {drift}")


async def _stage_review(
    ctx: RunContext, *, fixer: Any, tests: Any, max_rounds: int, emit: Callable[[str], None]
) -> None:
    """Review the change and fix what it finds, before anyone is asked to look at it.

    Runs on the worktree diff rather than a pull request: a loop that waited for a PR could
    never fix anything *before* opening one, which is the entire point.
    """
    from orchestrator.sdlc.reviewloop import review_and_fix

    if not ctx.worktree:
        ctx.record_stage("review", "skipped", "no worktree to review")
        emit("[review] skipped — nothing was built")
        return

    outcome = await review_and_fix(
        path=ctx.worktree,
        spec=ctx.spec or {},
        issue_key=ctx.issue_key,
        fixer=fixer,
        tests=tests,
        max_rounds=max_rounds,
    )
    path = ctx.write_artifact("review.md", outcome.render())
    detail = f"{outcome.stopped}"
    if outcome.remaining:
        detail += f" · {len(outcome.remaining)} unresolved"
    if outcome.deferred:
        detail += f" · {len(outcome.deferred)} for a human"
    ctx.record_stage("review", "ok" if outcome.clean else "failed", detail, path)
    emit(f"[review] {detail}")


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
