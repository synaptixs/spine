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

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC
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
openspec_app = typer.Typer(help="Spec-driven development with OpenSpec (openspec.dev).", no_args_is_help=True)
app.add_typer(template_app, name="template")
app.add_typer(contract_app, name="contract")
app.add_typer(task_app, name="task")
app.add_typer(sdlc_app, name="sdlc")
app.add_typer(mcp_app, name="mcp")
app.add_typer(catalog_app, name="catalog")
app.add_typer(openspec_app, name="openspec")
media_app = typer.Typer(
    help=(
        "Media ingestion (G3) — OCR images and transcribe audio/video into graph content. "
        "Explicit and opt-in; local by default, and `--asr api` uploads off-machine only with "
        "`--allow-remote`."
    ),
    no_args_is_help=True,
)
app.add_typer(media_app, name="media")


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


def _mcp_load_configs(path: str | None = None) -> Any:
    """Load the configured MCP servers (seam kept module-level so tests can stub it)."""
    from orchestrator.mcp.config import load_mcp_configs

    return load_mcp_configs(path)


def _mcp_build_registry(configs: Any) -> Any:
    """Build the MCP registry for a set of server configs."""
    from orchestrator.mcp import MCPRegistry

    return MCPRegistry(configs)


async def _mcp_build_tools(registry: Any, **kwargs: Any) -> Any:
    """Discover the ``(contract, handler)`` pairs for every onboarded MCP tool."""
    from orchestrator.mcp import build_mcp_tools

    return await build_mcp_tools(registry, **kwargs)


@contextlib.contextmanager
def _repo_arg(spec: str) -> Iterator[tuple[Path, bool]]:
    """Resolve a repo argument to an on-disk path, yielding ``(path, is_remote)``.

    ``spec`` is a **local path** (used as-is — the CLI is a trusted, single-user
    context) or a **git URL** (``https://``/``ssh://``/``git@host:…`` for
    github/bitbucket/gitlab, or a host in ``ORCHESTRATOR_REPO_ALLOWED_HOSTS``),
    which is shallow-cloned on demand and removed on exit. This mirrors the web
    ``/v1/capabilities/*`` resolution exactly (same SSRF guard + host allow-list),
    so ``understand``/``state``/``pkg``/``profile``/``catalog plan`` reach remote
    repos the way the UI does. ``is_remote`` lets a caller pick a sensible output
    location (a clone's files vanish on exit)."""
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import (
        RepoPathError,
        RepoSourceError,
        materialize_repo_source,
        resolve_repo_source,
    )

    try:
        # repo_allow_any_local: a local CLI path isn't sandboxed to a workspace root.
        source = resolve_repo_source(spec, Settings(repo_allow_any_local=True))
    except (RepoSourceError, RepoPathError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    with materialize_repo_source(source, log=lambda m: typer.echo(m, err=True)) as path:
        yield path, source.kind == "git"


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
            help="Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), "
            "notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md.",
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
    from orchestrator.core.llm.client import LLMError
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.service import SourceUriError, parse_source_uri, spec_to_issue_request

    try:
        parse_source_uri(source)  # validate the source URI early
        service = build_service_for(source, dry_run=not create, rules_path=rules_path)
    except (SourceUriError, IntakeNotConfiguredError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        plan = await analyze_cached(service, source, refresh=refresh, log=lambda m: typer.echo(m, err=True))
    except LLMError as exc:
        # Deriving a spec needs a model. A provider that will not answer is an expected
        # condition, and the message already names the model and the way out.
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
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


@openspec_app.command("draft")
def openspec_draft(
    source: Annotated[
        str,
        typer.Option("--source", help="Unstructured source to bootstrap FROM, e.g. confluence://<id>."),
    ],
    out: Annotated[
        str,
        typer.Option("--out", help="OpenSpec root to write into (changes/<id>/ is created under it)."),
    ] = "openspec",
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-extract from the source (default: reuse the cached backlog)."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing change files (default: never clobber)."),
    ] = False,
) -> None:
    """Bootstrap OpenSpec change proposals FROM an unstructured source (the write-back).

    Runs the LLM intake once (source → intents → specs), then renders each as a
    structured `openspec/changes/<id>/` proposal (proposal.md + specs delta + tasks).
    A human polishes the draft, then implements deterministically:

        orchestrator openspec draft --source confluence://<id> --out ./openspec
        # …review/edit openspec/changes/<id>/…
        orchestrator sdlc feature --source openspec://<id> --safe
    """
    import asyncio

    asyncio.run(_run_openspec_draft(source, out=out, refresh=refresh, overwrite=overwrite))


