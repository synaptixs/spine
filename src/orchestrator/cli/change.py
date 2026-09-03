"""Investigate & design a change: investigate, design, localize, rca, regression."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ._app import PANEL_CHANGE, app
from ._common import _repo_arg


@app.command("design", rich_help_panel=PANEL_CHANGE)
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


@app.command("investigate", rich_help_panel=PANEL_CHANGE)
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
    repos: Annotated[
        str | None,
        typer.Option("--repos", help="A `.spine/repos.yaml` — research across every declared repo."),
    ] = None,
    intents: Annotated[
        bool,
        typer.Option(
            "--intents",
            help="Also report which ticket each landing symbol was last changed for. Costs a "
            "`git blame` pass; single-repo only.",
        ),
    ] = False,
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

    if repos:
        # A merged graph with its joins applied: landing sites then carry their repository,
        # blast radius crosses a boundary, and a ticket landing in two services says so.
        from orchestrator.pkg.persistence import load_or_extract_repos
        from orchestrator.pkg.repos import RepoConfigError, load_repo_config

        try:
            repo_set = load_repo_config(repos)
        except RepoConfigError as exc:
            typer.echo(f"investigate: {exc}")
            raise typer.Exit(code=1) from exc
        merged = load_or_extract_repos(repo_set, extractor=RepoCodeExtractor(sql_dialect=dialect))
        if not merged.trusted:
            # The brief is still useful, but it cannot be reproduced at a commit — and nothing
            # in the markdown would tell a reader that later.
            typer.echo(
                f"investigate: NOT REPRODUCIBLE — {', '.join(merged.untrusted_keys)} "
                "has uncommitted work or is not a git repo.",
                err=True,
            )
        # `root=None`: `episteme/` belongs to one repository, and a merged brief has no single
        # owner for it. The section is omitted rather than filled from an arbitrary repo — the
        # brief is written to be honest when a section has nothing grounded.
        if intents:
            # Single-repo only: blame is per checkout, and a merged graph has several. Said
            # rather than ignored — a flag that silently does nothing is worse than one that
            # is refused.
            typer.echo("investigate: --intents does not apply with --repos (blame is per repo)", err=True)
        inv = build_investigation(ticket_title, problem, store=FactStore(merged.batch), root=None)
    else:
        with _repo_arg(path) as (repo, _):
            extractor = RepoCodeExtractor(sql_dialect=dialect)
            batch = extractor.extract(repo) if refresh else load_or_extract(repo, extractor=extractor)
            if intents:
                from orchestrator.pkg.intent_link import link_intents

                coverage = link_intents(batch, repo)
                rate = f"{coverage.rate:.1%}" if coverage.rate is not None else "n/a"
                # The rate goes to stderr beside the brief, not into it: the brief is the
                # artifact a human reads and a run records, and a coverage figure belongs with
                # the scan that produced it. Without it, a landing with no ticket reads as a
                # symbol with no prior work, and here that would be nine landings in ten.
                typer.echo(
                    f"  recorded intent: {coverage.intents} ticket(s) from "
                    f"{', '.join(coverage.prefixes_used) or 'no prefix'}; "
                    f"{coverage.symbols_attributed}/{coverage.symbols_total} symbols ({rate})",
                    err=True,
                )
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


@app.command("localize", rich_help_panel=PANEL_CHANGE)
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


@app.command("rca", rich_help_panel=PANEL_CHANGE)
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


@app.command("regression", rich_help_panel=PANEL_CHANGE)
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
