"""Understand a codebase: profile, understand, state, audit."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Annotated, Any

import typer

from ._app import PANEL_UNDERSTAND, app
from ._common import _print, _repo_arg


@app.command("audit", rich_help_panel=PANEL_UNDERSTAND)
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


@app.command("profile", rich_help_panel=PANEL_UNDERSTAND)
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


@app.command("understand", rich_help_panel=PANEL_UNDERSTAND)
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


@app.command("state", rich_help_panel=PANEL_UNDERSTAND)
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