async def _run_openspec_draft(source: str, *, out: str, refresh: bool, overwrite: bool) -> None:
    from pathlib import Path

    from orchestrator.core.env import load_local_env

    load_local_env()
    from orchestrator.core.llm.client import LLMError
    from orchestrator.intake.cache import analyze_cached
    from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
    from orchestrator.intake.openspec_writer import change_id_for, render_change, write_change
    from orchestrator.intake.service import SourceUriError, parse_source_uri

    try:
        parse_source_uri(source)  # validate early
        service = build_service_for(source, dry_run=True, rules_path=None)
    except (SourceUriError, IntakeNotConfiguredError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        plan = await analyze_cached(service, source, refresh=refresh, log=lambda m: typer.echo(m, err=True))
    except LLMError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    root = Path(out)
    intents_by_id = {i.id: i for i in plan.intents}
    drafted: list[dict[str, object]] = []
    for spec in plan.specs:
        intent = intents_by_id.get(spec.intent_id)
        if intent is None:
            continue
        written = write_change(root, intent, render_change(spec, intent), overwrite=overwrite)
        drafted.append(
            {
                "change_id": change_id_for(intent),
                "source": f"openspec://{change_id_for(intent)}",
                "files": [str(p) for p in written],
                "skipped_existing": not written,
            }
        )
    _print({"root": str(root), "drafted": drafted})
    typer.echo(
        f"\nDrafted {sum(1 for d in drafted if d['files'])} OpenSpec change(s) under {root}/changes/. "
        "Review + polish them, then: orchestrator sdlc feature --source openspec://<change-id> --safe",
        err=True,
    )


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

        intent_key = str(resolved.get("intent_id") or "spec")
        document = await build_plan(
            resolved,
            root=path,
            language=language,
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
    statuses = asyncio.run(registry.probe())
    _print(
        {
            "servers": servers,
            "tools": [
                {
                    "name": t.qualified_name,
                    "read_only": t.read_only,
                    "description": (t.description or "")[:120],
                }
                for s in statuses
                for t in s.tools
            ],
            # Report failures rather than showing an empty list and leaving the operator to
            # guess. A missing extra, an unpulled image and a dead server used to look
            # identical here — all of them "tools": [].
            "unavailable": [
                {"server": s.name, "kind": s.kind, "error": s.error, "remedy": s.remedy}
                for s in statuses
                if not s.ok
            ],
        }
    )
    failed = [s for s in statuses if not s.ok]
    for s in failed:
        typer.echo(f"! {s.name}: {s.error}", err=True)
        if s.remedy:
            typer.echo(f"  → {s.remedy}", err=True)
    # Non-zero when a server the operator configured could not be reached at all, so a
    # scripted `mcp list` fails loudly instead of quietly returning nothing.
    if failed and not any(s.ok for s in statuses):
        raise typer.Exit(code=1)


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
    """Show the ToolContract derived for each onboarded MCP tool (governance view).

    Each input is rendered ``name (type)``, with the type read from the
    server's own JSON Schema at display time (``string|null`` for unions,
    ``any`` when the schema declares no top-level type).
    """
    import asyncio

    from orchestrator.core.env import load_local_env
    from orchestrator.mcp.schema_types import argument_type_label, format_argument

    load_local_env()
    configs = _mcp_load_configs(config)
    registry = _mcp_build_registry(configs)
    built = asyncio.run(_mcp_build_tools(registry, configs=configs))
    rows: list[dict[str, Any]] = []
    for t in built:
        # Types come from the server's raw JSON Schema at display time; the
        # contract's normalised inputs stay the source of truth for *which*
        # arguments exist (and for `mcp call`).
        schema = getattr(getattr(t.handler, "tool", None), "input_schema", None)
        names = [f.name for f in t.contract.spec.inputs]
        labels = {name: argument_type_label(schema, name) for name in names}
        rows.append(
            {
                "contract_id": t.contract.metadata.id,
                "version": t.contract.metadata.version,
                "description": t.contract.metadata.description,
                "side_effects": t.contract.spec.side_effects.value,
                "requires_approval": t.contract.spec.requires_approval.value,
                "write_gated": not t.handler.read_only and not t.handler.write_enabled,
                "inputs": [format_argument(name, labels[name]) for name in names],
                "input_types": labels,
            }
        )
    _print(rows)


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


@app.command("models")
def models_cmd(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Filter to one vendor: anthropic, openai, gemini, …"),
    ] = None,
    tools_only: Annotated[
        bool,
        typer.Option("--tools-only/--all", help="Only models that support tool calling."),
    ] = True,
) -> None:
    """What you can point the pipeline at, and what each stage is using now.

    Read from the installed LiteLLM's own catalog rather than a list maintained here,
    so it reflects the client actually making the calls.

    **Tool calling is a requirement, not a preference.** Codegen forces a
    `submit_files` call and the acceptance judge forces `submit_verdict`; on a model
    without it both fall back to parsing prose out of a text reply, which is exactly
    the failure the forced-tool work removed. `--all` shows the rest, marked.
    """
    from orchestrator.core.llm import catalog

    rows = catalog.catalog(provider)
    if tools_only:
        rows = [m for m in rows if m.usable]
    typer.echo("Current per-stage models:")
    for stage in ("codegen", "judge", "intake"):
        chain = " → ".join(f"${v}" for v in catalog.STAGE_ENV[stage])
        typer.echo(f"  {stage:8} {catalog.resolve(stage):24} ({chain} → built-in default)")
    typer.echo("")
    typer.echo(catalog.render(rows, current=catalog.resolve("codegen")))


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


@app.command("up")
def up(
    port: Annotated[int, typer.Option("--port", help="Port for the web UI + API.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Bind address for the API.")] = "127.0.0.1",
    no_docker: Annotated[
        bool,
        typer.Option("--no-docker", help="Don't manage Docker; assume Postgres + Temporal are already up."),
    ] = False,
    no_worker: Annotated[
        bool, typer.Option("--no-worker", help="Skip the Temporal worker (browse-only; can't delegate runs).")
    ] = False,
    compose_file: Annotated[
        Path | None, typer.Option("--compose-file", help="Override the docker compose file to use.")
    ] = None,
) -> None:
    """Bring up the whole local stack in one command, then open the inbox.

    Starts Docker infra (Postgres + Temporal), applies migrations, and launches
    the web/API server **and** the Temporal worker with sensible defaults — so a
    non-technical user reaches the delegation inbox at ``/app`` without wiring up
    three terminals. Streams logs until Ctrl-C, then stops the app processes
    (infra containers are left running for fast restarts).
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.launch import LaunchConfig, LaunchError, run_up

    load_local_env()

    config = LaunchConfig(
        host=host,
        port=port,
        use_docker=not no_docker,
        start_worker=not no_worker,
        compose_file=compose_file,
        api_key=os.getenv("ORCHESTRATOR_API_KEY", "dev-key"),
        session_secret=os.getenv("ORCHESTRATOR_SESSION_SECRET", "dev-session-secret"),
    )
    try:
        code = run_up(config, echo=typer.echo)
    except LaunchError as exc:
        typer.echo(f"\n✗ {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


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
    path: Annotated[str, typer.Argument(help="Repo path or git URL to profile.")] = ".",
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent title, to classify the task type.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the profile as JSON.")] = False,
) -> None:
    """Profile a project (languages, framework, DB, tests, task type) — read-only.

    ``path`` is a local path or a git URL (github/bitbucket/gitlab/enterprise),
    cloned on demand.
    """
    from orchestrator.catalog import ProjectProfile

    with _repo_arg(path) as (repo, _):
        prof = ProjectProfile.from_repo(repo, intent_title=intent)
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
    path: Annotated[str, typer.Argument(help="Repo path or git URL to comprehend.")] = ".",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Knowledge-base dir (default: <repo>/episteme; ./episteme for a URL)."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Verify the committed episteme still matches the code; write nothing, exit non-zero if not.",
        ),
    ] = False,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect (postgres|mysql|tsql|oracle|…); default: auto-detect."),
    ] = None,
    intents: Annotated[
        bool,
        typer.Option(
            "--intents",
            help="Also record which ticket each symbol was last changed for (Intent/SERVES). "
            "Adds ~8s: one `git blame` per file. Opt-in — nothing renders these facts yet.",
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the result — including every file written — as JSON.")
    ] = False,
) -> None:
    """Build a committed `episteme/` — a code-true project knowledge base.

    Phase 0: extracts the Product Knowledge Graph + project profile and renders
    architecture / domain-model / tech-context / conventions / glossary as
    markdown in the target repo. Deterministic (no LLM); re-run to refresh.
    ``path`` may be a local path or a git URL cloned on demand — for a URL the
    clone is transient, so the knowledge base defaults to ``./episteme``.

    ``--check`` writes nothing: it re-renders and diffs against the committed
    bank, exiting non-zero when they disagree. That makes episteme *provably*
    current in CI rather than hopefully current.
    """
    from orchestrator.knowledge import build_memory_bank, check_memory_bank
    from orchestrator.knowledge.understand import BANK_DIRNAME, memory_bank_dir

    if check:
        with _repo_arg(path) as (repo, _):
            report = check_memory_bank(
                repo, out_dir=out, sql_dialect=dialect, intents=intents, log=typer.echo
            )
        typer.echo(report.summary_line())
        for label, names in (
            ("out of date", report.stale),
            ("missing", report.missing),
            ("describes code that's gone", report.orphaned),
        ):
            for name in names:
                typer.echo(f"  {label}: {name}")
        if not report.ok:
            if not report.absent:  # the absent message already says what to run
                typer.echo("Regenerate with `orchestrator understand .` and commit the result.")
            raise typer.Exit(code=1)
        return

    with _repo_arg(path) as (repo, is_remote):
        out_dir = out or (Path(BANK_DIRNAME) if is_remote else memory_bank_dir(repo))
        result = build_memory_bank(
            repo, out_dir=out_dir, refresh=refresh, sql_dialect=dialect, intents=intents, log=typer.echo
        )
    if as_json:
        _print(
            {
                "dir": result["dir"],
                "greenfield": result["greenfield"],
                "files": result["files"],
                "grounded_nodes": result["summary"].get("grounded_nodes", 0),
            }
        )
        return
    for line in _understand_summary(result):
        typer.echo(line)


def _understand_summary(result: dict[str, Any]) -> list[str]:
    """What the bank says, and which file to open first.

    This used to print the raw result — a JSON array of all 62 filenames — so the first thing
    anyone saw on their first run was a directory listing. The facts were in the files; the
    terminal showed plumbing. The listing is still available under ``--json``, where a caller
    that wants to parse it can ask for it.
    """
    summary = result.get("summary") or {}
    profile = result.get("profile") or {}
    nodes, grounded = summary.get("nodes", 0), summary.get("grounded_nodes", 0)
    calls, imports = summary.get("edges_calls", 0), summary.get("edges_imports", 0)
    docs = summary.get("edges_mentions", 0)

    # `profile["languages"]` is a list, and interpolating it printed `['python']` at a stranger
    # on their first run. The build's own `[understand]` lines already reported the node count
    # and the directory, so this says what those did not rather than repeating them.
    raw = profile.get("languages") or profile.get("language") or []
    languages = ", ".join(str(x) for x in raw) if isinstance(raw, list | tuple) else str(raw)

    out: list[str] = []
    head = f"{grounded:,} grounded of {nodes:,} nodes"
    out.append(f"{languages} · {head}" if languages else head)
    out.append(f"{calls:,} call edges · {imports:,} imports · {docs:,} doc mentions")
    # Three named files rather than "62 written": a reader needs one place to start, not an index.
    out.append("")
    out.append("Start here:")
    out.append("  README.md          what this codebase is")
    out.append("  architecture.md    how it fits together")
    out.append("  symbol-index.md    every symbol, with file:line")
    out.append("")
    out.append(f"{len(result.get('files') or [])} files in total — `--json` lists them all.")
    return out


@app.command("state")
def state(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to summarize.")] = ".",
    lens: Annotated[str, typer.Option("--lens", help="Audience: developer | stakeholder.")] = "developer",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the report to this file (default: print to stdout)."),
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect (postgres|mysql|tsql|oracle|…); default: auto-detect."),
    ] = None,
    no_timestamp: Annotated[
        bool,
        typer.Option("--no-timestamp", help="Omit the generated-at time (byte-stable HTML for CI diffs)."),
    ] = False,
    intents: Annotated[
        bool,
        typer.Option(
            "--intents",
            help="Also record which ticket each symbol was last changed for (Intent/SERVES). "
            "Adds ~8s: one `git blame` per file. Opt-in — nothing renders these facts yet.",
        ),
    ] = False,
) -> None:
    """Current State — a team-facing snapshot of what a repo is today and how healthy it looks.

    Synthesized from the Product Knowledge Graph + project profile (deterministic, no LLM),
    layered on top of `understand`. `--lens developer` gives the technical view;
    `--lens stakeholder` gives plain language. A report is a *view* of the code — re-run to
    refresh; nothing is written unless `--out` is given.

    Output format follows `--out`'s extension: `--out report.html` emits a single
    self-contained, shareable HTML report; any other extension (or stdout) emits markdown.
    """
    if lens not in ("developer", "stakeholder"):
        typer.echo("ERROR: --lens must be 'developer' or 'stakeholder'.", err=True)
        raise typer.Exit(code=2)

    want_html = out is not None and out.suffix.lower() in (".html", ".htm")
    with _repo_arg(path) as (repo, _):
        if want_html:
            content = _render_state_html(
                repo, lens=lens, refresh=refresh, dialect=dialect, no_timestamp=no_timestamp, intents=intents
            )
        else:
            from orchestrator.knowledge.current_state import build_current_state

            content = build_current_state(
                repo, lens=lens, refresh=refresh, sql_dialect=dialect, intents=intents
            )
    if out is not None:
        out.write_text(content, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(content)


def _render_state_html(
    repo: Path,
    *,
    lens: str,
    refresh: bool,
    dialect: str | None,
    no_timestamp: bool,
    intents: bool = False,
) -> str:
    """Build a `CurrentState` and render the self-contained shareable HTML report."""
    from datetime import datetime

    from orchestrator.knowledge.current_state import load_current_state
    from orchestrator.knowledge.report_html import render_report_html
    from orchestrator.pkg.persistence import repo_state
    from orchestrator.pkg.store import FactStore

    state, batch = load_current_state(repo, refresh=refresh, sql_dialect=dialect, intents=intents)
    sha, _dirty = repo_state(repo)
    grounded = sum(1 for n in batch.nodes if n.grounded)
    timestamp = None if no_timestamp else datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return render_report_html(
        state,
        repo_name=repo.resolve().name or "repository",
        sha=sha,
        timestamp=timestamp,
        lens=lens,
        grounded=grounded,
        edges=len(batch.edges),
        store=FactStore(batch),
    )


@app.command("design")
def design(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to design against.")] = ".",
    title: Annotated[str, typer.Option("--title", "-t", help="Feature title (the thing to build).")] = "",
    summary: Annotated[str, typer.Option("--summary", "-s", help="One-line feature summary.")] = "",
    criterion: Annotated[
        list[str] | None,
        typer.Option("--criterion", "-c", help="Acceptance criterion (repeatable)."),
    ] = None,
    spec_file: Annotated[
        Path | None,
        typer.Option(
            "--spec", help="Read the spec from JSON ({title,summary,acceptance_criteria}) or a .md file."
        ),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write design.md here (default: print to stdout).")
    ] = None,
    llm: Annotated[
        bool, typer.Option("--llm", help="Let an LLM write the design (needs a provider; else heuristic).")
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Grounded feature design: spec × knowledge graph → a design with blast radius.

    Produces the M2 design for one feature anchored to the repo's real structure,
    and annotates it with its **blast radius** (which modules it touches, who
    depends on them, the call hotspots) and any **unverified references** (named
    paths absent from the graph). Deterministic by default; `--llm` writes the
    prose. `path` may be a local path or a git URL cloned on demand.
    """
    import asyncio

    from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc.design import produce_design, render_design_md

    spec = _load_design_spec(spec_file, title, summary, list(criterion or []))
    if not spec.get("title"):
        typer.echo("ERROR: provide --title (or --spec with a title).", err=True)
        raise typer.Exit(code=2)

    client: Any = None
    if llm:
        from orchestrator.core.env import load_local_env
        from orchestrator.core.llm import LiteLLMClient
        from orchestrator.sdlc.codegen import resolve_codegen_model

        load_local_env()
        if not resolve_codegen_model():
            typer.echo("Set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL) for --llm.", err=True)
            raise typer.Exit(code=2)
        client = LiteLLMClient()

    with _repo_arg(path) as (repo, _):
        extractor = RepoCodeExtractor(sql_dialect=dialect)
        batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
        store = FactStore(batch)
        overview = build_overview(batch)
        memory_bank = _read_design_bank(repo)
        design_dict = asyncio.run(
            produce_design(spec, overview=overview, memory_bank=memory_bank, store=store, llm=client)
        )

    md = render_design_md(spec, design_dict)
    if out is not None:
        out.write_text(md, encoding="utf-8")
        unver = (design_dict.get("blast_radius") or {}).get("unverified_references") or []
        note = f"; {len(unver)} unverified reference(s)" if unver else ""
        typer.echo(f"wrote {out}{note}")
    else:
        typer.echo(md)


def _load_design_spec(
    spec_file: Path | None, title: str, summary: str, criteria: list[str]
) -> dict[str, Any]:
    """Build the design spec from a file or the inline flags."""
    if spec_file is not None:
        text = spec_file.read_text(encoding="utf-8")
        if spec_file.suffix.lower() == ".json":
            data = json.loads(text)
            if isinstance(data, dict):
                return data
            raise typer.BadParameter("--spec JSON must be an object")
        # Markdown: first heading/line is the title, the rest the summary.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        head = lines[0].lstrip("# ").strip() if lines else ""
        return {"title": head, "summary": "\n".join(lines[1:]), "acceptance_criteria": criteria}
    return {"title": title, "summary": summary, "acceptance_criteria": criteria}


def _read_design_bank(repo: Path) -> dict[str, str]:
    """Optional conventions/domain context from a committed `episteme/`, if present."""
    from orchestrator.knowledge.understand import existing_bank_dir

    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        bank = existing_bank_dir(repo)
        for name in ("domain-model.md", "tech-context.md", "conventions.md"):
            p = bank / name
            if p.exists():
                out[name] = p.read_text(encoding="utf-8")
    return out


@app.command("investigate")
def investigate(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to research against.")] = ".",
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help="Fetch the ticket from a source, e.g. jira://PROJ-123, confluence://<id>, file://./bug.md.",
        ),
    ] = None,
    title: Annotated[
        str, typer.Option("--title", "-t", help="Inline ticket title (instead of --source).")
    ] = "",
    text: Annotated[str, typer.Option("--text", help="Inline ticket body (with --title).")] = "",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the brief here (default: print to stdout).")
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Investigation brief: a ticket × the codebase, before you design.

    Researches where a ticket lands in the code (knowledge-graph retrieval, with
    `file:line` + caller counts), the relevant committed `episteme/` knowledge,
    and — when a registry DB is configured — prior-run notes. Deterministic, no
    LLM. Pass the ticket via `--source` (e.g. `jira://PROJ-123`) or inline with
    `--title`/`--text`. Feed the result into `orchestrator design`.
    """
    from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
    from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

    ticket_title, problem = _load_ticket(source, title, text)
    if not ticket_title and not problem:
        typer.echo("ERROR: provide --source or --title (the ticket to investigate).", err=True)
        raise typer.Exit(code=2)

    with _repo_arg(path) as (repo, _):
        extractor = RepoCodeExtractor(sql_dialect=dialect)
        batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
        inv = build_investigation(ticket_title, problem, store=FactStore(batch), root=repo)

    md = render_investigation_md(inv)
    if out is not None:
        out.write_text(md, encoding="utf-8")
        typer.echo(f"wrote {out} ({len(inv.landing)} code landing(s); {len(inv.areas)} area(s)).")
    else:
        typer.echo(md)


def _load_ticket(source: str | None, title: str, text: str) -> tuple[str, str]:
    """Resolve the ticket to investigate: a source URI's documents, or inline flags."""
    if source:
        import asyncio

        from orchestrator.intake.factory import IntakeNotConfiguredError, build_service_for
        from orchestrator.intake.service import SourceUriError, parse_source_uri

        try:
            _, root_id = parse_source_uri(source)
            service = build_service_for(source, dry_run=True)
            tree = asyncio.run(service.fetch_source_documents(root_id))
        except (SourceUriError, IntakeNotConfiguredError) as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        docs = tree.documents
        if not docs:
            typer.echo(f"ERROR: no documents found at {source}", err=True)
            raise typer.Exit(code=1)
        resolved_title = docs[0].title or source
        body = "\n\n".join(f"## {d.title}\n{d.body}".strip() for d in docs)
        return resolved_title, body
    return title, text


