"""Plan & build, the intake half: ingest, backlog, openspec. The sdlc sub-app is its own module."""

from __future__ import annotations

from typing import Annotated

import typer

from ._app import PANEL_BUILD, app
from ._common import _print

openspec_app = typer.Typer(help="Spec-driven development with OpenSpec (openspec.dev).", no_args_is_help=True)


@app.command("ingest", rich_help_panel=PANEL_BUILD)
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


@app.command("backlog", rich_help_panel=PANEL_BUILD)
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
