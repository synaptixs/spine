"""Plan & build, the pipeline half: the ``sdlc`` sub-app."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from ._common import _print

sdlc_app = typer.Typer(
    help="Run the end-to-end SDLC pipeline: plan, approve, build, review, complete.", no_args_is_help=True
)


@sdlc_app.command("run")
def sdlc_run(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), "
            "notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md.",
        ),
    ],
    actor: Annotated[
        str,
        typer.Option("--actor", help="Who is launching the run (recorded in audit rows)."),
    ] = "cli",
    create_jira: Annotated[
        bool,
        typer.Option(
            "--create-jira/--dry-run-jira",
            help="Write Jira issues for real (default: dry-run synthetic keys).",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait/--no-wait",
            help="Block until the workflow finishes and print its result (default: return after start).",
        ),
    ] = False,
    max_features: Annotated[
        int, typer.Option("--max-features", help="Cap features per run (0 = unlimited).")
    ] = 0,
    max_parallel: Annotated[
        int, typer.Option("--max-parallel", help="Feature children per batch (1 = sequential).")
    ] = 2,
) -> None:
    """Start the Block-C SDLC workflow on the sdlc-tasks queue.

    Generates a fresh sdlc_id and starts ``SDLCWorkflow`` with workflow id
    ``task-{sdlc_id}`` — the id convention the REST ``/v1/approvals/*`` API
    relies on to route gate decisions back to the workflow. The two human
    gates persist real, decidable ApprovalRequest rows
    (``sdlc-{sdlc_id}-0`` for intents, ``sdlc-{sdlc_id}-1`` for merge).

    A worker must be running on the sdlc-tasks queue
    (``python -m orchestrator.sdlc.worker``).
    """
    import asyncio

    asyncio.run(
        _run_sdlc(
            source,
            actor=actor,
            create_jira=create_jira,
            wait=wait,
            max_features=max_features,
            max_parallel=max_parallel,
        )
    )


async def _run_sdlc(
    source: str, *, actor: str, create_jira: bool, wait: bool, max_features: int = 0, max_parallel: int = 2
) -> None:
    import uuid

    from orchestrator.core.env import load_local_env
    from orchestrator.intake.factory import SUPPORTED_SOURCE_KINDS
    from orchestrator.intake.service import parse_source_uri
    from orchestrator.sdlc.types import SDLCWorkflowInput
    from orchestrator.sdlc.worker import sdlc_task_queue
    from orchestrator.sdlc.workflows import SDLCWorkflow
    from orchestrator.temporal import connect_client
    from orchestrator.temporal.config import TemporalConfig

    load_local_env()

    kind, _ = parse_source_uri(source)
    if kind not in SUPPORTED_SOURCE_KINDS:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_KINDS))
        typer.echo(f"Unsupported source kind {kind!r} (supported: {supported}).", err=True)
        raise typer.Exit(code=2)

    sdlc_id = uuid.uuid4().hex[:16]
    workflow_id = f"task-{sdlc_id}"
    queue = sdlc_task_queue()

    client = await connect_client(TemporalConfig.from_env())
    handle = await client.start_workflow(
        SDLCWorkflow.run,
        SDLCWorkflowInput(
            sdlc_id=sdlc_id,
            source_uri=source,
            actor=actor,
            # Bet 2c-ii: env-sourced tenant for CLI-launched runs (default
            # single-tenant). Scopes the run's approval + audit rows.
            tenant_id=os.getenv("ORCHESTRATOR_TENANT_ID", "default"),
            trace_id=sdlc_id,
            dry_run_jira=not create_jira,
            max_features=max_features,
            max_parallel_features=max_parallel,
        ),
        id=workflow_id,
        task_queue=queue,
    )
    _print(
        {
            "sdlc_id": sdlc_id,
            "workflow_id": workflow_id,
            "run_id": handle.result_run_id,
            "task_queue": queue,
            "gates": {
                "intents": f"sdlc-{sdlc_id}-0",
                "merge": f"sdlc-{sdlc_id}-1",
            },
        }
    )
    typer.echo(
        "\nDecide gate 1 (intents) via the approval API once intake completes. The "
        "gate's description lists any open questions; approve as-is, or answer them "
        "with a modify_input `clarifications` patch (folded into every spec):\n"
        f"  curl -X POST $ORCHESTRATOR_API_URL/v1/approvals/sdlc-{sdlc_id}-0/approve "
        '-H "x-api-key: $ORCHESTRATOR_API_KEY"\n'
        f"  curl -X POST $ORCHESTRATOR_API_URL/v1/approvals/sdlc-{sdlc_id}-0/modify_input "
        '-H "x-api-key: $ORCHESTRATOR_API_KEY" '
        '-d \'{"patch": {"clarifications": ["<answer the open questions>"]}}\'',
    )

    if not wait:
        return

    typer.echo("\nWaiting for the workflow to finish (Ctrl-C to detach)...")
    result = await handle.result()
    _print(result.__dict__ if hasattr(result, "__dict__") else result)


@sdlc_app.command("address-review")
def sdlc_address_review(
    pr: Annotated[str, typer.Option("--pr", help="The PR URL to address review comments on.")],
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Repo clone URL (defaults to SDLC_REPO_URL)."),
    ] = None,
    bot_login: Annotated[
        str | None,
        typer.Option("--bot-login", help="Skip this author's own comments (the agent's account)."),
    ] = None,
    max_refines: Annotated[int, typer.Option("--max-refines", help="Refine cycles to reach green.")] = 3,
) -> None:
    """Read a PR's human review comments, revise the change, and push the fix.

    Checks out the PR branch into a throwaway clone, feeds the reviewers'
    comments to codegen, re-drives to green (tests + preflight), and pushes a
    follow-up commit to the PR branch. Out-of-band and human-triggered — the
    autonomous run's merge gate stays the bookend. Needs SDLC_CODEGEN=llm and
    an authenticated ``gh``.
    """
    import asyncio

    asyncio.run(_run_address_review(pr=pr, repo=repo, bot_login=bot_login, max_refines=max_refines))


async def _run_address_review(*, pr: str, repo: str | None, bot_login: str | None, max_refines: int) -> None:
    import asyncio
    import os
    import tempfile

    from orchestrator.core.env import load_local_env
    from orchestrator.sdlc.review_response import respond_to_pr_feedback
    from orchestrator.sdlc.worker import build_deps

    load_local_env()
    repo_url = repo or os.getenv("SDLC_REPO_URL")
    if not repo_url:
        typer.echo("Set --repo or SDLC_REPO_URL to the repo clone URL.", err=True)
        raise typer.Exit(code=2)

    async def _run(*argv: str, cwd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        raw, _ = await proc.communicate()
        out = raw.decode("utf-8", "replace")
        if proc.returncode != 0:
            typer.echo(f"{argv[0]} failed: {out[-300:]}", err=True)
            raise typer.Exit(code=1)
        return out

    workdir = Path(tempfile.mkdtemp(prefix="sdlc-address-review-")) / "wt"
    workdir.mkdir(parents=True)
    typer.echo(f"Cloning and checking out PR {pr} …")
    await _run("git", "clone", "--quiet", repo_url, str(workdir), cwd=str(workdir.parent))
    await _run("gh", "pr", "checkout", pr, cwd=str(workdir))
    branch = (await _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=str(workdir))).strip()

    result = await respond_to_pr_feedback(
        build_deps(),
        pr_url=pr,
        branch=branch,
        path=str(workdir),
        bot_login=bot_login,
        max_refines=max_refines,
    )
    _print(result.__dict__)


@sdlc_app.command("baseline")
def sdlc_baseline(
    path: Annotated[str, typer.Option("--path", help="Repo whose graph the gate reads.")] = ".",
    as_json: Annotated[bool, typer.Option("--json", help="Emit the numbers as JSON.")] = False,
) -> None:
    """Score the run agent against a corpus of tickets whose right answer is known.

    Deterministic and free: the validity gate reads each ticket and a real graph, and every
    case has an argued expected verdict. Run metrics come from the durable run records —
    observations of what actually ran, not a simulation.

    False refusals and missed refusals are counted separately. A single accuracy number would
    let one hide behind the other, and they cost very different things.
    """
    import json as _json

    from orchestrator.evals.agent_corpus import render_report, score_gate, score_runs
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.sdlc.runstate import RunStore

    gate = score_gate(FactStore(load_or_extract(path)))
    runs = score_runs(RunStore().all())
    if as_json:
        typer.echo(
            _json.dumps(
                {
                    "gate": {
                        "accuracy": gate.accuracy,
                        "cases": len(gate.results),
                        "false_refusals": gate.false_refusals,
                        "missed_refusals": gate.missed_refusals,
                    },
                    "runs": {
                        "runs": runs.runs,
                        "completed": runs.completed,
                        "parked": runs.parked,
                        "failed": runs.failed,
                        "completion_rate": runs.completion_rate,
                        "intervention_rate": runs.intervention_rate,
                        "mean_cost_usd": runs.mean_cost_usd,
                    },
                },
                indent=2,
            )
        )
        return
    typer.echo(render_report(gate, runs))


@sdlc_app.command("explain")
def sdlc_explain(
    run_id: Annotated[str, typer.Argument(help="Run id, as printed by `sdlc autorun`.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the Case as JSON.")] = False,
) -> None:
    """Show the graph a run actually executed — node by node, with what each one produced.

    Reads the run's `case.json`. Every node appears, including the ones that were skipped and
    why: a summary listing only the nodes that ran cannot be told apart from one where the rest
    were never reached.

    Digests cover content, never timing — two identical runs must agree, and no two runs take
    the same time.
    """
    import json as _json

    from orchestrator.sdlc.autorun import default_artifacts_dir
    from orchestrator.sdlc.case import load_case

    path = default_artifacts_dir(run_id) / "case.json"
    if not path.is_file():
        typer.secho(f"No case for run {run_id!r} at {path}", fg=typer.colors.RED)
        typer.echo("A run older than Phase 2a has no Case; read its artifacts directory instead.")
        raise typer.Exit(code=2)

    case = load_case(path)
    if as_json:
        typer.echo(_json.dumps(case.to_dict(), indent=2, sort_keys=True))
        return
    typer.echo(case.render())


@sdlc_app.command("workflows")
def sdlc_workflows(
    path: Annotated[str, typer.Option("--path", help="Repo whose `.spine/workflows/` to include.")] = ".",
) -> None:
    """List the workflow profiles available, and which issue types choose them.

    Shipped profiles live in the package; a repo may carry its own in `.spine/workflows/`, where
    a profile of the same name **wins**. Both are listed, with the source, because "why did this
    run use that graph?" should be answerable without reading two directories.
    """
    from orchestrator.sdlc.profile_select import _BY_TYPE
    from orchestrator.sdlc.profiles import REPO_PROFILE_DIR, profile_names, repo_profile_names

    root = Path(path)
    carried = set(repo_profile_names(root))
    names = profile_names(root)
    if not names:
        typer.echo("No profiles found.")
        return

    by_profile: dict[str, list[str]] = {}
    for issue_type, profile in sorted(_BY_TYPE.items()):
        by_profile.setdefault(profile, []).append(issue_type)

    typer.echo(f"{'profile':<16} {'source':<10} chosen for")
    typer.echo(f"{'-' * 16} {'-' * 10} {'-' * 40}")
    for profile in names:
        source = "repo" if profile in carried else "shipped"
        types = ", ".join(by_profile.get(profile, [])) or (
            "any unmapped issue type" if profile == "default" else "—"
        )
        typer.echo(f"{profile:<16} {source:<10} {types}")
    if carried:
        typer.echo(f"\nRepo profiles read from {root / REPO_PROFILE_DIR} — same name wins over shipped.")


@sdlc_app.command("workflow")
def sdlc_workflow(
    name: Annotated[str, typer.Argument(help="Profile name, e.g. `default`.")] = "default",
    path: Annotated[
        str, typer.Option("--path", help="Repo whose `.spine/workflows/` to search first.")
    ] = ".",
    as_json: Annotated[bool, typer.Option("--json", help="Emit the validated IR as JSON.")] = False,
) -> None:
    """Show a workflow profile — the SDLC pipeline as a validated graph.

    Prints what each node is and, for deterministic nodes, which tool it names. Validation runs
    every time: a profile that cannot be validated is a packaging bug, and printing it as if it
    were fine is how a broken graph reaches a run.

    `sdlc autorun` executes one of these, chosen from the ticket's issue type — `sdlc workflows`
    lists which type picks which. A profile in the repo's `.spine/workflows/` wins over the
    shipped one of the same name.
    """
    import asyncio as _asyncio
    import json as _json

    from orchestrator.ir.graph import NodeType
    from orchestrator.ir.validator import IRValidator
    from orchestrator.sdlc.profiles import ProfileNotFoundError, load_profile, profile_names

    root = Path(path)
    try:
        ir = load_profile(name, root)
    except ProfileNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        typer.echo(f"Available: {', '.join(profile_names(root))}")
        raise typer.Exit(code=2) from exc

    report = _asyncio.run(IRValidator().validate(ir))
    if as_json:
        typer.echo(_json.dumps({"ir": ir.model_dump(mode="json"), "valid": report.ok}, indent=2))
        raise typer.Exit(code=0 if report.ok else 1)

    typer.echo(f"{ir.metadata.id}@{ir.metadata.version} — {ir.spec.workflow_pattern.value}")
    typer.echo(f"{ir.metadata.description.strip()}\n")
    successors: dict[str, list[str]] = {}
    for edge in ir.spec.edges:
        successors.setdefault(edge.source, []).append(edge.target)
    for node in ir.spec.nodes:
        kind = node.type.value
        tool = f" → {node.template_id}" if node.template_id else ""
        det = "deterministic" if node.type is NodeType.TOOL else "model"
        nxt = ", ".join(successors.get(node.id, [])) or "—"
        typer.echo(f"  {node.id:<16} {kind:<8} [{det}]{tool}")
        typer.echo(f"  {'':<16} next: {nxt}")
    typer.echo("")
    if report.ok:
        typer.secho(
            f"✓ valid — {len(ir.spec.nodes)} node(s), {len(ir.spec.edges)} edge(s)", fg=typer.colors.GREEN
        )
        return
    typer.secho(f"✗ invalid — {len(report.failures)} failure(s)", fg=typer.colors.RED)
    for failure in report.failures:
        typer.echo(f"  [{failure['rule']}] {failure['field']}: {failure['message']}")
    raise typer.Exit(code=1)


@sdlc_app.command("runs")
def sdlc_runs(
    action: Annotated[
        str,
        typer.Argument(
            help="list | show <run-id> | reap | approvals | approve <approval-id> — inspect and "
            "decide autorun's durable state."
        ),
    ] = "list",
    run_id: Annotated[str | None, typer.Argument(help="Run id, or approval id for approve.")] = None,
    reject: Annotated[bool, typer.Option("--reject", help="Reject rather than approve.")] = False,
    note: Annotated[str, typer.Option("--note", help="Why — recorded on the decision.")] = "",
) -> None:
    """Inspect what `sdlc autorun` has running, parked or abandoned.

    `reap` reports what a dead run left behind — worktree, branch, issue — and changes
    nothing: a worktree may hold the only copy of someone's work, and a ticket's status is
    an outward-facing write. Cleaning up stays a human's call.
    """
    import json as _json

    from orchestrator.sdlc.runstate import RunStore, render_reap, render_runs

    store = RunStore()
    if action == "list":
        typer.echo(render_runs(store.all()))
        return
    if action == "approvals":
        from orchestrator.sdlc.escalate import ApprovalStore, default_approval_dir, render_approvals

        typer.echo(render_approvals(ApprovalStore(root=default_approval_dir()).all()))
        return
    if action == "approve":
        from orchestrator.sdlc.escalate import ApprovalStore, decide, default_approval_dir

        if not run_id:
            typer.echo("approve needs an approval id (see `sdlc runs approvals`)", err=True)
            raise typer.Exit(code=2)
        try:
            decided = decide(
                run_id,
                approved=not reject,
                store=ApprovalStore(root=default_approval_dir()),
                note=note,
            )
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(f"{decided.approval_id}: {decided.decision}")
        typer.echo(f"Resume the run with: orchestrator sdlc autorun --resume {decided.run_id} …")
        return
    if action == "reap":
        stale = store.stale()
        typer.echo(render_reap(stale))
        raise typer.Exit(code=1 if stale else 0)
    if action == "show":
        if not run_id:
            typer.echo("show needs a run id", err=True)
            raise typer.Exit(code=2)
        record = store.load(run_id)
        if record is None:
            typer.echo(f"no run {run_id!r}", err=True)
            raise typer.Exit(code=2)
        typer.echo(_json.dumps(asdict(record), indent=2, sort_keys=True))
        return
    typer.echo(f"Unknown action {action!r}. Use list, show, reap, approvals or approve.", err=True)
    raise typer.Exit(code=2)


def _terminal_gate() -> Any:
    """Show the diff and ask, once, before the run's first write.

    Fails **closed** on a non-interactive stdin. A gate that assumes yes when nobody is
    there is not a gate — and this one is reached by unattended runs (cron, the MCP
    plugin, a backgrounded shell) as readily as by a person at a terminal.
    """
    import subprocess
    import sys

    async def gate(path: Path, files: list[str]) -> bool:
        # `add -N` makes new files show up in `git diff` without staging their content, so
        # the diff shown is the whole change rather than only the edits to tracked files.
        subprocess.run(["git", "-C", str(path), "add", "-N", "-A"], capture_output=True, check=False)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"], capture_output=True, text=True, check=False
        ).stdout
        stat = subprocess.run(
            ["git", "-C", str(path), "diff", "--stat"], capture_output=True, text=True, check=False
        ).stdout
        typer.echo("\n" + "=" * 70)
        typer.echo(f"HUMAN REVIEW — {len(files)} file(s) in {path}")
        typer.echo("=" * 70)
        typer.echo(diff or "(no textual diff)")
        typer.echo(stat)
        if not sys.stdin.isatty():
            typer.echo(
                "[gate] --review was asked for but there is no terminal to ask on. "
                "Refusing rather than assuming yes; nothing was committed.",
                err=True,
            )
            return False
        return bool(typer.confirm("Commit this change?", default=False))

    return gate


@sdlc_app.command("approve")
def sdlc_approve(
    intent: Annotated[str, typer.Argument(help="Intent id whose plan you are deciding, e.g. SSPN-49.")],
    path: Annotated[str, typer.Option("--path", help="Repo the plan was written for.")] = ".",
    by: Annotated[
        str | None, typer.Option("--by", help="Who is deciding (default: git config user.name).")
    ] = None,
    note: Annotated[str, typer.Option("--note", help="Why — recorded with the decision.")] = "",
    reject: Annotated[
        bool, typer.Option("--reject", help="Record a rejection instead of an approval.")
    ] = False,
    out: Annotated[
        Path | None, typer.Option("--out", help="Where the plan lives (default: <repo>/.spine/plans).")
    ] = None,
) -> None:
    """Record that a human read this build document and decided.

    The decision is bound to the document body it was made against, so a plan that changes
    afterwards is *stale* rather than silently still approved. `sdlc autorun` refuses to
    build without a current approval.
    """
    import datetime as _dt

    from orchestrator.sdlc.builddoc import (
        PlanApproval,
        decided_by_default,
        derived_at,
        plan_digest,
        plan_dir,
        save_approval,
    )

    plan_file = (Path(out) if out else plan_dir(path)) / f"{intent}-build.md"
    if not plan_file.is_file():
        typer.echo(
            f"No plan at {plan_file}. Produce one first: orchestrator sdlc plan --spec <file>",
            err=True,
        )
        raise typer.Exit(code=2)

    who = by or decided_by_default(path)
    if not who:
        typer.echo("Cannot tell who is approving — set git config user.name or pass --by.", err=True)
        raise typer.Exit(code=2)

    document = plan_file.read_text(encoding="utf-8")
    approval = PlanApproval(
        intent_id=intent,
        decision="REJECTED" if reject else "APPROVED",
        decided_by=who,
        # The date the human decided, not a derivation input — the document itself stays
        # deterministic because this lives beside it rather than inside it.
        decided_at=_dt.date.today().isoformat(),
        digest=plan_digest(document),
        commit=derived_at(path),
        note=note,
    )
    written = save_approval(approval, root=path, out=out)
    typer.echo(f"[plan] {approval.decision.lower()} by {who} — {written}")
    typer.echo("[plan] re-run `orchestrator sdlc plan` to see the status on the document itself.")


@sdlc_app.command("autorun")
def sdlc_autorun(
    source: Annotated[
        str,
        typer.Option("--source", help="Source root, e.g. jira://<issue-key>, confluence://<page_id>."),
    ],
    issue: Annotated[
        str | None,
        typer.Option("--issue", help="Adopt an existing tracker issue instead of creating one."),
    ] = None,
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent id to implement (default: the first).")
    ] = None,
    repo: Annotated[
        str | None, typer.Option("--repo", help="Git URL to branch from (default $SDLC_REPO_URL).")
    ] = None,
    path: Annotated[str, typer.Option("--path", help="Repo to reason about (the graph).")] = ".",
    live: Annotated[
        bool,
        typer.Option("--live/--safe", help="Write for real. Default --safe makes no external write."),
    ] = False,
    max_refine: Annotated[
        int,
        typer.Option(
            "--max-refine",
            help="Correction attempts allowed per check — tests, types and coverage each get their own.",
        ),
    ] = 5,
    review: Annotated[
        bool,
        typer.Option(
            "--review/--no-review",
            help="Show the diff and ask before committing or pushing anything.",
        ),
    ] = False,
    base: Annotated[str | None, typer.Option("--base", help="PR target branch.")] = None,
    language: Annotated[str, typer.Option("--language", help="Target language (auto detects).")] = "auto",
    issue_type: Annotated[
        str,
        typer.Option(
            "--issue-type",
            help="Override the ticket's issue type (Bug, Story, …). Default: read it from the ticket.",
        ),
    ] = "",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Where run artifacts go (default: a run dir under the temp dir)."),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Continue a run by id — adopts the issue it already created."),
    ] = None,
    max_cost: Annotated[
        float | None,
        typer.Option("--max-cost", help="Cap LLM spend (USD) for this run; exhausting it parks it."),
    ] = None,
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            help="Implement a hand-written spec (JSON) instead of deriving one from the source.",
        ),
    ] = None,
    plan_gate: Annotated[
        bool,
        typer.Option(
            "--plan-gate/--no-plan-gate",
            help="Refuse to build unless a human approved this ticket's build document.",
        ),
    ] = True,
) -> None:
    """Drive ONE ticket through the whole happy path: research → design → code → tests → PR.

    The stages are the commands you already have — `investigate`, `design`, `sdlc feature` —
    called in order with the same spec, and each result recorded. Default --safe makes no
    external write anywhere in the chain.

    It does not yet judge whether the ticket is worth doing, enforce a budget, survive a
    crash, or loop on review findings. Each stage says plainly when it skipped and why; see
    docs/specs/autonomous-run-agent.md for what lands when.
    """
    import asyncio

    from orchestrator.sdlc.autorun import AutorunError, autorun, render_summary
    from orchestrator.sdlc.spec_file import SpecFileError, load_spec_file

    # Before any work starts: a bad spec file should cost nothing to discover.
    try:
        injected = load_spec_file(spec) if spec else None
    except SpecFileError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def _go() -> None:
        try:
            ctx = await autorun(
                source,
                issue=issue,
                intent_id=intent,
                repo=repo,
                root=path,
                live=live,
                max_refine=max_refine,
                gate=_terminal_gate() if review else None,
                base_branch=base,
                language=language,
                issue_type=issue_type,
                artifacts_dir=out,
                resume=resume,
                max_cost_usd=max_cost,
                spec=injected,
                plan_gate=plan_gate,
                log=typer.echo,
            )
        except AutorunError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=exc.code) from exc
        typer.echo("\n" + render_summary(ctx))

    asyncio.run(_go())


@sdlc_app.command("plan")
def sdlc_plan(
    spec: Annotated[
        Path | None,
        typer.Option("--spec", help="A hand-written spec (JSON). Skips intake entirely."),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", help="Derive the spec instead, e.g. jira://<issue-key>."),
    ] = None,
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent id to plan (default: the first).")
    ] = None,
    path: Annotated[str, typer.Option("--path", help="Repo to reason about (the graph).")] = ".",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Where the document goes (default: <repo>/.spine/plans)."),
    ] = None,
    language: Annotated[str, typer.Option("--language", help="Target language for the prompt.")] = "python",
    issue_type: Annotated[
        str,
        typer.Option(
            "--issue-type",
            help="Override the ticket's issue type (Bug, Story, …). Default: read it from the ticket.",
        ),
    ] = "",
    quiet: Annotated[bool, typer.Option("--quiet", help="Write the document without printing it.")] = False,
) -> None:
    """Produce the build document for ONE ticket and stop. No worktree, no code, no spend.

    Runs intake → investigate → validity → design, renders the twelve sections of
    docs/specs/build-document.md, and persists it to `.spine/plans/<INTENT>-build.md`.
    With `--spec` there is no LLM anywhere in this path: same commit in, same document out.

    This is the gate that comes *before* code exists. `--review` still gates the diff.
    """
    import asyncio

    from orchestrator.sdlc.builddoc import build_plan, load_approval, load_journey, persist
    from orchestrator.sdlc.spec_file import SpecFileError, load_spec_file

    if not spec and not source:
        typer.echo("Give --spec <file.json> or --source <uri>.", err=True)
        raise typer.Exit(code=2)

    try:
        injected = load_spec_file(spec) if spec else None
    except SpecFileError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    async def _go() -> None:
        resolved = injected
        # The flag wins; otherwise the ticket answers. An injected spec has no ticket behind
        # it, so with `--spec` and no flag the document is honestly untyped.
        resolved_type = issue_type
        if resolved is None:
            from orchestrator.core.env import load_local_env
            from orchestrator.core.llm.client import LLMError
            from orchestrator.intake.cache import analyze_cached
            from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
            from orchestrator.intake.service import SourceUriError

            load_local_env()
            try:
                service = build_service_for(str(source), dry_run=True)
            except (SourceUriError, IntakeNotConfiguredError) as exc:
                typer.echo(f"ERROR: {exc}", err=True)
                raise typer.Exit(code=2) from exc
            try:
                plan_result = await analyze_cached(service, str(source), refresh=False, log=lambda _m: None)
            except LLMError as exc:
                typer.echo(f"ERROR: {exc}", err=True)
                raise typer.Exit(code=2) from exc
            if not plan_result.specs:
                typer.echo("No specs derived from the source — nothing to plan.", err=True)
                raise typer.Exit(code=3)
            chosen = (
                next((s for s in plan_result.specs if s.intent_id == intent), None)
                if intent
                else plan_result.specs[0]
            )
            if chosen is None:
                ids = ", ".join(s.intent_id for s in plan_result.specs)
                typer.echo(f"Intent {intent!r} not found. Available: {ids}", err=True)
                raise typer.Exit(code=3)
            resolved = chosen.model_dump()
            if not resolved_type:
                from orchestrator.intake.ticket_meta import resolve_ticket_meta

                resolved_type = resolve_ticket_meta(plan_result, chosen).issue_type

        intent_key = str(resolved.get("intent_id") or "spec")
        document = await build_plan(
            resolved,
            root=path,
            language=language,
            issue_type=resolved_type,
            # Rendered, never stored in the document: a plan that changed since it was
            # approved shows as stale rather than carrying an approval it outgrew.
            approval=load_approval(intent_key, root=path, out=out),
            # What every run of this ticket did, appended beneath the plan. Regenerating
            # is what refreshes the view; the entries themselves are never rewritten.
            journey=load_journey(intent_key, root=path, out=out),
        )
        written, superseded = persist(document, intent_id=intent_key, root=path, out=out)
        if not quiet:
            typer.echo(document)
        typer.echo(f"[plan] {written}", err=True)
        if superseded is not None:
            typer.echo(f"[plan] superseded document kept at {superseded}", err=True)

    asyncio.run(_go())


@sdlc_app.command("feature")
def sdlc_feature(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), "
            "notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md.",
        ),
    ],
    intent: Annotated[
        str | None,
        typer.Option("--intent", help="Intent id to implement (default: first derived intent)."),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option("--repo", help="Git URL to branch from (default $SDLC_REPO_URL; scratch if unset)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Codegen model (default: $SDLC_CODEGEN_MODEL or the adapter default)."),
    ] = None,
    max_refine: Annotated[
        int,
        # Matches `sdlc autorun`: the type checker draws on this budget too, so a run that
        # greens its suite on the first pass still has the checker to satisfy. Left at 3, this
        # flag silently overrode the raise and reintroduced the shortfall on this path only.
        typer.Option(
            "--max-refine",
            help="Correction attempts allowed per check — tests, types and coverage each get their own.",
        ),
    ] = 5,
    live: Annotated[
        bool,
        typer.Option(
            "--live/--safe",
            help="Write for real: create the Jira issue, push the branch + open a PR, comment on Jira. "
            "Default --safe stays local (branch + commit + diff, dry-run Jira, no push).",
        ),
    ] = False,
    issue: Annotated[
        str | None,
        typer.Option(
            "--issue",
            help="Adopt an existing tracker issue (e.g. SSPN-9) instead of creating one — the "
            "branch, PR, comment and transition all land on it.",
        ),
    ] = None,
    base: Annotated[
        str | None,
        typer.Option(
            "--base",
            help="PR target branch (default: $SDLC_PR_BASE, else the repo's default branch).",
        ),
    ] = None,
    layout: Annotated[
        str,
        typer.Option(
            "--layout",
            help="Target structure: auto (scaffold only empty repos), new (always scaffold a "
            "src/<pkg>/ skeleton), or existing (follow the repo's layout).",
        ),
    ] = "auto",
    package_name: Annotated[
        str | None,
        typer.Option(
            "--package-name", help="Override the scaffold package name (default: derived from repo)."
        ),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-extract intents from the source (default: reuse the cached, deterministic backlog).",
        ),
    ] = False,
    language: Annotated[
        str,
        typer.Option(
            "--language",
            help="Target language: auto (detect), python, java, typescript, csharp, c, cpp, go, or sql.",
        ),
    ] = "auto",
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            help="Implement a hand-written spec (JSON) instead of deriving one from the source.",
        ),
    ] = None,
) -> None:
    """Linear pipeline for ONE intent, end to end.

    source → intent → spec → Jira issue → worktree branch → code generation
    → test + refine → commit → (push + PR) → Jira update → ready for deployment.

    Default --safe makes no external write: it creates a local branch, commits
    the generated + tested code, and prints the diff. Pass --live to create the
    Jira issue, push the branch, open a real PR, and comment the PR link back on
    the issue.

    Pass --issue <KEY> when the work is already tracked: the run adopts that
    issue instead of creating a second one for the same story.
    """
    import asyncio

    from orchestrator.sdlc.feature_runner import unsupported_language_error
    from orchestrator.sdlc.spec_file import SpecFileError, load_spec_file

    lang_error = unsupported_language_error(language)
    if lang_error is not None:
        typer.echo(f"ERROR: {lang_error}", err=True)
        raise typer.Exit(code=2)

    # Before any work starts: a bad spec file should cost nothing to discover.
    try:
        injected = load_spec_file(spec) if spec else None
    except SpecFileError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    asyncio.run(
        _run_sdlc_feature(
            source,
            spec=injected,
            intent_id=intent,
            repo=repo,
            model=model,
            max_refine=max_refine,
            live=live,
            issue=issue,
            base=base,
            layout_mode=layout,
            package_name=package_name,
            refresh=refresh,
            language=language,
        )
    )


async def _run_sdlc_feature(
    source: str,
    *,
    intent_id: str | None,
    repo: str | None,
    model: str | None,
    max_refine: int,
    live: bool,
    issue: str | None,
    base: str | None,
    layout_mode: str,
    package_name: str | None,
    refresh: bool,
    language: str,
    spec: dict[str, Any] | None = None,
) -> None:
    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature

    try:
        result = await run_feature(
            source,
            spec=spec,
            intent_id=intent_id,
            repo=repo,
            model=model,
            max_refine=max_refine,
            live=live,
            issue=issue,
            base_branch=base,
            layout_mode=layout_mode,
            package_name=package_name,
            refresh=refresh,
            language=language,
            log=typer.echo,  # stream the pipeline's progress to stdout
        )
    except FeatureRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.code) from exc

    typer.echo("\n" + "=" * 70)
    typer.echo("VERDICT: PASSED — ready for deployment.")
    typer.echo(f"  issue:  {result.issue_key}")
    typer.echo(f"  branch: {result.branch}")
    typer.echo(f"  files:  {result.files}")
    if not result.live:
        typer.echo(f"  diff:   git -C {result.worktree} show --stat HEAD")
        typer.echo("  Re-run with --live to create the Jira issue, push, and open a real PR.")
    typer.echo("=" * 70)


@sdlc_app.command("remediate")
def sdlc_remediate(
    report: Annotated[str, typer.Option("--report", help="Path to an infodrift full_report JSON.")],
    mappings: Annotated[
        str,
        typer.Option("--mappings", help="Path to the confirmed code↔ontology MappingStore JSON."),
    ] = "spine-mappings.json",
    repo: Annotated[
        str | None, typer.Option("--repo", help="Git URL to branch from (default $SDLC_REPO_URL).")
    ] = None,
    min_severity: Annotated[
        str,
        typer.Option("--min-severity", help="Only remediate findings at/above: warning | critical."),
    ] = "warning",
    live: Annotated[
        bool,
        typer.Option(
            "--live/--safe",
            help="--safe (default) leaves a reviewable branch+diff per entity (human-gated); "
            "--live opens PRs.",
        ),
    ] = False,
) -> None:
    """Spine Seam 3: a drift report → governed remediation runs (one per affected entity).

    Plans scoped, guardrailed remediation tasks from the infodrift report (Phase 2) and
    runs each through the codegen pipeline with the task as the spec (intake skipped),
    grounded by ontomesh (Seam 1) when configured. Default --safe is human-gated: it
    leaves a branch + diff to review; --live opens PRs.
    """
    import asyncio

    asyncio.run(
        _run_sdlc_remediate(
            report_path=report,
            mappings_path=mappings,
            repo=repo,
            min_severity=min_severity,
            live=live,
        )
    )


async def _run_sdlc_remediate(
    *, report_path: str, mappings_path: str, repo: str | None, min_severity: str, live: bool
) -> None:
    import json
    from pathlib import Path

    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature
    from orchestrator.spine import (
        DriftReport,
        MappingStore,
        RemediationTask,
        execute_remediations,
        infer_entity_iris,
    )

    payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    report = DriftReport.from_infodrift(payload)
    store = MappingStore(mappings_path)
    entity_iris = infer_entity_iris(report, store.load())

    async def _runner(task: RemediationTask) -> str:
        result = await run_feature(
            source="spine://remediation", spec=task.spec, repo=repo, live=live, log=typer.echo
        )
        return result.branch

    try:
        outcomes = await execute_remediations(
            report,
            runner=_runner,
            entity_iris=entity_iris,
            code_for_iri=store.code_for_iri(),
            min_severity=min_severity,
        )
    except FeatureRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.code) from exc

    if not outcomes:
        typer.echo("No material drift findings — nothing to remediate.")
        return
    typer.echo("\n" + "=" * 70)
    typer.echo(f"REMEDIATION: {len(outcomes)} task(s)")
    for outcome in outcomes:
        status = "OK" if outcome.ok else "FAILED"
        scope = "" if outcome.result is None else f" → {outcome.result}"
        typer.echo(f"  [{status}] {outcome.entity_key}: {outcome.detail}{scope}")
    typer.echo("=" * 70)


@sdlc_app.command("complete")
def sdlc_complete(
    pr: Annotated[str, typer.Option("--pr", help="The merged PR URL whose linked issue to close.")],
    issue: Annotated[
        str | None,
        typer.Option("--issue", help="Issue key (default: derived from the PR branch feat/<id>/<KEY>)."),
    ] = None,
    status: Annotated[
        str, typer.Option("--status", help="Target Jira status to move the issue to.")
    ] = "Done",
    allow_unmerged: Annotated[
        bool, typer.Option("--allow-unmerged", help="Transition even if the PR is not merged yet.")
    ] = False,
) -> None:
    """Close the Jira issue for a merged PR (the merge → Done bookend).

    The linear ``sdlc feature`` path stops at an open PR for a human to review
    and merge; this reconciles Jira afterwards. Verifies the PR is merged (via
    ``gh``), derives the issue key from the PR's head branch
    (``feat/<sdlc_id>/<KEY>``) unless ``--issue`` is given, then transitions the
    issue and comments the merge. Needs an authenticated ``gh``.
    """
    import asyncio

    asyncio.run(_run_sdlc_complete(pr=pr, issue=issue, status=status, allow_unmerged=allow_unmerged))


def _issue_key_from_branch(branch: str) -> str | None:
    """Issue key from a feature branch ``feat/<sdlc_id>/<ISSUE-KEY>``."""
    parts = branch.split("/")
    if len(parts) >= 3 and parts[0] == "feat" and parts[-1]:
        return parts[-1]
    return None


async def _run_sdlc_complete(*, pr: str, issue: str | None, status: str, allow_unmerged: bool) -> None:
    import asyncio
    import json

    from orchestrator.core.env import load_local_env
    from orchestrator.intake.jira import IssueTrackerError, JiraAdapter, JiraConfig

    load_local_env()

    # Inspect the PR via gh: merge state + head branch (to derive the issue key).
    proc = await asyncio.create_subprocess_exec(
        "gh",
        "pr",
        "view",
        pr,
        "--json",
        "state,mergedAt,headRefName",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    raw, _ = await proc.communicate()
    out = raw.decode("utf-8", "replace")
    if proc.returncode != 0:
        typer.echo(f"gh pr view failed: {out[-300:]}", err=True)
        raise typer.Exit(code=1)
    info = json.loads(out)
    merged = bool(info.get("mergedAt")) or str(info.get("state", "")).upper() == "MERGED"
    if not merged and not allow_unmerged:
        typer.echo(
            f"PR {pr} is not merged (state={info.get('state')}). Pass --allow-unmerged to override.",
            err=True,
        )
        raise typer.Exit(code=3)

    issue_key = issue or _issue_key_from_branch(str(info.get("headRefName") or ""))
    if not issue_key:
        typer.echo("Could not derive the issue key from the PR branch; pass --issue.", err=True)
        raise typer.Exit(code=2)

    # Force a real (non-dry-run) tracker — closing the ticket is the whole point.
    jira = JiraAdapter(JiraConfig(dry_run=False))
    try:
        moved = await jira.transition_issue(issue_key, status)
        await jira.comment_issue(issue_key, f"Merged via {pr}.")
    except IssueTrackerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        await jira.aclose()

    # Mark the backlog intent done (done = PR merged) and refresh the local ledger.
    backlog_done = False
    if merged:
        from orchestrator.intake.backlog_doc import backlog_path, write_backlog
        from orchestrator.intake.cache import complete_by_pr, load_progress

        matched = complete_by_pr(pr)
        if matched is not None:
            src, plan = matched
            write_backlog(backlog_path(), src, plan, load_progress(src))
            backlog_done = True

    _print(
        {
            "issue": issue_key,
            "pr": pr,
            "merged": merged,
            "status": moved or status,
            "backlog_done": backlog_done,
        }
    )