@app.command("localize")
def localize(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to resolve the trace against.")] = ".",
    trace: Annotated[
        Path | None, typer.Option("--trace", help="File with the stack trace / failing-test output.")
    ] = None,
    text: Annotated[str, typer.Option("--text", help="Inline trace text (instead of --trace).")] = "",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the report here (default: print to stdout).")
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Fault localization: a stack trace → the repo symbols it names.

    Parses a Python traceback / pytest failure, resolves each frame to a
    knowledge-graph symbol (`file:line`), and points at the likely fault site
    plus who calls it. Reads the trace from `--trace <file>`, `--text`, or stdin.
    Deterministic, no LLM — the first step of a root-cause investigation.
    """
    import sys

    from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
    from orchestrator.sdlc.localize import localize_trace, render_localization_md

    trace_text = text or (trace.read_text(encoding="utf-8") if trace else "")
    if not trace_text and not sys.stdin.isatty():
        trace_text = sys.stdin.read()
    if not trace_text.strip():
        typer.echo("ERROR: provide a trace via --trace <file>, --text, or stdin.", err=True)
        raise typer.Exit(code=2)

    with _repo_arg(path) as (repo, _):
        extractor = RepoCodeExtractor(sql_dialect=dialect)
        batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
        loc = localize_trace(trace_text, store=FactStore(batch))

    md = render_localization_md(loc)
    if out is not None:
        out.write_text(md, encoding="utf-8")
        site = loc.fault.where if loc.fault else "unresolved"
        typer.echo(f"wrote {out} (fault site: {site}; {len(loc.frames)} frame(s)).")
    else:
        typer.echo(md)


@app.command("rca")
def rca(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to analyze against.")] = ".",
    source: Annotated[
        str | None,
        typer.Option("--source", help="Fetch the bug from a source, e.g. jira://PROJ-42 (a Bug ticket)."),
    ] = None,
    trace: Annotated[
        Path | None, typer.Option("--trace", help="File with a stack trace / failing-test output.")
    ] = None,
    text: Annotated[
        str, typer.Option("--text", help="Inline bug text / trace (instead of --trace/--source).")
    ] = "",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write rca.md here (default: print to stdout).")
    ] = None,
    llm: Annotated[
        bool,
        typer.Option(
            "--llm", help="Let an LLM enrich the hypotheses (needs a provider; else deterministic)."
        ),
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Root-cause analysis: a bug → grounded RCA + fix approach (no code changed).

    Localizes the bug (a stack trace, a `jira://` Bug, or inline text) against
    the knowledge graph, then reports the fault site, ranked root-cause
    *hypotheses* with evidence (callers, recent churn, the exception), the
    regression surface a fix must cover, and a scoped fix approach. Deterministic
    by default; `--llm` enriches the hypotheses. It stops at the report — a human
    decides whether to build the fix.
    """
    import asyncio

    from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
    from orchestrator.sdlc.rca import build_rca, render_rca_md

    problem = _load_bug_text(source, trace, text)
    if not problem.strip():
        typer.echo("ERROR: provide the bug via --source, --trace <file>, --text, or stdin.", err=True)
        raise typer.Exit(code=2)

    client: Any = None
    if llm:
        from orchestrator.core.env import load_local_env
        from orchestrator.core.llm import LiteLLMClient
        from orchestrator.sdlc.codegen import resolve_codegen_model

        load_local_env()
        if not resolve_codegen_model():
            typer.echo("Set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL) for --llm.", err=True)
            raise typer.Exit(code=2)
        client = LiteLLMClient()

    with _repo_arg(path) as (repo, _):
        extractor = RepoCodeExtractor(sql_dialect=dialect)
        batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
        report = asyncio.run(build_rca(problem, store=FactStore(batch), root=repo, llm=client))

    md = render_rca_md(report)
    if out is not None:
        out.write_text(md, encoding="utf-8")
        site = report.fault_site or "unresolved"
        typer.echo(f"wrote {out} (fault site: {site}; {len(report.hypotheses)} hypothesis(es)).")
    else:
        typer.echo(md)


def _load_bug_text(source: str | None, trace: Path | None, text: str) -> str:
    """Resolve the bug text: a source ticket, a trace file, --text, or stdin."""
    if source:
        title, body = _load_ticket(source, "", "")
        return f"{title}\n\n{body}".strip()
    if trace is not None:
        return trace.read_text(encoding="utf-8")
    if text:
        return text
    import sys

    return sys.stdin.read() if not sys.stdin.isatty() else ""


@app.command("regression")
def regression(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to analyze.")] = ".",
    symbol: Annotated[
        str, typer.Option("--symbol", "-s", help="The symbol you're about to change (by name).")
    ] = "",
    trace: Annotated[
        Path | None,
        typer.Option("--trace", help="A stack trace instead — the fault site becomes the target."),
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the plan here (default: print to stdout).")
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Re-extract the PKG instead of using the commit cache.")
    ] = False,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Regression coverage: what a change should re-test, from the call graph.

    For a symbol you're about to change (`--symbol`) or a fault site (`--trace`),
    computes the blast radius and splits it into tests that already exercise it
    and production code in the radius with no covering test — the regression
    gaps. Deterministic, no LLM. Needs a call graph (Python/C/C++/C#/Java/TS).
    """
    from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
    from orchestrator.sdlc.coverage import build_regression_plan, render_regression_plan_md, resolve_target

    if not symbol and trace is None:
        typer.echo("ERROR: provide --symbol <name> or --trace <file>.", err=True)
        raise typer.Exit(code=2)

    with _repo_arg(path) as (repo, _):
        extractor = RepoCodeExtractor(sql_dialect=dialect)
        batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
        store = FactStore(batch)

        if trace is not None:
            from orchestrator.sdlc.localize import localize_trace

            loc = localize_trace(trace.read_text(encoding="utf-8"), store=store)
            target_id = loc.fault.node_id if loc.fault else None
            if not target_id:
                typer.echo("ERROR: no fault site in the trace resolved to a repo symbol.", err=True)
                raise typer.Exit(code=1)
        else:
            target_id = resolve_target(store, symbol)
            if target_id is None:
                typer.echo(f"ERROR: symbol {symbol!r} not found in the knowledge graph.", err=True)
                raise typer.Exit(code=1)

        plan = build_regression_plan(store, target_id)

    md = render_regression_plan_md(plan)
    if out is not None:
        out.write_text(md, encoding="utf-8")
        gaps = sum(1 for i in plan.impacted if not i.covered)
        typer.echo(f"wrote {out} ({gaps} regression gap(s); {len(plan.impacted)} impacted symbol(s)).")
    else:
        typer.echo(md)


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
    path: Annotated[str, typer.Argument(help="Repo path or git URL to plan for.")] = ".",
    intent: Annotated[
        str | None, typer.Option("--intent", help="Intent title, to classify the task type.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the plan as JSON.")] = False,
) -> None:
    """Show the capability plan the orchestrator would assemble for a project."""
    from orchestrator.catalog import ProjectProfile, plan_capabilities

    with _repo_arg(path) as (repo, _):
        prof = ProjectProfile.from_repo(repo, intent_title=intent)
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
    path: Annotated[str, typer.Argument(help="Repo path or git URL to scan.")] = ".",
    query: Annotated[
        str | None, typer.Option("--query", "-q", help="Show callers + blast radius of a symbol name.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Dump all facts as JSON.")] = False,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect (postgres|mysql|tsql|oracle|…); default: auto-detect."),
    ] = None,
    repos: Annotated[
        str | None,
        typer.Option("--repos", help="A `.spine/repos.yaml` — extract every declared repo into one graph."),
    ] = None,
) -> None:
    """Extract grounded code facts from a repo and print a summary (read-only)."""
    from orchestrator.pkg import FactStore, RepoCodeExtractor

    extractor = RepoCodeExtractor(sql_dialect=dialect)
    if repos:
        store, merged = _extract_repos(repos, dialect)
        path = repos
    else:
        merged = None
        with _repo_arg(path) as (repo, _):
            store = FactStore(extractor.extract(repo))

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
    scanned = f"{len(merged.repos)} repos" if merged is not None else path
    typer.echo(
        f"Scanned {scanned} — {summary['grounded_nodes']} grounded nodes, "
        f"{summary['external_nodes']} external, {summary['edges']} edges."
    )
    if merged is not None:
        for state in merged.repos:
            mark = "cached" if state.cached else "extracted"
            trust = "" if state.trusted else "  ** UNTRUSTED **"
            typer.echo(f"  {state.key:<16} {mark:<9} {(state.sha or '-')[:12]}{trust}")
    # Per kind, because one total cannot show a kind that stopped being emitted. Zeros are
    # printed rather than skipped: `REFERENCES 0` on a repo with entities is the line worth
    # reading, and omitting it looks like a question nobody asked.
    per_kind = {k[len("edges_") :]: v for k, v in summary.items() if k.startswith("edges_")}
    if per_kind:
        typer.echo("  " + "  ".join(f"{k.upper()} {v}" for k, v in per_kind.items()))
    if extractor.skipped:
        typer.echo(f"  (skipped {len(extractor.skipped)} unparseable file(s))")

    if merged is not None and not merged.trusted:
        # Last, and loud. A merged graph looks identical either way, and one describing
        # uncommitted work cannot back a currency gate or be reproduced at a commit — so the
        # thing that must not be missed goes where the eye stops, not above the counts.
        typer.echo(
            f"\n  NOT REPRODUCIBLE — {', '.join(merged.untrusted_keys)} "
            "has uncommitted work or is not a git repo.\n"
            "  The counts above are real, but this graph cannot be re-derived at a commit."
        )

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


def _extract_repos(config: str, dialect: str | None) -> tuple[Any, Any]:
    """`--repos`: every declared repository, merged into one scoped graph."""
    from orchestrator.pkg import FactStore, RepoCodeExtractor
    from orchestrator.pkg.persistence import load_or_extract_repos
    from orchestrator.pkg.repos import RepoConfigError, load_repo_config

    try:
        repo_set = load_repo_config(config)
    except RepoConfigError as exc:
        typer.echo(f"pkg extract: {exc}")
        raise typer.Exit(code=1) from exc
    merged = load_or_extract_repos(repo_set, extractor=RepoCodeExtractor(sql_dialect=dialect))
    return FactStore(merged.batch), merged


@pkg_app.command("capabilities")
def pkg_capabilities(
    fmt: Annotated[
        str,
        typer.Option("--format", help="markdown (the KNOWLEDGE_GRAPH.md matrix) | json."),
    ] = "markdown",
) -> None:
    """Which node/edge kinds each language front-end can emit (read-only, no repo needed).

    Read off the front-ends' own source, so it cannot drift from them. This is
    capability — what Spine *would* see — not coverage: a front-end that emits
    `Endpoint` still finds none in a repo without routes. For that question, run
    `pkg verify` and read the `source-parity` check.
    """
    import json as _json

    from orchestrator.pkg.capabilities import front_end_capabilities, render_markdown

    caps = front_end_capabilities()
    if fmt == "json":
        typer.echo(
            _json.dumps(
                [
                    {
                        "language": c.language,
                        "node_kinds": list(c.node_kinds),
                        "edge_kinds": list(c.edge_kinds),
                    }
                    for c in caps
                ],
                indent=2,
            )
        )
        return
    if fmt != "markdown":
        typer.echo(f"Unknown --format {fmt!r}. Use markdown or json.", err=True)
        raise typer.Exit(code=2)
    typer.echo(render_markdown(caps))


@pkg_app.command("verify")
def pkg_verify(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to scan.")] = ".",
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect (postgres|mysql|tsql|oracle|…); default: auto-detect."),
    ] = None,
) -> None:
    """Check Tier-1 graph invariants (dangling edges, provenance, unjoined imports).

    Self-consistency checks that need no ground truth: every edge endpoint
    exists, every grounded provenance resolves, and first-party imports
    actually join (orphan rate / external ratio per language). Exits non-zero
    on any error, so it can stand guard in CI.
    """
    from orchestrator.pkg import RepoCodeExtractor
    from orchestrator.pkg.verify import verify_batch

    extractor = RepoCodeExtractor(sql_dialect=dialect)
    with _repo_arg(path) as (repo, _):
        report = verify_batch(extractor.extract(repo), repo)

    if as_json:
        _print(
            {
                "ok": report.ok,
                "issues": [
                    {"check": i.check, "severity": i.severity, "message": i.message} for i in report.issues
                ],
            }
        )
    else:
        for issue in report.issues:
            typer.echo(f"[{issue.severity}] {issue.check}: {issue.message}")
        typer.echo(
            f"pkg verify: {'OK' if report.ok else 'FAILED'} — "
            f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)."
        )
    if not report.ok:
        raise typer.Exit(code=1)


def _pct(value: float | None) -> str:
    """A score, or an em dash — never a 1.0 standing in for 'nothing was expected'."""
    return "—   " if value is None else f"{value:.2f}"


def _runtime_oracle(repo: str, tests: str | None, as_json: bool) -> None:
    """`--oracle runtime`: trace the repo's own test suite and score CALLS recall."""
    from orchestrator.pkg.runtime_oracle import OracleError, score_runtime

    try:
        report = score_runtime(repo, targets=tests.split() if tests else None)
    except OracleError as exc:
        typer.echo(f"pkg accuracy: {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        _print(
            {
                "oracle": "runtime",
                "observed": report.observed,
                "matched": report.matched,
                "unmapped": report.unmapped,
                "calls_recall_lower_bound": report.recall,
                "coverage_pct": report.coverage_pct,
                "precision": None,
                "precision_note": "not measurable from a trace",
                "missing": list(report.missing),
                "unmapped_examples": list(report.unmapped_examples),
                "dropped": report.dropped,
                "pytest_exit": report.pytest_exit,
                "command": report.command,
            }
        )
        return

    typer.echo(f"traced: {report.command}")
    typer.echo(f"\nCALLS recall (runtime oracle) — {_pct(report.recall)} lower bound")
    typer.echo(f"  observed  {report.observed} first-party call pair(s) whose ends are both graph nodes")
    typer.echo(f"  matched   {report.matched} have a CALLS edge · {report.observed - report.matched} do not")
    typer.echo(f"  unmapped  {report.unmapped} observed pair(s) had no node for one end")
    cov = "unavailable" if report.coverage_pct is None else f"{report.coverage_pct:.1f}% of statements"
    typer.echo(f"  coverage  these tests reach {cov} — the number is bounded by this")
    if report.dropped:
        typer.echo(f"  filtered  {report.dropped} (never the graph's job)")
    for item in report.missing:
        typer.echo(f"    missing: {item}")
    for item in report.unmapped_examples:
        typer.echo(f"    unmapped: {item}")
    typer.echo(
        "\n  A LOWER BOUND: it counts only what these tests executed.\n"
        "  PRECISION IS NOT MEASURABLE from a trace — an edge the tests never exercised is\n"
        "  untested, not wrong. Use the corpus oracle for precision.\n"
        "  Not deterministic: never write this into episteme/."
    )
    if report.pytest_exit != 0:
        typer.echo(f"\n  NOTE: the test suite exited {report.pytest_exit}; recall is over what still ran.")


def _parity_oracle(repo: str, as_json: bool) -> None:
    """`--oracle parity`: what the source declares against what the graph holds, per file."""
    from orchestrator.pkg.accuracy import CorpusError, score_parity

    try:
        report = score_parity(repo)
    except CorpusError as exc:
        typer.echo(f"pkg accuracy: {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        _print(
            {
                "oracle": "parity",
                "declared": report.declared,
                "in_graph": report.in_graph,
                "shortfall": report.shortfall,
                "surplus": report.surplus,
                "files": [
                    {
                        "file": c.file,
                        "line": c.first_line,
                        "language": c.language,
                        "kind": c.kind.value,
                        "declared": c.declared,
                        "in_graph": c.in_graph,
                        "approximate": c.approximate,
                    }
                    for c in report.counts
                ],
            }
        )
        return

    typer.echo(f"\nper-construct parity — {report.declared} declared, {report.in_graph} in graph")
    typer.echo(f"  shortfall {report.shortfall} — declared in source, absent from the graph")
    typer.echo(f"  surplus   {report.surplus} — expected where a router is mounted more than once")
    for c in report.short_files:
        where = f"{c.file}:{c.first_line}" if c.first_line else c.file
        hedge = "  (approximate)" if c.approximate else ""
        typer.echo(
            f"    short: {where} declares {c.declared} {c.kind.value}, graph holds {c.in_graph}{hedge}"
        )
    typer.echo(
        "\n  Needs no corpus and no test run — only the source.\n"
        "  Shortfall and surplus are NOT averaged into one ratio: a doubly-mounted router\n"
        "  legitimately yields more nodes than decorators, so a combined figure hides both."
    )


def _invention_oracle(repo: str, sample: int, kind: str, as_json: bool) -> None:
    """`--oracle invention`: CALLS edges targeting a name bound in the caller's own scope."""
    from orchestrator.pkg.facts import EdgeKind
    from orchestrator.pkg.invention import sample_edges, score_invention

    try:
        report = score_invention(repo)
    except ValueError as exc:
        typer.echo(f"pkg accuracy: {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        _print(
            {
                "oracle": "invention",
                "invented": len(report.invented),
                "rate": report.rate,
                "total_calls": report.total_calls,
                "external_calls": report.external_calls,
                "candidates": report.candidates,
                "unexamined": report.unexamined,
                "languages": [
                    {
                        "language": entry.language,
                        "status": entry.status,
                        "reason": entry.reason,
                        "invented": len(entry.invented),
                        "total_calls": entry.total_calls,
                        "examined": entry.examined,
                        "shadowable": entry.shadowable,
                        "unexamined": entry.unexamined,
                    }
                    for entry in report.by_language
                ],
                "examples": list(report.examples),
            }
        )
        return

    rate = "—" if report.rate is None else f"{report.rate:.2%}"
    typer.echo(f"\ninvented CALLS edges — {len(report.invented)} ({rate} of all calls)")
    typer.echo(f"  {report.total_calls} CALLS, {report.external_calls} to external targets")
    typer.echo(f"  {report.candidates} candidate(s) examined, {report.unexamined} unexaminable")

    if report.by_language:
        typer.echo("\n  per front-end — a count only means 'clean' where status is measured:")
        for entry in report.by_language:
            typer.echo(
                f"    {entry.language:<12} {entry.status:<14} "
                f"{len(entry.invented):>5} invented / {entry.shadowable:>6} bare calls"
                f"  (of {entry.total_calls} CALLS)"
            )
            if entry.reason:
                typer.echo(f"      {entry.reason}")

    for line in report.examples:
        typer.echo(f"    {line}")
    typer.echo(
        "\n  Each of these asserts a call that the source does not make.\n"
        "  Exactly detected, not sampled: a name bound inside the caller cannot be one."
    )
    if report.unmeasured_languages:
        typer.echo(
            "  NOT MEASURED here: "
            + ", ".join(report.unmeasured_languages)
            + " — these carry CALLS edges no walker examined."
        )

    if sample:
        try:
            edge_kind = EdgeKind(kind)
        except ValueError:
            typer.echo(f"pkg accuracy: unknown edge kind {kind!r}")
            raise typer.Exit(code=1) from None
        from orchestrator.pkg import RepoCodeExtractor

        batch = RepoCodeExtractor().extract(Path(repo))
        typer.echo(f"\n{sample} sampled {kind} edge(s) for review — deterministic for this commit:")
        for line in sample_edges(batch, edge_kind, sample):
            typer.echo(f"    {line}")
        typer.echo(
            "\n  No detector reaches these: CONSUMES matches on (verb, path), EXPOSES composes\n"
            "  mount prefixes, REFERENCES guesses a class name. Only a person reading the\n"
            "  source can say whether each is real."
        )


# Inside the package, not at the repo root: the wheel ships `src/orchestrator` only, and
# the build document quotes this number at generation time on installed Spines too.
SCOREBOARD_FILE = "src/orchestrator/pkg/scoreboard.json"


def _scoreboard(repo: str, write: bool, as_json: bool) -> None:
    """`--scoreboard` writes the committed baseline; `--check` compares against it."""
    import json as _json

    from orchestrator.pkg.accuracy import build_scoreboard, compare_scoreboard, scoreboard_improvements

    root = Path(repo)
    path = root / SCOREBOARD_FILE
    current = build_scoreboard(root / "corpus", root)
    rendered = _json.dumps(current, indent=2, sort_keys=True) + "\n"

    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        typer.echo(f"wrote {path}")
        return

    if not path.is_file():
        typer.echo(f"pkg accuracy: no baseline at {path} — run `pkg accuracy --scoreboard` first")
        raise typer.Exit(code=1)
    baseline = _json.loads(path.read_text(encoding="utf-8"))

    regressions = compare_scoreboard(baseline, current)
    improvements = scoreboard_improvements(baseline, current)

    if as_json:
        _print(
            {
                "ok": not regressions,
                "regressions": [
                    {"metric": r.metric, "detail": r.detail, "was": r.was, "now": r.now} for r in regressions
                ],
                "improvements": improvements,
            }
        )
    else:
        for r in regressions:
            typer.echo(f"[REGRESSION] {r}")
        for i in improvements:
            typer.echo(f"[improved]   {i}")

        # Ungated metrics move on ordinary commits, so they are reported and never fail.
        was_inv = baseline.get("metrics", {}).get("invention", {}).get("count")
        now_inv = current["metrics"]["invention"]["count"]
        if was_inv is not None and was_inv != now_inv:
            typer.echo(
                f"[trend]      invention: {was_inv} -> {now_inv} (ungated — moves with ordinary commits)"
            )

        if improvements and not regressions:
            typer.echo(
                "\n  The baseline is stale in the good direction. Re-run with --scoreboard to record it."
            )
        typer.echo(
            f"\npkg accuracy --check: {'FAILED' if regressions else 'OK'} — "
            f"{len(regressions)} gated regression(s), {len(improvements)} improvement(s)."
        )
    if regressions:
        raise typer.Exit(code=1)


@pkg_app.command("accuracy")
def pkg_accuracy(
    path: Annotated[
        str | None,
        typer.Argument(help="Corpus root (default 'corpus'), or the repo to trace with --oracle."),
    ] = None,
    oracle: Annotated[
        str | None,
        typer.Option(
            "--oracle",
            help="'runtime' EXECUTES THE REPO'S TEST SUITE to measure CALLS recall; "
            "'parity' compares declared routes/tables against the graph, reading only source.",
        ),
    ] = None,
    tests: Annotated[
        str | None,
        typer.Option("--tests", help="Test target(s) for --oracle runtime; default: the repo's own."),
    ] = None,
    sample: Annotated[
        int,
        typer.Option("--sample", help="With --oracle invention: also list N edges for human review."),
    ] = 0,
    kind: Annotated[
        str,
        typer.Option("--kind", help="Edge kind to sample (CONSUMES, EXPOSES, REFERENCES, CALLS)."),
    ] = "CONSUMES",
    scoreboard: Annotated[
        bool, typer.Option("--scoreboard", help="Write the committed accuracy baseline.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Compare against the baseline; exit non-zero on a GATED drop.")
    ] = False,
    language: Annotated[str | None, typer.Option("--language", help="Score only this language.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect (postgres|mysql|tsql|oracle|…); default: auto-detect."),
    ] = None,
) -> None:
    """Precision and recall per kind, against a hand-labelled corpus (read-only).

    `pkg verify` asks whether the graph contradicts itself, which needs no oracle. This asks
    whether the graph is *right*, which does — so it scores extraction against fixture
    repositories whose facts a human wrote down by hand (see `corpus/README.md`).

    With `--oracle runtime` it instead **runs the repository's own test suite** under a call
    tracer and reports what fraction of calls that demonstrably happened have a `CALLS` edge.
    That needs no labelling and works on any repo — but it *executes that repo's code*, which
    no other command here does, so it is never implied and the command is echoed first. It
    measures recall only: a call the tests never made is untested, not wrong.

    Reports; does not gate. Exits non-zero only when a corpus case is malformed or the suite
    cannot be run — never because a score is low.
    """
    from orchestrator.pkg.accuracy import CorpusError, score_corpus

    if scoreboard or check:
        _scoreboard(path or ".", scoreboard, as_json)
        return

    if oracle is not None:
        if oracle == "parity":
            _parity_oracle(path or ".", as_json)
            return
        if oracle == "invention":
            _invention_oracle(path or ".", sample, kind, as_json)
            return
        if oracle != "runtime":
            typer.echo(
                f"pkg accuracy: unknown oracle {oracle!r} — known oracles: corpus, runtime, parity, invention"
            )
            raise typer.Exit(code=1)
        _runtime_oracle(path or ".", tests, as_json)
        return

    corpus = path or "corpus"
    try:
        report = score_corpus(corpus, language=language, sql_dialect=dialect)
    except CorpusError as exc:
        typer.echo(f"pkg accuracy: corpus error — {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        _print(
            {
                "cases": [
                    {
                        "language": c.language,
                        "case": c.case,
                        "nodes": [
                            {
                                "kind": s.kind,
                                "precision": s.precision,
                                "recall": s.recall,
                                "expected": s.expected,
                                "emitted": s.emitted,
                                "matched": s.matched,
                            }
                            for s in c.nodes
                        ],
                        "edges": [
                            {
                                "kind": s.kind,
                                "precision": s.precision,
                                "recall": s.recall,
                                "expected": s.expected,
                                "emitted": s.emitted,
                                "matched": s.matched,
                            }
                            for s in c.edges
                        ],
                        "missing": list(c.missing),
                        "unlabelled": list(c.unlabelled),
                        "known_gaps": c.known_gaps,
                        "declared_false_positives": c.declared_false_positives,
                        "provenance_checked": c.provenance_checked,
                        "provenance_drift": list(c.provenance_drift),
                    }
                    for c in report.cases
                ],
                "totals": {
                    lang: {
                        group: [
                            {"kind": s.kind, "precision": s.precision, "recall": s.recall} for s in scores
                        ]
                        for group, scores in groups.items()
                    }
                    for lang, groups in report.totals().items()
                },
            }
        )
        return

    for case in report.cases:
        typer.echo(f"\n{case.language}/{case.case}")
        for group, scores in (("node", case.nodes), ("edge", case.edges)):
            for s in scores:
                typer.echo(
                    f"  {group} {s.kind:<10} P {_pct(s.precision)}  R {_pct(s.recall)}"
                    f"   (expected {s.expected}, emitted {s.emitted}, matched {s.matched})"
                )
        for label, items in (("missing", case.missing), ("unlabelled", case.unlabelled)):
            for item in items:
                typer.echo(f"    {label}: {item}")
        for item in case.provenance_drift:
            typer.echo(f"    provenance: {item}")
        if case.known_gaps or case.declared_false_positives:
            typer.echo(
                f"    annotated: {case.known_gaps} known gap(s), "
                f"{case.declared_false_positives} declared false positive(s) — neither changes a score"
            )

    for lang, groups in report.totals().items():
        typer.echo(f"\n{lang} — all cases")
        for group, scores in groups.items():
            for s in scores:
                typer.echo(f"  {group[:-1]} {s.kind:<10} P {_pct(s.precision)}  R {_pct(s.recall)}")

    if report.skipped:
        # Never silently drop: scoring 4 of 7 cases and printing only the 4 reads as a full
        # picture. The extras that are missing are the reason, and naming them is the fix.
        typer.echo(
            f"\n  SKIPPED {len(report.skipped)} case(s) — no front-end installed for "
            f"{', '.join(report.skipped_languages)}: {', '.join(report.skipped)}"
        )
        typer.echo("  Not scored zero: an absent optional extra is not a regression.")
    typer.echo(f"\npkg accuracy: {len(report.cases)} case(s) scored. Reporting only — nothing gated.")


@pkg_app.command("export")
def pkg_export(
    path: Annotated[str, typer.Argument(help="Repo path or git URL to scan.")] = ".",
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            help="sqlite | graphml | dot | json | obsidian. GraphML/DOT open in Gephi/yEd.",
        ),
    ] = "sqlite",
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Output file. Defaults to pkg-facts.<ext> for the format."),
    ] = None,
    db: Annotated[
        Path | None,
        typer.Option("--db", help="DEPRECATED alias for --out (sqlite only). Use --out."),
    ] = None,
) -> None:
    """Extract facts and export the whole graph in a format other tools can read.

    `sqlite` is the ontomesh-ready kind-per-table projection. `graphml` and `dot` open in
    Gephi, yEd, Cytoscape and Graphviz; `json` carries nodes AND edges (unlike
    `pkg extract --json`, which is nodes plus a summary). `obsidian` writes an Obsidian vault
    — a COPY of the repo's existing `episteme/` with wikilink syntax, so run `understand`
    first; it reads the knowledge base rather than re-extracting, and never edits it in place.

    Exports are **complete, never truncated** — the point of handing the graph to another tool
    is that its filtering is better than ours. Output is byte-identical for an identical commit.
    """
    from orchestrator.pkg import RepoCodeExtractor, export_sqlite
    from orchestrator.pkg.graph_export import GRAPH_FORMATS, WRITERS

    fmt = fmt.lower()
    if fmt not in ("sqlite", "obsidian", *GRAPH_FORMATS):
        typer.echo(f"Unknown --format {fmt!r}. Choose from: sqlite, obsidian, {', '.join(GRAPH_FORMATS)}.")
        raise typer.Exit(code=2)

    if fmt == "obsidian":
        # Reads the rendered episteme, not the fact graph — the vault is the same pages in a
        # different link syntax, so re-extracting would be wasted work and could disagree with
        # what is committed.
        from orchestrator.knowledge.understand import existing_bank_dir
        from orchestrator.knowledge.wikilinks import write_vault

        if db is not None:
            typer.echo("--db only applies to --format sqlite. Use --out.")
            raise typer.Exit(code=2)
        with _repo_arg(path) as (repo, _):
            bank = existing_bank_dir(repo)
            if not bank.is_dir():
                typer.echo(f"No knowledge base at {bank}. Run `orchestrator understand {path}` first.")
                raise typer.Exit(code=2)
            vault = out if out is not None else Path("pkg-vault")
            counts = write_vault(bank, vault)
        typer.echo(f"Exported {bank} → {vault} (obsidian vault)")
        for label, n in counts.items():
            typer.echo(f"  {label:<18} {n}")
        return

    # --db predates --format and is published surface, so it keeps working rather than being
    # silently ignored — that would break a script without saying so. It only ever meant sqlite.
    if db is not None:
        if fmt != "sqlite":
            typer.echo(f"--db only applies to --format sqlite (got {fmt!r}). Use --out instead.")
            raise typer.Exit(code=2)
        if out is not None:
            typer.echo("Pass either --out or --db, not both.")
            raise typer.Exit(code=2)
        typer.echo("note: --db is deprecated; use --out.")
        out = db

    suffix = {"sqlite": "db", "graphml": "graphml", "dot": "dot", "json": "json"}[fmt]
    target = out if out is not None else Path(f"pkg-facts.{suffix}")

    with _repo_arg(path) as (repo, _):
        batch = RepoCodeExtractor().extract(repo)
        if fmt != "sqlite":
            # Doc nodes + MENTIONS come from the link_docs post-pass, not raw extraction, so
            # without this the doc/media modality is invisible in the export — and media (G3)
            # reuses Doc, so transcripts and OCR'd images would vanish too. Not applied to
            # sqlite: that schema is kind-per-table with no doc table, so the nodes would be
            # dropped anyway, and its shape is a contract with the ontomesh consumer.
            from orchestrator.pkg import link_docs

            batch = link_docs(batch, repo)
    counts = export_sqlite(batch, target) if fmt == "sqlite" else WRITERS[fmt](batch, target)
    typer.echo(f"Exported {path} → {target} ({fmt})")
    for label, n in counts.items():
        typer.echo(f"  {label:<18} {n}")


