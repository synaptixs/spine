"""Plan & build, the intake half: ingest, backlog, openspec. The sdlc sub-app is its own module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from ._app import PANEL_BUILD, app
from ._common import _merged_store, _print, _repo_arg

if TYPE_CHECKING:  # pragma: no cover - typing only; nothing here is imported at runtime
    from pathlib import Path

    from orchestrator.intake.pkg_evidence import DraftGrounding
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.pkg import FactStore
    from orchestrator.sdlc.landings import Landing

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
    path: Annotated[
        str | None,
        typer.Argument(help="Repo path to ground the draft against (default: ungrounded)."),
    ] = None,
    repos: Annotated[
        str | None,
        typer.Option("--repos", help="A `.spine/repos.yaml` — ground against every declared repo."),
    ] = None,
    dialect: Annotated[
        str | None, typer.Option("--dialect", help="SQL dialect; default: auto-detect.")
    ] = None,
) -> None:
    """Bootstrap OpenSpec change proposals FROM an unstructured source (the write-back).

    Runs the LLM intake once (source → intents → specs), then renders each as a
    structured `openspec/changes/<id>/` proposal (proposal.md + specs delta + tasks).
    A human polishes the draft, then implements deterministically:

        orchestrator openspec draft --source confluence://<id> --out ./openspec
        # …review/edit openspec/changes/<id>/…
        orchestrator sdlc feature --source openspec://<id> --safe

    Pass a repo path (or `--repos`) to ground the draft against the code — the proposal then
    carries what the graph says, fenced off from the model's prose and labelled. Without one
    the draft is **ungrounded, and says so on its own face** rather than leaving a reader to
    wonder which mode produced it: the requirements and scenarios are unchanged, and the page
    states that nothing checked them.
    """
    import asyncio

    asyncio.run(
        _run_openspec_draft(
            source, out=out, refresh=refresh, overwrite=overwrite, path=path, repos=repos, dialect=dialect
        )
    )


def _grounding_for(
    path: str | None, repos: str | None, dialect: str | None
) -> tuple[DraftGrounding, FactStore | None, Path | None, dict[str, Path] | None]:
    """Read the repository once, and classify what came back.

    Returns ``(grounding, store, root, repo_roots)`` — the store and roots are what the
    per-spec pass needs, and reading them once is the whole point: extraction is the expensive
    half and cannot differ between the N changes one source drafts (D9).

    **This is the composition root, on purpose.** The evidence needs landing sites, which are
    computed in `sdlc`; `intake` may not import `sdlc` at module level, and moving the landing
    machinery down into `pkg` would drag `CoverageIndex`, `excerpt` and `brief` with it. The
    CLI is the one layer allowed to read both, so it reads both and hands the result down —
    which is also why `render_change` takes grounding as an argument rather than fetching it.
    """
    from orchestrator.intake import pkg_evidence
    from orchestrator.pkg import RepoCodeExtractor

    if not path and not repos:
        return pkg_evidence.ungrounded(), None, None, None
    if repos:
        store, merged, repo_set = _merged_store(
            repos, command="openspec draft", extractor=RepoCodeExtractor(sql_dialect=dialect)
        )
        untrusted = tuple(merged.untrusted_keys) if not merged.trusted else ()
        base = pkg_evidence.from_store(store, where=repos, untrusted=untrusted)
        return base, store, None, dict(repo_set.roots)
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.persistence import repo_state

    with _repo_arg(str(path)) as (repo, is_remote):
        batch = load_or_extract(repo, extractor=RepoCodeExtractor(sql_dialect=dialect))
        # The single-repo path gets no standing for free the way a merged graph does, so ask
        # for it. Without this, D18's warning would fire only under `--repos` — and a dirty
        # single checkout is the far commoner way to draft against unreproducible evidence.
        _sha, dirty = repo_state(repo)
        store = FactStore(batch)
        base = pkg_evidence.from_store(store, where=str(path), untrusted=(str(path),) if dirty else ())
        # `repo` is context-managed: a git URL is cloned here and **removed when this block
        # exits**, so returning it would hand the per-spec pass a path that no longer exists.
        # Nothing raises when that happens — `rglob` on a missing directory yields nothing and
        # `is_file()` is False — so a criterion naming a real file would be reported as one the
        # graph cannot find. A false statement of absence inside the fact block is the exact
        # failure this track exists to prevent, so the degradation is **chosen and narrowed**:
        # a remote draft binds against the graph only, never the tree.
        return base, store, (None if is_remote else repo), None


def _facts_for_spec(
    base: DraftGrounding,
    store: FactStore,
    root: Path | None,
    repo_roots: dict[str, Path] | None,
    spec: FeatureSpec,
) -> DraftGrounding:
    """Retrieval and binding for **one** change (D21).

    Per spec, not per source: a shared block would cite identical sites in every change dir,
    which is actively misleading the moment the specs diverge — the "looks verified" failure
    one level out from the one this whole track is about.
    """
    from orchestrator.intake import pkg_evidence
    from orchestrator.pkg.criteria_binding import bind_criteria
    from orchestrator.sdlc.investigate import build_investigation
    from orchestrator.sdlc.landings import render_landings

    problem = (spec.description or spec.summary or "").strip()
    inv = build_investigation(spec.title, problem, store=store, root=root, repo_roots=repo_roots)
    groups: list[pkg_evidence.LandingGroup] = []
    if repo_roots:
        # Grouped by repository key, following the shape `investigate` settled on for a merged
        # brief: a **declared** repository with nothing to show is named, never silently
        # dropped. Seeded from `repo_roots` — the declared set — and not from `inv.repos`,
        # which `investigate` builds *inside* its hit loop and is therefore exactly the set of
        # repos that did land. Seeding from that made `absent` unreachable: every key already
        # had a hit, so the honesty branch this comment describes was dead code, and a repo the
        # change does not touch simply vanished from the page.
        by_repo: dict[str, list[Landing]] = {key: [] for key in repo_roots}
        for hit in inv.landing:
            by_repo.setdefault(hit.repo, []).append(hit)
        for key in sorted(by_repo):
            hits = by_repo[key]
            groups.append(
                pkg_evidence.LandingGroup(repo=key, bullets=tuple(render_landings(hits)), absent=not hits)
            )
    elif inv.landing:
        groups.append(pkg_evidence.LandingGroup(repo="", bullets=tuple(render_landings(inv.landing))))
    # `bind_criteria` takes **one** root, and a merged graph has several. Symbol anchors and
    # file anchors drawn from node provenance need no root at all, so most binding is
    # unaffected — but two last-resort paths do read the tree: the existence check for a file
    # the extractor never parsed (a config, a markdown page), and snake-token stem resolution.
    # With exactly one declared repository there is no ambiguity, so pass it. With several,
    # binding stays graph-only and the page **says so** rather than reporting a criterion as
    # unfindable when nothing looked for it on disk.
    bind_root = root
    tree_checked = True
    # `in_evidence` is the strongest badge in the criteria section — "this criterion binds
    # *where the ticket actually is*". It is a plain string test over repo-stripped paths on
    # both sides: the landing loses its repo in `where.split(":")[0]`, and the anchor's own
    # `where` is node provenance, which was never scoped. Two services that both have
    # `app/models.py` is the normal case, so in a merged graph the badge can land on the wrong
    # checkout. Withheld rather than guessed — a wrong strongest-signal is worse than none.
    landed = tuple(sorted({hit.where.split(":", 1)[0] for hit in inv.landing if hit.where}))
    if repo_roots:
        roots = list(repo_roots.values())
        bind_root = roots[0] if len(roots) == 1 else None
        tree_checked = bind_root is not None
        if len(roots) > 1:
            landed = ()
    binding = bind_criteria(
        spec.model_dump(),
        store=store,
        evidence_files=landed,
        root=bind_root,
    )
    return pkg_evidence.with_facts(
        base,
        landings=tuple(groups),
        elided=inv.elided,
        areas=tuple(inv.areas),
        binding=binding,
        tree_checked=tree_checked,
    )


async def _run_openspec_draft(
    source: str,
    *,
    out: str,
    refresh: bool,
    overwrite: bool,
    path: str | None = None,
    repos: str | None = None,
    dialect: str | None = None,
) -> None:
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

    # Read the repository **before** the intake, not after. `analyze_cached` is a paid model
    # run on a cache miss, and the grounding arguments are the ones a user most easily
    # mistypes — a wrong path or a malformed repos.yaml used to fail only once the spend had
    # already happened. Extraction is cached and commit-keyed, so doing it first costs nothing
    # on the path where both succeed.
    base_grounding, store, repo_root, repo_roots = _grounding_for(path, repos, dialect)

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
        grounding = (
            _facts_for_spec(base_grounding, store, repo_root, repo_roots, spec)
            if store is not None
            else base_grounding
        )
        written = write_change(root, intent, render_change(spec, intent, grounding), overwrite=overwrite)
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
