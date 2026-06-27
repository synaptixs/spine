"""Command-line client for the registry service.

Usage:
    orchestrator template register path/to/template.json
    orchestrator template list
    orchestrator template show research.summarizer
    orchestrator template show research.summarizer 0.1.0
    orchestrator template publish research.summarizer 0.1.0
    orchestrator template deprecate research.summarizer 0.1.0

Same surface under ``orchestrator contract <...>`` for tool contracts.

Configuration via environment variables:
    ORCHESTRATOR_API_URL   default http://localhost:8000
    ORCHESTRATOR_API_KEY   default dev-key
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

app = typer.Typer(help="Orchestrator registry client.", no_args_is_help=True)
template_app = typer.Typer(help="Manage agent templates.", no_args_is_help=True)
contract_app = typer.Typer(help="Manage tool contracts.", no_args_is_help=True)
task_app = typer.Typer(help="Submit tasks for execution.", no_args_is_help=True)
sdlc_app = typer.Typer(help="Run the end-to-end SDLC orchestration (Block C).", no_args_is_help=True)
mcp_app = typer.Typer(help="Onboard external MCP servers (DBs, Atlassian, …).", no_args_is_help=True)
catalog_app = typer.Typer(
    help="Capability catalog — inspect what the orchestrator can assemble.", no_args_is_help=True
)
app.add_typer(template_app, name="template")
app.add_typer(contract_app, name="contract")
app.add_typer(task_app, name="task")
app.add_typer(sdlc_app, name="sdlc")
app.add_typer(mcp_app, name="mcp")
app.add_typer(catalog_app, name="catalog")


def _client() -> httpx.Client:
    base_url = os.getenv("ORCHESTRATOR_API_URL", "http://localhost:8000")
    api_key = os.getenv("ORCHESTRATOR_API_KEY", "dev-key")
    timeout = float(os.getenv("ORCHESTRATOR_API_TIMEOUT_SECONDS", "60"))
    return httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=httpx.Timeout(timeout))


def _load_payload(path: Path) -> dict[str, Any]:
    import yaml

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded: dict[str, Any] = yaml.safe_load(text)
        return loaded
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _check(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except json.JSONDecodeError:
            detail = resp.text
        typer.echo(f"Error {resp.status_code}: {json.dumps(detail, indent=2)}", err=True)
        raise typer.Exit(code=1)
    body: dict[str, Any] = resp.json()
    return body


def _register(entity: str, file: Path) -> None:
    payload = _load_payload(file)
    with _client() as client:
        _print(_check(client.post(f"/v1/{entity}", json=payload)))


def _list(entity: str, tag: str | None, status: str | None) -> None:
    params: dict[str, str] = {}
    if tag:
        params["tag"] = tag
    if status:
        params["status"] = status
    with _client() as client:
        _print(_check(client.get(f"/v1/{entity}", params=params)))


def _show(entity: str, id: str, version: str | None) -> None:
    suffix = f"/{version}" if version else ""
    with _client() as client:
        _print(_check(client.get(f"/v1/{entity}/{id}{suffix}")))


def _publish(entity: str, id: str, version: str) -> None:
    with _client() as client:
        _print(_check(client.post(f"/v1/{entity}/{id}/{version}/publish")))


def _deprecate(entity: str, id: str, version: str) -> None:
    with _client() as client:
        _print(_check(client.post(f"/v1/{entity}/{id}/{version}/deprecate")))


@template_app.command("register")
def template_register(file: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Register a new agent template from a JSON or YAML file."""
    _register("agent-templates", file)