@pkg_app.command("docs")
def pkg_docs(
    repo: Annotated[str, typer.Argument(help="Repo path or git URL to extract facts from.")] = ".",
    docs: Annotated[list[Path], typer.Option("--doc", "-d", help="Markdown/text doc(s) to reconcile.")] = [],  # noqa: B006
) -> None:
    """Reconcile documentation claims against the code's fact graph (read-only)."""
    from orchestrator.pkg import DocPage, DocReconciler, load_or_extract

    if not docs:
        typer.echo("No docs given — pass one or more --doc <file>.")
        raise typer.Exit(code=2)

    pages = [
        DocPage(
            title=str(p),
            text=p.read_text(encoding="utf-8"),
            base_dir=str(p.parent) if p.parent != Path(".") else "",
        )
        for p in docs
    ]
    with _repo_arg(repo) as (repo_path, _):
        batch = load_or_extract(repo_path)
        bindings, drift = DocReconciler(batch, repo_root=repo_path).reconcile(pages)

    bound = sum(1 for b in bindings if b.bound)
    typer.echo(
        f"{len(bindings)} code-intent mentions · {bound} bound to anchors · {len(drift)} drift finding(s)"
    )
    for f in drift:
        typer.echo(f"  [drift/{f.kind.value}] {f.page_title}: `{f.mention}` — unbound")


