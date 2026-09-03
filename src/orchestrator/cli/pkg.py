"""Knowledge graph: the ``pkg`` sub-app — extraction, joins, verification, accuracy, export."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ._common import _print, _repo_arg

# ---------------------------------------------------------------------------
# pkg — Product Knowledge Graph (Layer 1: grounded code extraction)
# ---------------------------------------------------------------------------

pkg_app = typer.Typer(help="Product Knowledge Graph — code extraction (read-only).", no_args_is_help=True)


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


@pkg_app.command("joins")
def pkg_joins(
    config: Annotated[
        str, typer.Option("--config", help="The `.spine/repos.yaml` declaring the repos.")
    ] = ".spine/repos.yaml",
    propose: Annotated[
        bool, typer.Option("--propose", help="Suggest a `joins:` block from the evidence.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="List the calls no declared join could place.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit as JSON.")] = False,
) -> None:
    """Propose or check cross-repository joins (read-only; writes no config).

    `--propose` reads the evidence — calls a repo makes to paths it does not serve, against the
    endpoints its neighbours expose — and prints a `joins:` block to review. It never writes one:
    a topology Spine invented and then enforced would be a rule nobody agreed to.

    `--check` is the countermeasure for the quiet failure. A forgotten `repos:` entry is loud —
    no nodes, a visibly narrower graph. A forgotten `joins:` entry is not: missing cross-repo
    edges look exactly like two services that are not coupled, which reads as health. So the
    calls nothing placed are reported as a number rather than an absence.
    """
    from orchestrator.pkg.persistence import load_or_extract_repos
    from orchestrator.pkg.repos import RepoConfigError, load_repo_config

    if propose == check:
        typer.echo("pkg joins: choose exactly one of --propose or --check")
        raise typer.Exit(code=2)
    try:
        repo_set = load_repo_config(config)
    except RepoConfigError as exc:
        typer.echo(f"pkg joins: {exc}")
        raise typer.Exit(code=1) from exc

    merged = load_or_extract_repos(repo_set)
    if propose:
        _joins_propose(repo_set, merged, as_json)
    else:
        _joins_check(repo_set, merged, as_json)


def _joins_propose(repo_set: Any, merged: Any, as_json: bool) -> None:
    from orchestrator.pkg.joins_propose import propose as propose_joins
    from orchestrator.pkg.joins_propose import render

    unresolved = _unresolved_by_repo(repo_set)
    candidates = propose_joins(merged.batch, unresolved)
    if as_json:
        _print(
            [
                {
                    "kind": c.kind,
                    "consumer": c.consumer,
                    "provider": c.provider,
                    "base": c.base,
                    "edges": c.edges,
                    "examples": list(c.examples),
                }
                for c in candidates
            ]
        )
        return
    declared = {(j.kind, j.consumer, j.provider) for j in repo_set.joins}
    typer.echo(render(candidates))
    already = [c for c in candidates if (c.kind, c.consumer, c.provider) in declared]
    if already:
        typer.echo(f"  ({len(already)} of these are already declared — shown so the counts are checkable.)")


def _joins_check(repo_set: Any, merged: Any, as_json: bool) -> None:
    report = merged.joins
    if report is None:
        # Not the same as "everything joined". A config with no `joins:` block has nothing to
        # report against, and saying "0 unplaced" here would be the silence this command exists
        # to prevent.
        msg = "no joins declared — run `pkg joins --propose` to see what the evidence supports"
        _print({"declared": 0, "note": msg}) if as_json else typer.echo(f"pkg joins: {msg}")
        return

    if as_json:
        _print(
            {
                "joined": report.joined,
                "examined": report.examined,
                "recall": report.recall,
                "per_join": [{"join": k, "edges": v} for k, v in report.per_join],
                "unjoined": [
                    {"repo": u.repo, "verb": u.verb, "path": u.path, "at": u.where, "reason": u.reason}
                    for u in report.unjoined
                ],
            }
        )
        return

    rate = "—" if report.recall is None else f"{report.recall:.0%}"
    typer.echo(f"\ncross-repo joins — {report.joined} of {report.examined} candidate call(s) placed ({rate})")
    for label, count in report.per_join:
        # A declared join placing 0 is the row worth reading: it is either stale or wrong, and
        # a summary line would hide it inside a healthy-looking total.
        tail = "   ** placed nothing **" if count == 0 else ""
        typer.echo(f"  {label:<44} {count:>5}{tail}")

    if report.unjoined:
        by_reason: dict[str, int] = {}
        for u in report.unjoined:
            by_reason[u.reason] = by_reason.get(u.reason, 0) + 1
        typer.echo("\n  unplaced, by reason:")
        for reason, count in sorted(by_reason.items()):
            typer.echo(f"    {reason:<24} {count}")
        typer.echo("\n  first few:")
        for u in report.unjoined[:8]:
            typer.echo(f"    {u}")
    typer.echo(
        "\n  Precision is ~1.00 by construction — nothing joins to an undeclared repo — so the\n"
        "  number above is recall, and it is the one worth watching."
    )


def _unresolved_by_repo(repo_set: Any) -> dict[str, list[Any]]:
    """Re-extract to collect the side-channel. Cheap: every repo is cache-warm by now."""
    from orchestrator.pkg.joins_propose import unresolved_by_repo

    return unresolved_by_repo(repo_set)


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


def _comprehension_oracle(repo: str, as_json: bool, pinned_corpus: bool) -> None:
    """`--oracle comprehension`: do facts open to a line that names them?"""
    from orchestrator.pkg.accuracy import CorpusError, score_comprehension

    if pinned_corpus:
        import tempfile

        from orchestrator.evals.corpus_fetch import CorpusFetchError, load_manifest, materialize

        try:
            pinned = load_manifest()
        except CorpusFetchError as exc:
            typer.echo(f"pkg accuracy: {exc}")
            raise typer.Exit(code=1) from exc
        # Task-scoped: the corpus lives for this command and no longer, so a later run cannot
        # inherit a half-fetched tree. See evals/corpus_fetch.py.
        with tempfile.TemporaryDirectory(prefix="spine-g6-") as tmp:
            rows: list[dict[str, Any]] = []
            roots: dict[str, Path] = {}
            for entry in pinned:
                typer.echo(f"  fetching {entry.name} @ {entry.sha[:12]} ...", err=True)
                try:
                    path = materialize(entry, tmp)
                except CorpusFetchError as exc:
                    # All-or-nothing: scoring what happened to arrive would publish a number
                    # whose denominator moved with the network.
                    typer.echo(f"pkg accuracy: corpus incomplete — {exc}")
                    raise typer.Exit(code=1) from exc
                roots[entry.name] = path
                report = score_comprehension(path)
                rate = float(report.rate) if report.rate is not None else None
                rows.append(
                    {
                        "repo": entry.name,
                        "language": entry.language,
                        "sha": entry.sha,
                        "anchored": report.anchored,
                        "resolved": report.resolved,
                        "excluded": report.excluded,
                        "rate": rate,
                    }
                )

            # Inside the `with`, and it has to be: scoring reads the checkouts, and the
            # temporary directory is gone the moment this block exits. Run outside it, every
            # path is missing, extraction yields an empty graph, and all 24 labels score as
            # "no landing site" — a total failure that looks exactly like the tool finding
            # nothing, which is the reading that would have been published.
            from orchestrator.evals.labels import load_labels
            from orchestrator.evals.localization import score_localization

            gold = load_labels(known_repos={r.name for r in pinned})
            localization = score_localization(gold, roots) if gold.measured else None

        if as_json:
            _print(
                {
                    "oracle": "comprehension",
                    "corpus": rows,
                    "localization": localization.as_dict() if localization else None,
                }
            )
            return
        typer.echo("\nprovenance validity on the pinned corpus")
        for r in rows:
            shown = f"{r['rate']:.4f}" if r["rate"] is not None else "not measured"
            typer.echo(
                f"  {r['repo']:10s} {r['language']:11s} {r['resolved']:6d}/{r['anchored']:<6d} {shown}"
            )
        # A repository that yielded nothing almost always means its language extra is absent,
        # and the consequence is not a missing row — it is a LOWER localization number, because
        # every label in that repository becomes unfindable. Silent, that reads as the tool
        # performing badly. Said out loud instead.
        empty = [str(r["repo"]) for r in rows if not r["anchored"]]
        if empty:
            typer.echo(
                f"\n  WARNING: {', '.join(empty)} produced no facts — almost certainly a missing\n"
                "  language extra. Install them before quoting any number here; labels in those\n"
                "  repositories cannot be found at all: pip install 'synaptixs-spine[languages]'"
            )
        if localization is not None:
            typer.echo(f"\ntop-k localization — {len(localization.results)} labelled issues")
            for k in localization.ks:
                hit_rate = localization.rate_at(k)
                shown = f"{float(hit_rate):.2f}" if hit_rate is not None else "n/a"
                typer.echo(
                    f"  top-{k:<3} {localization.hits_at(k):3d}/{len(localization.results):<3d}  {shown}"
                )
            if localization.as_dict()["empty_results"]:
                typer.echo(
                    f"  {localization.as_dict()['empty_results']} issue(s) returned no landing "
                    "site at all — a different failure from ranking one badly"
                )
        else:
            typer.echo(
                "\ntop-k localization — NOT MEASURED: the gold set is empty.\n"
                "  Not 0: a 0 would be indistinguishable from never having measured it."
            )
        if gold.excluded:
            typer.echo(f"\n  {len(gold.excluded)} issue(s) examined and excluded, with reasons.")
        typer.echo("\n  C# has no slot in this corpus — five repositories, six front-ends.")
        return

    try:
        report = score_comprehension(repo)
    except CorpusError as exc:
        typer.echo(f"pkg accuracy: {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        _print(
            {
                "oracle": "comprehension",
                "anchored": report.anchored,
                "resolved": report.resolved,
                "excluded": report.excluded,
                "unreadable": report.unreadable,
                "rate": float(report.rate) if report.rate is not None else None,
                "localization": "not_measured",
            }
        )
        return

    if not report.measured:
        typer.echo("\nno anchored facts — nothing to measure, which is not the same as a clean result")
        return
    typer.echo(
        f"\nprovenance validity — {report.resolved} of {report.anchored} anchored facts "
        f"({float(report.rate):.2%}) open to a line that names them"
    )
    typer.echo(f"  {report.excluded} excluded: Module, Endpoint and Entity are named by construction")
    if report.unreadable:
        typer.echo(f"  {report.unreadable} unreadable file(s) — reported, never counted as passing")
    typer.echo(
        "\n  Localization is NOT measured: it needs the gold set G6 D1 called for, and a zero\n"
        "  there would be indistinguishable from never having measured it.\n"
        "  --pinned-corpus runs this against the five pinned repositories instead (network)."
    )


def _drift_oracle(repo: str, as_json: bool) -> None:
    """`--oracle drift`: doc claims the graph cannot support, and the rate the gate ratchets."""
    from orchestrator.pkg.accuracy import CorpusError, score_drift

    try:
        report = score_drift(repo)
    except CorpusError as exc:
        typer.echo(f"pkg accuracy: {exc}")
        raise typer.Exit(code=1) from exc

    rate = float(report.rate) if report.rate is not None else None
    if as_json:
        _print(
            {
                "oracle": "drift",
                "count": report.count,
                "mentions": report.mentions,
                "docs": report.docs,
                "rate": rate,
                "measured": report.measured,
                "gated": False,
            }
        )
        return

    if not report.measured:
        typer.echo("\nno documentation read — nothing to measure, which is not the same as no drift")
        return
    shown = f"{rate:.1%}" if rate is not None else "n/a"
    typer.echo(
        f"\ndocumentation drift — {report.count} unbound of {report.mentions} claims ({shown}), "
        f"across {report.docs} sections"
    )
    typer.echo("  symbol-shaped claims only; paths, URLs and filenames are filtered out")
    typer.echo(
        "\n  Recorded, never gated. About a tenth of these cannot bind by construction —\n"
        "  parameters, module constants, string literals and log event names have no node kind —\n"
        "  so this is an UPPER BOUND on drift, not a defect count. `orchestrator state` reports\n"
        "  the same number and names the claims."
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


def _scoreboard(repo: str, write: bool, as_json: bool, pinned_corpus: bool = False) -> None:
    """`--scoreboard` writes the committed baseline; `--check` compares against it.

    ``pinned_corpus`` additionally fetches the five pinned repositories and scores localization.
    Off by default, and that is the contract: the baseline CI compares on every pull request must
    not depend on the network, so an ordinary run records localization as *not measured* and the
    gate skips it rather than reading its absence as zero.
    """
    import json as _json

    from orchestrator.pkg.accuracy import build_scoreboard, compare_scoreboard, scoreboard_improvements

    root = Path(repo)
    path = root / SCOREBOARD_FILE
    localization = None
    if pinned_corpus:
        from orchestrator.evals.localization import measure_pinned

        typer.echo("  fetching the pinned corpus to score localization ...", err=True)
        localization = measure_pinned()
        if localization is None:
            typer.echo("pkg accuracy: the pinned corpus could not be scored — refusing to record a number")
            raise typer.Exit(code=1)
    current = build_scoreboard(root / "corpus", root, localization=localization)
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

        was_drift = baseline.get("metrics", {}).get("drift", {}).get("count")
        now_drift = current["metrics"]["drift"]["count"]
        if was_drift is not None and was_drift != now_drift:
            typer.echo(
                f"[trend]      doc drift: {was_drift} -> {now_drift} "
                "(ungated — a tenth of it cannot bind by construction; see GATES)"
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


@pkg_app.command("fix-sites")
def pkg_fix_sites(
    repo: Annotated[str, typer.Argument(help="A repo name from the G6 corpus manifest.")],
    commit: Annotated[str, typer.Argument(help="The full 40-character commit that fixed the issue.")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
) -> None:
    """What a fixing commit changed — the raw material for a G6 gold-set label.

    Prints paths and change counts straight from git. **It does not choose for you**, and that
    is deliberate: a commit usually touches tests, changelogs and incidental tidying, and
    deciding which change *is* the fix is the judgement the hand-labelled gold set exists to
    capture. A candidate picked by reading the ticket the way `investigate` reads it would not
    be independent of the thing being scored.
    """
    import tempfile

    from orchestrator.core.pinned_checkout import CheckoutError, _git, materialize_at
    from orchestrator.evals.corpus_fetch import load_manifest

    entry = next((r for r in load_manifest() if r.name == repo), None)
    if entry is None:
        known = ", ".join(r.name for r in load_manifest())
        typer.echo(f"pkg fix-sites: unknown repo {repo!r} — the corpus holds: {known}")
        raise typer.Exit(code=1)

    with tempfile.TemporaryDirectory(prefix="spine-fixsites-") as tmp:
        try:
            # Fetch the FIXING commit, not the pin: the pin is the pre-fix tree we score
            # against, and this is the change that answers the question.
            # Depth 2: the commit AND its parent, so `--numstat` is a real diff. At depth 1
            # there is no parent and every file reads as added.
            root = materialize_at(entry.url, commit, Path(tmp) / repo, depth=2)
            stat = _git("show", "--numstat", "--format=%s%n%b", commit, cwd=root)
        except CheckoutError as exc:
            typer.echo(f"pkg fix-sites: {exc}")
            raise typer.Exit(code=1) from exc

    message: list[str] = []
    files: list[dict[str, Any]] = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and (parts[0].isdigit() or parts[0] == "-"):
            files.append({"path": parts[2], "added": parts[0], "removed": parts[1]})
        elif line.strip():
            message.append(line)

    if as_json:
        _print({"repo": repo, "commit": commit, "message": message, "files": files})
        return

    typer.echo(f"\n{repo} @ {commit[:12]}")
    for line in message[:5]:
        typer.echo(f"  {line}")
    typer.echo(f"\n  {len(files)} file(s) changed:")
    for f in files:
        typer.echo(f"    +{f['added']:>5} -{f['removed']:>5}  {f['path']}")
    typer.echo(
        "\n  Which of these IS the fix is yours to decide — tests, changelogs and tidying\n"
        "  travel with it. Record the real site(s) in evals/comprehension_labels.yaml,\n"
        "  then: orchestrator pkg labels --check"
    )


@pkg_app.command("labels")
def pkg_labels(
    check: Annotated[
        bool, typer.Option("--check", help="Validate the gold set and exit non-zero on a problem.")
    ] = False,
    paths: Annotated[
        bool,
        typer.Option("--paths", help="Also verify every labelled path exists in the pinned tree (network)."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable.")] = False,
) -> None:
    """The G6 gold set: what is labelled, what was excluded and why."""
    from orchestrator.evals.corpus_fetch import load_manifest
    from orchestrator.evals.labels import LabelError, load_labels

    try:
        manifest = load_manifest()
        gold = load_labels(known_repos={r.name for r in manifest})
    except LabelError as exc:
        typer.echo(f"pkg labels: {exc}")
        raise typer.Exit(code=1) from exc

    problems: list[str] = []
    if paths:
        import tempfile

        from orchestrator.core.pinned_checkout import CheckoutError, materialize_at
        from orchestrator.evals.labels import unresolvable_paths

        by_name = {r.name: r for r in manifest}
        with tempfile.TemporaryDirectory(prefix="spine-labels-") as tmp:
            roots: dict[str, Path] = {}
            for label in gold.labels:
                if label.repo not in roots:
                    try:
                        roots[label.repo] = materialize_at(
                            by_name[label.repo].url, by_name[label.repo].sha, Path(tmp) / label.repo
                        )
                    except CheckoutError as exc:
                        problems.append(f"{label.repo}: could not materialise — {exc}")
                        continue
                if label.repo not in roots:
                    continue
                missing = unresolvable_paths(label, roots[label.repo])
                if missing:
                    problems.append(
                        f"{label.issue}: {missing} not in the pinned tree — a path the fix "
                        "CREATED can never be found by a run against the pre-fix state"
                    )

    if as_json:
        _print(
            {
                "labelled": len(gold.labels),
                "excluded": [{"repo": e.repo, "issue": e.issue, "reason": e.reason} for e in gold.excluded],
                "problems": problems,
                "ok": not problems,
            }
        )
    else:
        typer.echo(f"\ngold set — {len(gold.labels)} labelled, {len(gold.excluded)} excluded")
        for e in gold.excluded:
            typer.echo(f"  excluded  {e.issue} — {e.reason}")
        for p in problems:
            typer.echo(f"  [PROBLEM] {p}")
        if not gold.labels:
            typer.echo(
                "\n  Nothing labelled yet, so localization reports `not_measured` rather than 0 —\n"
                "  a 0 would be indistinguishable from never having measured.\n"
                "  Start with: orchestrator pkg fix-sites <repo> <fix-commit>"
            )
    if check and problems:
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
            "'parity' compares declared routes/tables against the graph, reading only source; "
            "'drift' counts doc claims the graph cannot support; "
            "'comprehension' checks facts open to a line that names them.",
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
    pinned_corpus: Annotated[
        bool,
        typer.Option(
            "--pinned-corpus/--no-pinned-corpus",
            help="With --oracle comprehension: fetch and score the five PINNED repositories "
            "(needs network; all-or-nothing). Named apart from the positional corpus root.",
        ),
    ] = False,
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
        _scoreboard(path or ".", scoreboard, as_json, pinned_corpus)
        return

    if oracle is not None:
        if oracle == "parity":
            _parity_oracle(path or ".", as_json)
            return
        if oracle == "invention":
            _invention_oracle(path or ".", sample, kind, as_json)
            return
        if oracle == "drift":
            _drift_oracle(path or ".", as_json)
            return
        if oracle == "comprehension":
            _comprehension_oracle(path or ".", as_json, pinned_corpus)
            return
        if oracle != "runtime":
            typer.echo(
                f"pkg accuracy: unknown oracle {oracle!r} — "
                "known oracles: corpus, runtime, parity, invention, drift, comprehension"
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
    intents: Annotated[
        bool,
        typer.Option(
            "--intents",
            help="Also emit Intent nodes + SERVES edges (which ticket a symbol was last "
            "changed for). Costs a `git blame` pass; ignored for --format sqlite.",
        ),
    ] = False,
    intent_prefix: Annotated[
        list[str] | None,
        typer.Option(
            "--intent-prefix",
            help="Issue-key prefix to accept, repeatable (e.g. --intent-prefix PROJ). Default: "
            "infer the repository's dominant prefix, which the run reports.",
        ),
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
        if intents and fmt == "sqlite":
            # Said, not silently ignored. That schema is kind-per-table and is a contract with
            # the ontomesh consumer; `link_docs` is excluded from it for the same reason.
            typer.echo(
                "  note: --intents does not apply to --format sqlite (kind-per-table schema, "
                "no intent table)",
                err=True,
            )
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

            # Same post-pass argument, same modality problem: Intent nodes and SERVES edges
            # come from `link_intents`, so without this the recorded-intent tier is invisible
            # in the export exactly as docs would be. The exporters do not filter by kind —
            # `export_json` emits every node — so the facts were absent because nothing added
            # them, not because anything rejected them.
            #
            # Opt-in rather than unconditional, unlike docs: blame roughly doubles the cost of
            # an extraction, and on a repository whose commits carry no issue keys it buys
            # nothing at all.
            if intents:
                from orchestrator.pkg.intent_link import link_intents

                coverage = link_intents(batch, repo, prefixes=intent_prefix or None)
                rate = f"{coverage.rate:.1%}" if coverage.rate is not None else "n/a"
                # What it decided, not just what it found. The generic key pattern reads
                # `SHA-256` and `ISO-8601` as tickets, so which prefixes were accepted is the
                # difference between a measurement and a guess — and an operator who can see
                # the rejects can correct them with --intent-prefix.
                typer.echo(
                    f"  intent prefixes: accepted {', '.join(coverage.prefixes_used) or 'none'}"
                    + (
                        f"; rejected as not tickets: {', '.join(coverage.prefixes_rejected)}"
                        if coverage.prefixes_rejected
                        else ""
                    ),
                    err=True,
                )
                # The denominator travels with the facts. A tier that attributes 12 of 4,000
                # symbols is working as designed and says almost nothing, and only the ratio
                # makes that visible to whoever reads the export.
                typer.echo(
                    f"  intents: {coverage.intents} from {coverage.commits_keyed} keyed commit(s); "
                    f"{coverage.symbols_attributed}/{coverage.symbols_total} symbols attributed ({rate})",
                    err=True,
                )
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