@template_app.command("list")
def template_list(
    tag: Annotated[str | None, typer.Option(help="Filter by tag.")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by lifecycle state.")] = None,
) -> None:
    """List agent templates."""
    _list("agent-templates", tag, status)


@template_app.command("show")
def template_show(id: str, version: Annotated[str | None, typer.Argument()] = None) -> None:
    """Show the latest published version (or a specific version)."""
    _show("agent-templates", id, version)


@template_app.command("publish")
def template_publish(id: str, version: str) -> None:
    """Promote a draft to published."""
    _publish("agent-templates", id, version)


@template_app.command("deprecate")
def template_deprecate(id: str, version: str) -> None:
    """Mark a published version as deprecated."""
    _deprecate("agent-templates", id, version)


@contract_app.command("register")
def contract_register(file: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Register a new tool contract from a JSON or YAML file."""
    _register("tool-contracts", file)


@contract_app.command("list")
def contract_list(
    tag: Annotated[str | None, typer.Option(help="Filter by tag.")] = None,
    status: Annotated[str | None, typer.Option(help="Filter by lifecycle state.")] = None,
) -> None:
    """List tool contracts."""
    _list("tool-contracts", tag, status)


@contract_app.command("show")
def contract_show(id: str, version: Annotated[str | None, typer.Argument()] = None) -> None:
    """Show the latest published version (or a specific version)."""
    _show("tool-contracts", id, version)


@contract_app.command("publish")
def contract_publish(id: str, version: str) -> None:
    """Promote a draft to published."""
    _publish("tool-contracts", id, version)


@contract_app.command("deprecate")
def contract_deprecate(id: str, version: str) -> None:
    """Mark a published version as deprecated."""
    _deprecate("tool-contracts", id, version)


@task_app.command("submit")
def task_submit(
    objective: str,
    template_id: Annotated[
        str | None,
        typer.Option("--template", help="Pin a specific template id; planner chooses otherwise."),
    ] = None,
    template_version: Annotated[
        str | None,
        typer.Option("--version", help="Pin a specific template version."),
    ] = None,
) -> None:
    """Submit a task to the orchestrator and print the final state."""
    body: dict[str, Any] = {"objective": objective}
    if template_id:
        ref: dict[str, Any] = {"id": template_id}
        if template_version:
            ref["version"] = template_version
        body["template"] = ref
    with _client() as client:
        _print(_check(client.post("/v1/tasks", json=body)))


@app.command("ingest")
def ingest(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, notion://<page_id>, or file://./spec.md.",
        ),
    ],
    create: Annotated[
        bool,
        typer.Option("--create/--dry-run", help="Create issues for real (default: dry-run preview)."),
    ] = False,
    rules: Annotated[
        str | None,
        typer.Option("--rules", help="Path to a gap-rules YAML (defaults to built-ins)."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Create even when gaps gate the intent-approval bookend."),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-extract from the source (default: reuse the cached backlog)."),
    ] = False,
) -> None:
    """Source (Confluence / Notion / local files) → intents → gaps → specs → Jira backlog.

    Dry-run by default: fetches the source tree, derives intents, flags
    gaps, drafts specs, and prints the would-be Jira issues without writing
    anything. Pass --create to write to Jira (refused when gaps gate
    approval unless --force).

    The lowest-friction source is local files — no SaaS account needed:

        orchestrator ingest --source file://./examples/intake/sample-spec.md

    (An LLM key is still required for the intent/spec stages.)
    """
    import asyncio

    asyncio.run(_run_ingest(source, create=create, rules_path=rules, force=force, refresh=refresh))


async def _run_ingest(
    source: str, *, create: bool, rules_path: str | None, force: bool, refresh: bool
) -> None:
    from orchestrator.core.env import load_local_env

    # Bridge .env → os.environ so LiteLLM sees the provider key and the
    # ORCHESTRATOR_INTAKE_MODEL override is visible to the factory.
    load_local_env()
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.service import parse_source_uri, spec_to_issue_request

    parse_source_uri(source)  # validate the source URI early

    try:
        service = build_service_for(source, dry_run=not create, rules_path=rules_path)
    except IntakeNotConfiguredError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    plan = await analyze_cached(service, source, refresh=refresh, log=lambda m: typer.echo(m, err=True))
    _print(
        {
            "documents": len(plan.documents),
            "truncated": plan.truncated,
            "intents": [i.model_dump() for i in plan.intents],
            "gaps": [
                {"intent": g.intent_id, "rule": g.rule_id, "severity": g.severity.value, "message": g.message}
                for g in plan.gaps
            ],
            "blocked": plan.blocked,
            "would_create": [
                {"summary": spec_to_issue_request(s).summary, "intent": s.intent_id} for s in plan.specs
            ],
        }
    )

    if not create:
        typer.echo("\nDry-run: no issues created. Re-run with --create to write to Jira.")
        return
    if plan.blocked and not force:
        typer.echo(
            "\nGaps gate the intent-approval bookend; refusing to create. Resolve the gaps or pass --force.",
            err=True,
        )
        raise typer.Exit(code=3)

    issues = await service.create_issues(plan, link_dependencies=True)
    _print({"created": [{"key": i.key, "url": i.url} for i in issues]})


@app.command("backlog")
def backlog(
    source: Annotated[
        str,
        typer.Option("--source", help="Source URI whose cached backlog to render, e.g. confluence://<id>."),
    ],
    out: Annotated[
        str | None,
        typer.Option("--out", help="Write the markdown here (default: print to stdout)."),
    ] = None,
) -> None:
    """Render the cached backlog + completion progress as markdown (read-only).

    Reads the persisted backlog (from a prior ingest / sdlc feature run) and
    prints a checkbox ledger: [ ] todo, [~] in progress, [x] done. Pass --out to
    write a BACKLOG.md.
    """
    from orchestrator.intake.backlog_doc import render_markdown, write_backlog
    from orchestrator.intake.cache import load_cached_plan, load_progress

    plan = load_cached_plan(source)
    if plan is None:
        typer.echo(
            f"No cached backlog for {source}. Run `ingest` or `sdlc feature` (optionally --refresh) first.",
            err=True,
        )
        raise typer.Exit(code=1)
    progress = load_progress(source)
    if out:
        typer.echo(f"wrote {write_backlog(out, source, plan, progress)}")
    else:
        typer.echo(render_markdown(source, plan, progress), nl=False)


@sdlc_app.command("run")
def sdlc_run(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, notion://<page_id>, or file://./spec.md.",
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


@sdlc_app.command("feature")
def sdlc_feature(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Source root, e.g. confluence://<page_id>, notion://<page_id>, or file://./spec.md.",
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
        typer.Option("--max-refine", help="Max implement→test→refine iterations."),
    ] = 3,
    live: Annotated[
        bool,
        typer.Option(
            "--live/--safe",
            help="Write for real: create the Jira issue, push the branch + open a PR, comment on Jira. "
            "Default --safe stays local (branch + commit + diff, dry-run Jira, no push).",
        ),
    ] = False,
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
        typer.Option("--language", help="Target language: auto (detect), python, java, or typescript."),
    ] = "auto",
) -> None:
    """Linear pipeline for ONE intent, end to end.

    source → intent → spec → Jira issue → worktree branch → code generation
    → test + refine → commit → (push + PR) → Jira update → ready for deployment.

    Default --safe makes no external write: it creates a local branch, commits
    the generated + tested code, and prints the diff. Pass --live to create the
    Jira issue, push the branch, open a real PR, and comment the PR link back on
    the issue.
    """
    import asyncio

    asyncio.run(
        _run_sdlc_feature(
            source,
            intent_id=intent,
            repo=repo,
            model=model,
            max_refine=max_refine,
            live=live,
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
    layout_mode: str,
    package_name: str | None,
    refresh: bool,
    language: str,
) -> None:
    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature

    try:
        result = await run_feature(
            source,
            intent_id=intent_id,
            repo=repo,
            model=model,
            max_refine=max_refine,
            live=live,
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


# ---------------------------------------------------------------------------
# mcp — onboard external MCP servers (Phase 1: discover + invoke their tools)
# ---------------------------------------------------------------------------


@mcp_app.command("list")
def mcp_list(
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            help="Path to an mcpServers JSON file (default: $ORCHESTRATOR_MCP_CONFIG or ./mcp.json).",
        ),
    ] = None,
) -> None:
    """Discover the allow-listed tools across all configured MCP servers."""
    import asyncio

    from orchestrator.core.env import load_local_env
    from orchestrator.mcp import MCPRegistry

    load_local_env()
    registry = MCPRegistry.from_config(config)
    servers = registry.server_names()
    if not servers:
        typer.echo("No MCP servers configured. Add an mcpServers JSON file (--config or ./mcp.json).")
        return
    tools = asyncio.run(registry.list_tools())
    _print(
        {
            "servers": servers,
            "tools": [
                {
                    "name": t.qualified_name,
                    "read_only": t.read_only,
                    "description": (t.description or "")[:120],
                }
                for t in tools
            ],
        }
    )


@mcp_app.command("ingest-db")
def mcp_ingest_db(
    server: Annotated[str, typer.Option("--server", help="Name of an onboarded DB MCP server.")],
    query_tool: Annotated[
        str, typer.Option("--query-tool", help="The server's SQL query tool name.")
    ] = "query",
    sql_arg: Annotated[str, typer.Option("--sql-arg", help="The query tool's SQL argument name.")] = "sql",
    schema: Annotated[str, typer.Option("--schema", help="DB schema to introspect.")] = "public",
    config: Annotated[str | None, typer.Option("--config", help="mcpServers JSON file path.")] = None,
) -> None:
    """Introspect a DB MCP server's schema into PKG data-layer facts (Entity/Field)."""
    import asyncio

    from orchestrator.core.env import load_local_env
    from orchestrator.mcp import MCPRegistry
    from orchestrator.mcp.db import introspect_via_mcp
    from orchestrator.pkg.schema import schema_to_facts

    load_local_env()
    registry = MCPRegistry.from_config(config)
    db = asyncio.run(
        introspect_via_mcp(registry, server=server, query_tool=query_tool, sql_arg=sql_arg, db_schema=schema)
    )
    facts = schema_to_facts(db)
    _print(
        {
            "database": db.database,
            "tables": {t.name: [c.name for c in t.columns] for t in db.tables},
            "pkg_facts": facts.counts(),
        }
    )


@mcp_app.command("contracts")
def mcp_contracts(
    config: Annotated[str | None, typer.Option("--config", help="mcpServers JSON file path.")] = None,
) -> None:
    """Show the ToolContract derived for each onboarded MCP tool (governance view)."""
    import asyncio

    from orchestrator.core.env import load_local_env
    from orchestrator.mcp import MCPRegistry, build_mcp_tools
    from orchestrator.mcp.config import load_mcp_configs

    load_local_env()
    configs = load_mcp_configs(config)
    registry = MCPRegistry(configs)
    built = asyncio.run(build_mcp_tools(registry, configs=configs))
    _print(
        [
            {
                "contract_id": t.contract.metadata.id,
                "version": t.contract.metadata.version,
                "side_effects": t.contract.spec.side_effects.value,
                "requires_approval": t.contract.spec.requires_approval.value,
                "write_gated": not t.handler.read_only and not t.handler.write_enabled,
                "inputs": [f.name for f in t.contract.spec.inputs],
            }
            for t in built
        ]
    )


@mcp_app.command("call")
def mcp_call(
    tool: Annotated[str, typer.Argument(help="Qualified tool name: server:tool.")],
    args: Annotated[str, typer.Option("--args", help="JSON object of tool arguments.")] = "{}",
    config: Annotated[str | None, typer.Option("--config", help="mcpServers JSON file path.")] = None,
) -> None:
    """Invoke one onboarded MCP tool (server:tool) with JSON arguments."""
    import asyncio

    from orchestrator.core.env import load_local_env
    from orchestrator.mcp import MCPRegistry

    load_local_env()
    try:
        arguments = json.loads(args)
    except json.JSONDecodeError as exc:
        typer.echo(f"--args is not valid JSON: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not isinstance(arguments, dict):
        typer.echo("--args must be a JSON object.", err=True)
        raise typer.Exit(code=2)

    registry = MCPRegistry.from_config(config)
    try:
        result = asyncio.run(registry.call(tool, arguments))
    except (KeyError, PermissionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    _print({"tool": tool, "is_error": result.is_error, "text": result.text})


# ---------------------------------------------------------------------------
# pkg — Product Knowledge Graph (Layer 1: grounded code extraction)
# ---------------------------------------------------------------------------

pkg_app = typer.Typer(help="Product Knowledge Graph — code extraction (read-only).", no_args_is_help=True)
app.add_typer(pkg_app, name="pkg")


@app.command("tui")
def tui(
    api_url: Annotated[
        str, typer.Option("--api-url", help="Registry API base URL.", envvar="ORCHESTRATOR_API_URL")
    ] = "http://localhost:8000",
    api_key: Annotated[
        str, typer.Option("--api-key", help="API key for the registry.", envvar="ORCHESTRATOR_API_KEY")
    ] = "dev-key",
) -> None:
    """Launch the terminal UI: watch runs, clear gates, and delegate a run.

    A keyboard-driven cousin of the web inbox over the same ``/v1`` API. Needs the
    ``tui`` extra: ``pip install 'synaptixs-spine[tui]'``.
    """
    try:
        from orchestrator.tui.app import run_tui
    except ImportError as exc:  # textual is the optional `tui` extra
        typer.echo("The TUI needs the 'tui' extra. Install it: pip install 'synaptixs-spine[tui]'.", err=True)
        raise typer.Exit(code=2) from exc
    run_tui(api_url, api_key)


@app.command("doctor")
def doctor() -> None:
    """Check environment readiness and print a diagnostic report.

    Bridges ``.env`` into the process environment first (same as ``ingest`` /
    ``sdlc``), so the report reflects exactly what the pipeline will see — a
    real exported variable still wins over the file.
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.doctor import render_report, run_env_checks

    loaded = load_local_env()
    if loaded:
        typer.echo(f"Loaded {loaded} variable(s) from .env\n")

    results = run_env_checks()
    report = render_report(results)
    typer.echo(report)
    all_passed = all(r.passed for r in results)
    if not all_passed:
        raise typer.Exit(code=1)


@app.command("init")
def init(
    path: Annotated[Path, typer.Option("--path", help="Directory to scaffold the .env into.")] = Path("."),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing .env with a fresh template.")
    ] = False,
) -> None:
    """Scaffold a new project: create a .env from the template, then guide setup.

    Creates a commented .env skeleton (from the same env groups ``doctor``
    checks), then reports readiness. While required variables are still unset it
    exits non-zero with a call to fill them in and re-run — so ``init`` is the
    one-command setup loop: run it, fill the blanks, run it again until green.

    Safe to re-run: an existing .env is never overwritten (only missing keys are
    appended) unless --force.
    """
    from orchestrator.doctor import render_report, run_env_checks
    from orchestrator.init_scaffold import parse_env_file, scaffold_env

    env_path = path / ".env"
    existed = env_path.exists()
    wrote, added = scaffold_env(env_path, force=force)
    if not existed and wrote:
        typer.echo(f"Created {env_path} from the template.")
    elif added:
        typer.echo(f"Extended {env_path} (+{len(added)} key(s): {', '.join(added)}).")
    else:
        typer.echo(f"{env_path} already has every required key — nothing to add.")

    # Report readiness against what's now in the file (blank values don't count),
    # so the operator sees exactly what's left to provide.
    current = parse_env_file(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    results = run_env_checks({k: v for k, v in current.items() if v})
    typer.echo("")
    typer.echo(render_report(results))
    typer.echo("")

    if all(r.passed for r in results):
        typer.echo(f"✓ Environment ready — every required variable is set in {env_path}.")
        return

    failed = [r.name for r in results if not r.passed]
    typer.echo(f"Action required — fill in the variables for: {', '.join(failed)}.")
    typer.echo(f"  1. Open {env_path} and provide the values (see .env.example for the full annotated list).")
    typer.echo("  2. Re-run `orchestrator init` (or `orchestrator doctor`) to verify.")
    raise typer.Exit(code=1)


@app.command("audit")
def audit(
    path: Annotated[Path, typer.Argument(help="Repo or directory to audit.")] = Path("."),
    focus: Annotated[
        str, typer.Option("--focus", help="What to look for.")
    ] = "general code quality, correctness risks, and security",
    out: Annotated[Path | None, typer.Option("--out", help="Write the findings report to this file.")] = None,
    bundle: Annotated[
        Path | None,
        typer.Option("--bundle", help="Write the full run bundle (trace + policy blocks) as JSON."),
    ] = None,
) -> None:
    """Codebase-auditor persona: a read-only agentic audit → findings report.

    The auditor navigates the repo via the PKG + file reads (no writes) and
    reports findings anchored to real file:line. Needs an LLM provider (same
    creds the pipeline uses); the model follows ``resolve_codegen_model``.
    """
    import asyncio

    from orchestrator.agentic import build_run_bundle
    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient
    from orchestrator.personas import render_findings_markdown, run_audit
    from orchestrator.sdlc.codegen import resolve_codegen_model

    load_local_env()
    model = resolve_codegen_model()
    if not model:
        typer.echo("Set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL) to a tool-calling model.", err=True)
        raise typer.Exit(code=2)
    result = asyncio.run(run_audit(path, llm=LiteLLMClient(), model=model, focus=focus))
    report = render_findings_markdown(result, title=f"Audit — {Path(path).resolve().name}")
    if out:
        out.write_text(report, encoding="utf-8")
        typer.echo(f"Wrote {out} ({len(result.findings)} finding(s); {result.stopped_reason}).")
    else:
        typer.echo(report)
    if bundle and result.loop_result is not None:
        run_record = build_run_bundle(
            result.loop_result,
            persona="auditor",
            metadata={"findings": len(result.findings), "unresolved": len(result.unresolved)},
        )
        bundle.write_text(json.dumps(run_record, indent=2), encoding="utf-8")
        typer.echo(f"Wrote run bundle {bundle}.")


@app.command("profile")
def profile(
    path: Annotated[Path, typer.Argument(help="Repo or directory to profile.")] = Path("."),
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent title, to classify the task type.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the profile as JSON.")] = False,
) -> None:
    """Profile a project (languages, framework, DB, tests, task type) — read-only."""
    from orchestrator.catalog import ProjectProfile

    prof = ProjectProfile.from_repo(path, intent_title=intent)
    if as_json:
        _print(prof.to_dict())
        return
    typer.echo(f"languages:      {', '.join(sorted(prof.languages)) or '(none detected)'}")
    typer.echo(f"framework:      {prof.framework or '-'}")
    db = "yes" if prof.has_db else "no"
    migrations = "yes" if prof.has_migrations else "no"
    typer.echo(f"database:       {db} (migrations: {migrations})")
    typer.echo(f"test runner:    {prof.test_runner or '-'}")
    typer.echo(f"task type:      {prof.task_type}")


@app.command("understand")
def understand(
    path: Annotated[Path, typer.Argument(help="Repo or directory to comprehend.")] = Path("."),
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Memory-bank dir (default: <repo>/memory-bank)."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
) -> None:
    """Build a committed `memory-bank/` — a code-true project knowledge base.

    Phase 0: extracts the Product Knowledge Graph + project profile and renders
    architecture / domain-model / tech-context / conventions / glossary as
    markdown in the target repo. Deterministic (no LLM); re-run to refresh.
    """
    from orchestrator.knowledge import build_memory_bank

    result = build_memory_bank(path, out_dir=out, refresh=refresh, log=typer.echo)
    _print(
        {
            "dir": result["dir"],
            "greenfield": result["greenfield"],
            "files": result["files"],
            "grounded_nodes": result["summary"].get("grounded_nodes", 0),
        }
    )


@app.command("state")
def state(
    path: Annotated[Path, typer.Argument(help="Repo or directory to summarize.")] = Path("."),
    lens: Annotated[str, typer.Option("--lens", help="Audience: developer | stakeholder.")] = "developer",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the report to this file (default: print to stdout)."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
) -> None:
    """Current State — a team-facing snapshot of what a repo is today and how healthy it looks.

    Synthesized from the Product Knowledge Graph + project profile (deterministic, no LLM),
    layered on top of `understand`. `--lens developer` gives the technical view;
    `--lens stakeholder` gives plain language. A report is a *view* of the code — re-run to
    refresh; nothing is written unless `--out` is given.
    """
    from orchestrator.knowledge.current_state import build_current_state

    if lens not in ("developer", "stakeholder"):
        typer.echo("ERROR: --lens must be 'developer' or 'stakeholder'.", err=True)
        raise typer.Exit(code=2)
    markdown = build_current_state(path, lens=lens, refresh=refresh)
    if out is not None:
        out.write_text(markdown, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(markdown)


@catalog_app.command("list")
def catalog_list(
    as_json: Annotated[bool, typer.Option("--json", help="Emit the catalog as JSON.")] = False,
) -> None:
    """List the capabilities the orchestrator can assemble (read-only)."""
    from orchestrator.catalog import default_catalog

    caps = default_catalog().all()
    if as_json:
        _print(
            [
                {
                    "id": c.id,
                    "kind": c.kind.value,
                    "summary": c.summary,
                    "applies_to": {
                        "languages": sorted(c.selector.languages) if c.selector.languages else None,
                        "task_types": sorted(c.selector.task_types) if c.selector.task_types else None,
                        "requires_db": c.selector.requires_db,
                    },
                }
                for c in caps
            ]
        )
        return
    for c in caps:
        typer.echo(f"{c.id}  [{c.kind.value}]  — {c.summary}")


@catalog_app.command("plan")
def catalog_plan(
    path: Annotated[Path, typer.Argument(help="Repo or directory to plan for.")] = Path("."),
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent title, to classify the task type.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the plan as JSON.")] = False,
) -> None:
    """Show the capability plan the orchestrator would assemble for a project."""
    from orchestrator.catalog import ProjectProfile, plan_capabilities

    prof = ProjectProfile.from_repo(path, intent_title=intent)
    plan = plan_capabilities(prof)
    if as_json:
        _print(plan.to_dict())
        return
    for line in plan.summary_lines():
        typer.echo(f"  - {line}")
    if plan.workflow_params:
        typer.echo(f"workflow params: {plan.workflow_params}")
    if plan.mcp_servers:
        typer.echo(f"onboard MCP:     {', '.join(plan.mcp_servers)}")


@pkg_app.command("extract")
def pkg_extract(
    path: Annotated[Path, typer.Argument(help="Repo or directory to scan.")] = Path("."),
    query: Annotated[
        str | None, typer.Option("--query", "-q", help="Show callers + blast radius of a symbol name.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Dump all facts as JSON.")] = False,
) -> None:
    """Extract grounded code facts from a repo and print a summary (read-only)."""
    from orchestrator.pkg import FactStore, RepoCodeExtractor

    extractor = RepoCodeExtractor()
    store = FactStore(extractor.extract(path))

    if as_json:
        _print(
            {
                "nodes": [
                    {
                        "id": n.id,
                        "kind": n.kind.value,
                        "name": n.name,
                        "at": str(n.provenance) if n.provenance else None,
                        "external": n.external,
                    }
                    for n in store.nodes
                ],
                "summary": store.summary(),
            }
        )
        return

    summary = store.summary()
    typer.echo(
        f"Scanned {path} — {summary['grounded_nodes']} grounded nodes, "
        f"{summary['external_nodes']} external, {summary['edges']} edges."
    )
    if extractor.skipped:
        typer.echo(f"  (skipped {len(extractor.skipped)} unparseable file(s))")

    if query:
        matches = store.find(query)
        if not matches:
            typer.echo(f"No symbol named '{query}'.")
            return
        for node in matches:
            where = f" @ {node.provenance}" if node.provenance else ""
            typer.echo(f"\n{node.kind.value} {node.id}{where}")
            callers = store.callers_of(node.id)
            typer.echo(f"  called by ({len(callers)}):")
            for cs in callers:
                typer.echo(f"    - {cs.caller.id}  @ {cs.at}")
            touched = store.touches(node.id)
            tail = "…" if len(touched) > 12 else ""
            typer.echo(f"  touches ({len(touched)}): " + ", ".join(t.id for t in touched[:12]) + tail)


@pkg_app.command("export")
def pkg_export(
    path: Annotated[Path, typer.Argument(help="Repo or directory to scan.")] = Path("."),
    db: Annotated[Path, typer.Option("--db", help="SQLite file to write.")] = Path("pkg-facts.db"),
) -> None:
    """Extract facts and export the ontomesh-ready kind-per-table SQLite projection."""
    from orchestrator.pkg import RepoCodeExtractor, export_sqlite

    batch = RepoCodeExtractor().extract(path)
    counts = export_sqlite(batch, db)
    typer.echo(f"Exported {path} → {db}")
    for table, n in counts.items():
        typer.echo(f"  {table:<18} {n}")


@pkg_app.command("docs")
def pkg_docs(
    repo: Annotated[Path, typer.Argument(help="Repo to extract facts from.")] = Path("."),
    docs: Annotated[list[Path], typer.Option("--doc", "-d", help="Markdown/text doc(s) to reconcile.")] = [],  # noqa: B006
) -> None:
    """Reconcile documentation claims against the code's fact graph (read-only)."""
    from orchestrator.pkg import DocPage, DocReconciler, load_or_extract

    if not docs:
        typer.echo("No docs given — pass one or more --doc <file>.")
        raise typer.Exit(code=2)

    batch = load_or_extract(repo)
    pages = [
        DocPage(
            title=str(p),
            text=p.read_text(encoding="utf-8"),
            base_dir=str(p.parent) if p.parent != Path(".") else "",
        )
        for p in docs
    ]
    bindings, drift = DocReconciler(batch, repo_root=repo).reconcile(pages)

    bound = sum(1 for b in bindings if b.bound)
    typer.echo(
        f"{len(bindings)} code-intent mentions · {bound} bound to anchors · {len(drift)} drift finding(s)"
    )
    for f in drift:
        typer.echo(f"  [drift/{f.kind.value}] {f.page_title}: `{f.mention}` — unbound")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