@media_app.command("extract")
def media_extract(
    paths: Annotated[
        list[Path], typer.Argument(help="Media file(s)/director(ies): images (OCR) + audio/video (ASR).")
    ],
    repo_root: Annotated[
        Path, typer.Option("--repo-root", help="Root whose .spine-media/ receives the artifacts.")
    ] = Path("."),
    force: Annotated[
        bool, typer.Option("--force", help="Re-extract even if an up-to-date artifact exists.")
    ] = False,
    asr: Annotated[
        str, typer.Option("--asr", help="Audio/video backend: 'local' (Whisper) or 'api' (remote).")
    ] = "local",
    whisper_model: Annotated[
        str, typer.Option("--whisper-model", help="Local Whisper model size (tiny/base/small/…).")
    ] = "base",
    api_endpoint: Annotated[
        str | None,
        typer.Option("--api-endpoint", help="OpenAI-compatible transcription URL (with --asr api)."),
    ] = None,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Consent to uploading audio/video OFF-MACHINE (--asr api)."),
    ] = False,
) -> None:
    """OCR images and transcribe audio/video into reviewable artifacts under .spine-media/.

    Explicit and opt-in: this MAY run a model and be slow. Image OCR and `--asr local` run entirely
    on this machine. `--asr api` UPLOADS audio/video to a remote service and therefore requires
    `--allow-remote`. The deterministic graph build (`understand`/`state`) never runs this; it only
    reads the committed artifacts. Review and commit the .spine-media/ files afterward.
    """
    from orchestrator.pkg.media import AUDIO_VIDEO_SUFFIXES, IMAGE_SUFFIXES
    from orchestrator.pkg.media_asr import (
        ApiAsrBackend,
        AsrBackend,
        LocalWhisperBackend,
        RemoteConsentRequiredError,
        extract_media,
    )
    from orchestrator.pkg.media_extract import (
        MediaExtractorUnavailableError,
        extract_image,
        iter_media_files,
    )

    files = iter_media_files(paths)
    if not files:
        typer.echo("No supported media (.png/.jpg/.webp/.mp3/.wav/.mp4/.mov) in the given path(s).")
        raise typer.Exit(code=2)

    needs_asr = any(f.suffix.lower() in AUDIO_VIDEO_SUFFIXES for f in files)
    backend: AsrBackend | None = None
    if needs_asr:
        if asr == "local":
            backend = LocalWhisperBackend(whisper_model)
        elif asr == "api":
            if not api_endpoint:
                typer.echo("ERROR: --asr api needs --api-endpoint <url>.", err=True)
                raise typer.Exit(code=2)
            backend = ApiAsrBackend(api_endpoint)
        else:
            typer.echo(f"ERROR: unknown --asr backend {asr!r} (use 'local' or 'api').", err=True)
            raise typer.Exit(code=2)
        where = "OFF-MACHINE (remote API)" if backend.off_machine else "local — nothing leaves this machine"
        typer.echo(f"Audio/video transcription: {where}.")

    written = 0
    try:
        for media in files:
            if media.suffix.lower() in IMAGE_SUFFIXES:
                result = extract_image(media, repo_root, force=force)
                unit = "label(s)"
            else:
                assert backend is not None  # noqa: S101  (type-narrowing for mypy; set whenever needs_asr)
                result = extract_media(media, repo_root, backend, allow_remote=allow_remote, force=force)
                unit = "segment(s)"
            if result.status == "written":
                written += 1
                note = " (truncated)" if result.truncated else ""
                typer.echo(f"  wrote {result.artifact}  · {result.segments} {unit}{note}")
            elif result.status == "unchanged":
                typer.echo(f"  unchanged {media} (artifact exists; --force to re-extract)")
            elif result.status == "skipped-too-large":
                typer.echo(f"  skipped {media} (larger than the size cap)")
    except RemoteConsentRequiredError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except MediaExtractorUnavailableError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Done — {written} artifact(s) written. Review and commit .spine-media/ to ingest them.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
