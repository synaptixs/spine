"""Expose the orchestrator *as* an MCP server (the plugin surface).

This is the inverse of ``orchestrator.mcp`` (which *consumes* MCP servers):
here the orchestrator is the server, so Claude Code / Codex / Claude Desktop can
call its capabilities as tools. The MCP server is a thin façade — each tool runs
the real engine (intake, PKG grounding, readiness).

**Three tiers, separated by what a tool can cost you if it is wrong.**

**1. Read-only comprehension** — ``doctor``, ``pkg_grounding``, ``read_memory_bank``,
``ingest_preview`` (dry-run), and the graph-query set ``map_repo`` / ``blast_radius`` /
``explain_symbol`` / ``investigate`` / ``localize`` / ``regression_gaps`` / ``root_cause`` /
``docs_for``. Hands an assistant Spine's *engineering decisions* (what breaks, what's
untested, where a change lands, which docs describe it) with ``file:line`` provenance.
Deterministic, no credentials — except ``root_cause``'s opt-in ``use_llm`` enrichment. These
take a local path **or a git URL** (shallow-cloned behind the CLI's SSRF/host-allow-list
guard).

``blast_radius`` and ``investigate`` additionally take ``repos`` — a ``.spine/repos.yaml`` —
and answer across every declared repository, reporting the dependents a change reaches in
*other* repos. ``pkg_joins`` proposes or checks the topology that makes those edges possible;
it is read-only and never writes a config. Every multi-repo answer carries a ``standing``
block, because a merged graph built over a dirty tree looks identical to one that is
reproducible.

**2. Plan and decide** — ``sdlc_plan`` and ``sdlc_approve``. These *write*, but only under
``.spine/`` in the repo, and they still need no model and no credentials: ``build_plan``
passes ``llm=None`` throughout, so the twelve-section document is rendered from the graph,
git and the tree alone. That property is the point of this tier rather than an accident of
it — it is what lets a host with its own model and its own tracker credentials drive Spine
on a machine where Spine itself has neither.

**3. The gated ``sdlc`` run** — ``sdlc_feature`` and the ``sdlc_start_run`` / ``_status`` /
``_decide_gate`` / ``_result`` set. **Gated means two things, and both matter.** It spends
real money: every call drives a model through codegen, tests and review. And with
``live=true`` it writes where you cannot take it back — a tracker issue, a pushed branch, an
open PR — which is why ``live`` additionally requires ``confirm=true``, an explicit human
authorization on top of whatever confirmation the host already asks for. Safe mode
(``live=false``) still costs tokens; it just keeps every write local.

An assistant should work down the tiers, not up: comprehend, then plan and get the plan
approved, then build. A run that starts at tier 3 is one nobody reviewed.

Tool *implementations* are module-level functions (unit-testable without the ``mcp``
extra); ``build_server`` lazy-imports ``FastMCP`` and registers them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


def doctor() -> dict[str, Any]:
    """Report environment readiness (LLM provider, Confluence/Jira, MCP, …)."""
    from orchestrator.doctor import run_env_checks

    results = run_env_checks()
    return {
        "all_passed": all(r.passed for r in results),
        "checks": [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results],
    }


async def ingest_preview(source: str) -> dict[str, Any]:
    """Preview the backlog for a requirements source — dry-run, writes nothing.

    ``source`` is a ``<kind>://<root>`` URI: ``file://./spec.md``,
    ``confluence://<id>``, ``notion://<id>``, or ``mcp-confluence://<id>``.
    Returns the derived intents + gap summary.
    """
    from orchestrator.intake.factory import build_service_for
    from orchestrator.intake.service import parse_source_uri

    _, root_id = parse_source_uri(source)
    plan = await build_service_for(source, dry_run=True).analyze(root_id)
    return {
        "documents": len(plan.documents),
        "intent_count": len(plan.intents),
        "intents": [{"id": i.id, "title": i.title} for i in plan.intents],
        "gap_count": len(plan.gaps),
        "blocked": plan.blocked,
    }


async def sdlc_feature(
    source: str,
    intent_id: str | None = None,
    repo: str | None = None,
    language: str = "auto",
    layout: str = "auto",
    package_name: str | None = None,
    live: bool = False,
    confirm: bool = False,
    max_refine: int = 3,
) -> dict[str, Any]:
    """Build ONE intent end to end: spec → grounded codegen → tests → branch.

    Works for **greenfield and brownfield**:
    - ``repo`` — a git URL/owner-slug to branch from (e.g. ``https://github.com/me/app``
      or ``me/app``). Omit for a throwaway scratch repo (pure greenfield demo).
    - ``layout`` — ``auto`` (scaffold only empty repos), ``new`` (always scaffold a
      fresh ``src/<pkg>/`` skeleton — greenfield into an existing repo), or
      ``existing`` (follow the repo's own structure — **brownfield**).
    - ``language`` — ``auto`` (detect from the repo) or an explicit
      ``python|java|typescript|csharp|c|cpp``.
    - ``package_name`` — override the scaffold package name (greenfield).

    Safe by default (``live=False``): a local branch + diff, dry-run Jira, NO
    external writes. ``live=True`` creates a real Jira issue, pushes a branch,
    and opens a PR — and is **gated**: it requires ``confirm=true`` (an explicit
    human authorization, on top of the host's own tool-use confirmation).
    """
    if live and not confirm:
        raise PermissionError(
            "live=true creates a real Jira issue + PR; pass confirm=true to authorize the write."
        )
    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature

    try:
        result = await run_feature(
            source,
            intent_id=intent_id,
            repo=repo,
            language=language,
            layout_mode=layout,
            package_name=package_name,
            live=live,
            max_refine=max_refine,
        )
    except FeatureRunError as exc:
        return {"passed": False, "error": str(exc)}
    return {
        "passed": result.passed,
        "intent_id": result.intent_id,
        "issue_key": result.issue_key,
        "branch": result.branch,
        "files": result.files,
        "iterations": result.iterations,
        "grounding_chars": result.grounding_chars,
        "live": result.live,
        "pr_url": result.pr_url,
    }


def pkg_grounding(repo_path: str, spec_text: str) -> dict[str, Any]:
    """Existing-code context a repo's Product Knowledge Graph surfaces for a spec.

    Deterministic, read-only (no LLM): the real APIs/types the codegen would
    reuse, with ``file:line`` provenance. Empty for an unrelated/greenfield repo.
    """
    from orchestrator.sdlc.grounding import PKGCodegenGrounder

    grounder = PKGCodegenGrounder.from_repo(repo_path)
    context = grounder.context_for_spec({"title": spec_text, "summary": spec_text})
    return {"chars": len(context), "context": context}


def read_memory_bank(repo_path: str, section: str | None = None) -> dict[str, Any]:
    """Read a repo's committed memory bank (``memory-bank/``) — code-true project
    knowledge built by ``orchestrator understand``.

    Without ``section``: the section list + the index. With ``section`` (e.g.
    ``architecture`` / ``domain-model`` / ``conventions``): that section's markdown.
    Lets an external agent ground on the project's real structure + conventions.
    """
    from orchestrator.knowledge.access import read_memory_bank as _read

    return _read(repo_path, section)


# --- comprehension / graph-query tools --------------------------------------------------
# Thin façades over the same engine the `state` / `investigate` / `localize` / `regression`
# / `pkg` / `rca` CLI commands use. Each returns structured fields an assistant reasons over,
# plus a ``markdown`` rendering. ``repo_path`` is a local path OR a git URL (shallow-cloned
# behind the same SSRF/host-allow-list guard as the CLI). Read-only + deterministic + no
# credentials — except ``root_cause``'s opt-in ``use_llm`` enrichment.


@contextmanager
def _open_repo(repo_path: str) -> Iterator[Any]:
    """Yield a local repo ``Path`` for a local path OR a git URL (shallow-cloned + cleaned up),
    resolved through the same guard as the CLI's ``_repo_arg``."""
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import materialize_repo_source, resolve_repo_source

    source = resolve_repo_source(repo_path, Settings(repo_allow_any_local=True))
    with materialize_repo_source(source, log=lambda _m: None) as path:
        yield path


@contextmanager
def _repo_store(repo_path: str) -> Iterator[tuple[Any, Any]]:
    """Yield ``(FactStore, repo Path)`` for a local path or git URL."""
    from orchestrator.pkg import FactStore, load_or_extract

    with _open_repo(repo_path) as repo:
        yield FactStore(load_or_extract(repo)), repo


def _repos_note(repo: Any) -> dict[str, Any] | None:
    """A ``.spine/repos.yaml`` sitting in the repo we were pointed at — which this answer ignored.

    The single-repo path has no way to fail loudly here. Point a tool at a directory and it
    extracts that directory; there is no error to raise, and the result looks like every other
    answer. But if the project declares siblings, the honest reading of ``0 caller(s)`` is
    "none *in this repository*", and nothing in the payload would have said so.

    A note, not a behaviour change: it never switches the caller to the merged graph on their
    behalf, because which repositories an answer covers is the caller's decision to make.
    """
    from orchestrator.pkg.repos import RepoConfigError, find_repo_config, load_repo_config

    config = find_repo_config(repo)
    if config is None:
        return None
    try:
        declared = [key for key, _root in load_repo_config(config)]
    except RepoConfigError:
        # A config too broken to read is still evidence that this is a multi-repo project, and
        # staying quiet about it would be the exact silence this note exists to break.
        return {
            "config": str(config),
            "note": (
                f"{config} exists but could not be read. This answer covers one repository; "
                "fix the config and pass repos= to include the others."
            ),
        }
    return {
        "config": str(config),
        "declares": declared,
        "note": (
            f"This answer covers one repository. {config} declares {len(declared)} "
            f"({', '.join(declared)}) — pass repos='{config}' to include dependents in the others."
        ),
    }


def _with_repos_note(out: dict[str, Any], repo: Any, *, hint: bool) -> dict[str, Any]:
    """Attach the multi-repo note, unless the tool opted out or already said something."""
    if not hint or "error" in out:
        return out
    note = _repos_note(repo)
    if note is not None:
        out.setdefault("multi_repo_available", note)
    return out


def _in_repo(repo_path: str, fn: Callable[[Any], dict[str, Any]], *, hint: bool = True) -> dict[str, Any]:
    """Run ``fn(repo)`` inside a resolved repo; a bad path / URL returns ``{"error": …}``."""
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _open_repo(repo_path) as repo:
            return _with_repos_note(fn(repo), repo, hint=hint)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


def _in_repo_store(
    repo_path: str, fn: Callable[[Any, Any], dict[str, Any]], *, hint: bool = True
) -> dict[str, Any]:
    """Run ``fn(store, repo)`` inside a resolved repo; a bad path / URL returns ``{"error": …}``."""
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _repo_store(repo_path) as (store, repo):
            return _with_repos_note(fn(store, repo), repo, hint=hint)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


def _merged_store(repos: str) -> tuple[Any, Any, Any]:
    """``(FactStore, MergedFacts, RepoSet)`` for a ``.spine/repos.yaml`` — every declared
    repository extracted, scoped and merged into one graph with its declared joins applied."""
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.persistence import load_or_extract_repos
    from orchestrator.pkg.repos import load_repo_config

    repo_set = load_repo_config(repos)
    merged = load_or_extract_repos(repo_set)
    return FactStore(merged.batch), merged, repo_set


def _standing(merged: Any) -> dict[str, Any]:
    """The merged graph's standing, attached to every multi-repo answer.

    A merged graph assembled from a repository with uncommitted work cannot be reproduced at a
    commit, and looks identical to a clean one. The CLI prints that on stderr; a tool has to
    return it, or the caller quotes a number that nothing can reproduce.
    """
    return {
        "repos": [r.key for r in merged.repos],
        "reproducible": merged.trusted,
        "untrusted": list(merged.untrusted_keys),
    }


def _in_repos_store(repos: str, fn: Callable[[Any, Any], dict[str, Any]]) -> dict[str, Any]:
    """Run ``fn(store, merged)`` over a merged multi-repo graph; a bad config returns
    ``{"error": …}``."""
    from orchestrator.pkg.repos import RepoConfigError

    try:
        store, merged, _repo_set = _merged_store(repos)
    except RepoConfigError as exc:
        return {"error": str(exc)}
    out = fn(store, merged)
    out.setdefault("standing", _standing(merged))
    return out


def _cross_repo_reach(store: Any, node_id: str) -> list[dict[str, Any]]:
    """The symbols in *other* repositories that a change to this one reaches.

    This is the whole point of a merged graph. An HTTP handler with ``0 caller(s)`` is telling
    the truth — nothing in its own source calls it — and on its own that is the most dangerous
    answer the graph can give.
    """
    from orchestrator.pkg.scoping import unscope_id

    owner, _ = unscope_id(node_id)
    if not owner:
        return []
    out: list[dict[str, Any]] = []
    for node, hops in store.impact_of(node_id):
        repo, _unscoped = unscope_id(node.id)
        if repo and repo != owner:
            out.append(
                {
                    "id": node.id,
                    "repo": repo,
                    "hops": hops,
                    "where": str(node.provenance) if node.provenance else None,
                }
            )
    return out


def map_repo(repo_path: str, lens: str = "developer") -> dict[str, Any]:
    """A skim-first map of a repo: languages, components, **call-hotspots**, **test-coverage
    gaps**, and prioritized **recommendations**. Deterministic (no LLM). ``lens`` is
    ``developer`` (technical) or ``stakeholder`` (plain language). ``repo_path`` is a local path
    or a git URL."""
    if lens not in ("developer", "stakeholder"):
        return {"error": "lens must be 'developer' or 'stakeholder'"}

    def run(repo: Any) -> dict[str, Any]:
        from orchestrator.knowledge.current_state import load_current_state, render_current_state

        state, _batch = load_current_state(repo)
        return {
            "languages": state.languages,
            "counts": state.counts,
            "areas": state.areas,
            "files": state.modules,
            "has_call_graph": state.has_calls,
            "call_hotspots": [{"function": n, "call_sites": c} for n, c in state.call_hotspots],
            "coverage": {
                "tested_areas": state.tested_areas,
                "total_areas": state.areas,
                "largest_untested": [{"area": a, "types": c} for a, c in state.untested_top],
            },
            "recommendations": [{"priority": p, "action": t} for p, t in state.recommendations],
            "markdown": render_current_state(state, lens=lens),
        }

    return _in_repo(repo_path, run)


def blast_radius(repo_path: str = "", symbol: str = "", repos: str | None = None) -> dict[str, Any]:
    """ "What breaks if I change X" — a symbol's direct callers plus the cross-layer set a
    change ripples into (CALLS + IMPORTS + REFERENCES), each with ``file:line``. Deterministic.

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` to answer across every
    declared repository: each match then also reports the dependents a change reaches **in
    other repositories**, which is what a single-repo graph cannot see. An HTTP handler with
    zero callers in its own source is the case this exists for."""
    if not symbol:
        return {"error": "provide a symbol"}
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

    def run(store: Any, _ctx: Any) -> dict[str, Any]:
        matches = store.find(symbol)
        if not matches:
            return {"symbol": symbol, "found": False, "matches": []}
        out: list[dict[str, Any]] = []
        for node in matches[:5]:
            callers = store.callers_of(node.id)
            touched = store.touches(node.id)
            entry: dict[str, Any] = {
                "id": node.id,
                "kind": node.kind.value,
                "where": str(node.provenance) if node.provenance else None,
                "caller_count": len(callers),
                "callers": [{"id": cs.caller.id, "at": cs.at} for cs in callers[:25]],
                "touch_count": len(touched),
                "touches": [
                    {"id": t.id, "where": str(t.provenance) if t.provenance else None} for t in touched[:25]
                ],
            }
            if repos:
                reach = _cross_repo_reach(store, node.id)
                entry["cross_repo_count"] = len(reach)
                entry["cross_repo"] = reach[:25]
            out.append(entry)
        return {"symbol": symbol, "found": True, "matches": out, "markdown": _blast_markdown(out)}

    if repos:
        return _in_repos_store(repos, run)
    return _in_repo_store(repo_path, run)


def explain_symbol(repo_path: str, symbol: str) -> dict[str, Any]:
    """What a symbol is and how it connects: kind, location, who calls it, what it calls, and
    what it contains. Deterministic (no LLM)."""

    def run(store: Any, _repo: Any) -> dict[str, Any]:
        matches = store.find(symbol)
        if not matches:
            return {"symbol": symbol, "found": False, "matches": []}
        out = [
            {
                "id": node.id,
                "kind": node.kind.value,
                "name": node.name,
                "language": node.language,
                "where": str(node.provenance) if node.provenance else None,
                "called_by": [cs.caller.id for cs in store.callers_of(node.id)[:15]],
                "calls": [n.id for n in store.callees_of(node.id)[:15]],
                "contains": [n.id for n in store.children_of(node.id)[:25]],
            }
            for node in matches[:5]
        ]
        return {"symbol": symbol, "found": True, "matches": out}

    return _in_repo_store(repo_path, run)


def investigate(
    repo_path: str = "", title: str = "", problem: str = "", repos: str | None = None
) -> dict[str, Any]:
    """Where a ticket lands in the code: the real symbols to start from (``file:line`` + caller
    counts), the owning areas, and any committed ``episteme/`` knowledge. Deterministic (no LLM).

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` to research across every
    declared repository: a landing site then carries its repository and reports the dependents
    it has in others, so a ticket landing in two services says so. The ``episteme/`` section is
    omitted on a merged graph — a knowledge base belongs to one repository, and filling it from
    an arbitrary one would be the brief inventing an owner."""
    if not title and not problem:
        return {"error": "provide a ticket title (and optionally a problem description)"}
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

    def build(store: Any, root: Any) -> dict[str, Any]:
        from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

        inv = build_investigation(title, problem, store=store, root=root)
        return {
            "title": inv.title,
            "landing": [
                {
                    "name": h.name,
                    "kind": h.kind,
                    "where": h.where,
                    "callers": h.callers,
                    "cross_repo": h.cross_repo,
                    "module": h.module,
                }
                for h in inv.landing
            ],
            "areas": inv.areas,
            "has_knowledge": bool(inv.knowledge),
            "markdown": render_investigation_md(inv),
        }

    if repos:
        # `root=None`: see the docstring — a merged brief has no single owner for `episteme/`.
        return _in_repos_store(repos, lambda store, _merged: build(store, None))
    return _in_repo_store(repo_path, build)


def pkg_joins(config: str = ".spine/repos.yaml", mode: str = "check") -> dict[str, Any]:
    """Propose or check cross-repository joins. Read-only — it never writes a config.

    ``mode="propose"`` derives a ``joins:`` block from the evidence (calls a repo makes to paths
    it does not serve, matched against its neighbours' endpoints; shared tables; imports another
    repo defines), each candidate carrying the number of edges it would create — a join
    producing zero is noise.

    ``mode="check"`` reports what the declared joins could not place. This is the countermeasure
    for a quiet failure: a repository nobody listed is loud (no nodes, a visibly narrower
    graph), but a missing ``joins:`` entry looks exactly like two services that are not coupled,
    which reads as health."""
    if mode not in ("propose", "check"):
        return {"error": "mode must be 'propose' or 'check'"}
    from orchestrator.pkg.repos import RepoConfigError

    try:
        _store, merged, repo_set = _merged_store(config)
    except RepoConfigError as exc:
        return {"error": str(exc)}

    if mode == "propose":
        return {"mode": "propose", "standing": _standing(merged), **_joins_proposal(repo_set, merged)}
    return {"mode": "check", "standing": _standing(merged), **_joins_report(merged)}


def _joins_proposal(repo_set: Any, merged: Any) -> dict[str, Any]:
    from orchestrator.pkg.joins_propose import propose as propose_joins
    from orchestrator.pkg.joins_propose import unresolved_by_repo

    unresolved = unresolved_by_repo(repo_set)
    declared = {(j.kind, j.consumer, j.provider) for j in repo_set.joins}
    candidates = [
        {
            "kind": c.kind,
            "consumer": c.consumer,
            "provider": c.provider,
            "base": c.base,
            "edges": c.edges,
            "examples": list(c.examples),
            "already_declared": (c.kind, c.consumer, c.provider) in declared,
        }
        for c in propose_joins(merged.batch, unresolved)
    ]
    return {"candidates": candidates}


def _joins_report(merged: Any) -> dict[str, Any]:
    report = merged.joins
    if report is None:
        # Not the same as "everything joined". A config with no `joins:` block has nothing to
        # report against, and "0 unplaced" here would be the silence this tool exists to prevent.
        return {
            "declared": 0,
            "note": "no joins declared — run with mode='propose' to see what the evidence supports",
        }
    return {
        "declared": len(report.per_join),
        "joined": report.joined,
        "examined": report.examined,
        "recall": report.recall,
        "per_join": [{"join": k, "edges": v} for k, v in report.per_join],
        "unjoined": [
            {"repo": u.repo, "verb": u.verb, "path": u.path, "at": u.where, "reason": u.reason}
            for u in report.unjoined
        ],
    }


def localize(repo_path: str, trace: str) -> dict[str, Any]:
    """Resolve a stack trace / traceback to the repo symbols it names, pointing at the likely
    fault site and its callers. Deterministic (no LLM)."""
    if not trace.strip():
        return {"error": "provide a stack trace / traceback text"}

    def run(store: Any, _repo: Any) -> dict[str, Any]:
        from orchestrator.sdlc.localize import localize_trace, render_localization_md

        loc = localize_trace(trace, store=store)
        return {
            "exception": loc.exception,
            "grounded": loc.grounded,
            "fault": (
                {"func": loc.fault.func, "where": loc.fault.where, "id": loc.fault.node_id}
                if loc.fault
                else None
            ),
            "frames": [
                {
                    "func": f.func,
                    "trace_at": f"{f.file}:{f.line}",
                    "resolved": f.resolved,
                    "id": f.node_id,
                    "where": f.where,
                }
                for f in loc.frames
            ],
            "callers": loc.callers,
            "markdown": render_localization_md(loc),
        }

    return _in_repo_store(repo_path, run)


def regression_gaps(repo_path: str, symbol: str = "", trace: str = "") -> dict[str, Any]:
    """Blast-radius test-coverage gaps for a change: the production symbols a change to
    ``symbol`` (or the fault site in ``trace``) reaches that **no test covers**. Deterministic."""
    if not symbol and not trace.strip():
        return {"error": "provide a symbol name or a stack trace"}

    def run(store: Any, _repo: Any) -> dict[str, Any]:
        from orchestrator.sdlc.coverage import (
            build_regression_plan,
            render_regression_plan_md,
            resolve_target,
        )

        if trace.strip():
            from orchestrator.sdlc.localize import localize_trace

            loc = localize_trace(trace, store=store)
            target_id = loc.fault.node_id if (loc.fault and loc.fault.node_id) else None
        else:
            target_id = resolve_target(store, symbol)
        if not target_id:
            return {"target": symbol or "(trace)", "found": False}
        plan = build_regression_plan(store, target_id)
        return {
            "target": plan.target,
            "found": True,
            "target_covered": plan.target_covered,
            "call_graph_available": plan.call_graph_available,
            "impacted_count": len(plan.impacted),
            "uncovered": [{"name": i.name, "where": i.where} for i in plan.impacted if not i.covered],
            "covering_tests": plan.covering_tests,
            "truncated": plan.truncated,
            "markdown": render_regression_plan_md(plan),
        }

    return _in_repo_store(repo_path, run)


async def root_cause(repo_path: str, bug: str, use_llm: bool = False) -> dict[str, Any]:
    """A grounded root-cause report for a bug (a stack trace, an error message, or a
    description): the fault site, ranked root-cause **hypotheses** with evidence, the regression
    surface a fix must cover, and a scoped fix approach. **Deterministic by default** (no LLM,
    no credentials); ``use_llm=true`` opts into LLM-enriched hypotheses (needs a model). Stops
    at analysis — it never changes code."""
    if not bug.strip():
        return {"error": "provide the bug: a stack trace, an error message, or a description"}
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError
    from orchestrator.sdlc.rca import build_rca, render_rca_md

    client: Any = None
    if use_llm:
        from orchestrator.core.env import load_local_env
        from orchestrator.core.llm import LiteLLMClient
        from orchestrator.sdlc.codegen import resolve_codegen_model

        load_local_env()
        if not resolve_codegen_model():
            return {
                "error": "use_llm=true needs a model — set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL)."
            }
        client = LiteLLMClient()

    try:
        with _repo_store(repo_path) as (store, repo):
            report = await build_rca(bug, store=store, root=repo, llm=client)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}
    return {
        "fault_site": report.fault_site,
        "used_llm": bool(client),
        "hypotheses": [{"claim": h.claim, "evidence": list(h.evidence)} for h in report.hypotheses],
        "regression_surface": report.regression_surface,
        "fix_approach": report.fix_approach,
        "markdown": render_rca_md(report),
    }


async def sdlc_plan(repo_path: str, spec: dict[str, Any], persist_plan: bool = True) -> dict[str, Any]:
    """The twelve-section **build document** for one ticket: requirement, intent, root cause,
    what the graph knows, blast radius, design, files, acceptance criteria, the codegen prompt,
    cost and confidence. **Deterministic — no LLM, no credentials, nothing spent.** Every
    section says where it came from. Hand it a ``spec`` object (title, summary,
    acceptance_criteria, and optionally met_criteria mapping a criterion already satisfied by
    existing code to the evidence). Stops at the document — it never changes code."""
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.sdlc.spec_file import SpecFileError, validate_spec

    if not isinstance(spec, dict):
        return {"error": f"spec must be an object, got {type(spec).__name__}"}
    try:
        # The same validator the CLI uses. A host's model drafts this spec, and
        # ``FeatureSpec`` forbids extra keys — so an invented field is refused here, naming
        # the valid ones, rather than rendering a document from a spec that is wrong.
        resolved = validate_spec(spec, where="spec")
    except SpecFileError as exc:
        # Returned rather than raised: the caller is a model that can read this and fix the
        # spec, which a stack trace on the host's side does not let it do.
        return {"error": str(exc), "valid_fields": sorted(FeatureSpec.model_fields)}

    from orchestrator.sdlc.builddoc import build_plan, load_approval, load_journey, persist

    intent = str(resolved.get("intent_id") or "spec")

    async def run(repo: Any) -> dict[str, Any]:
        document = await build_plan(
            resolved,
            root=repo,
            approval=load_approval(intent, root=repo),
            journey=load_journey(intent, root=repo),
        )
        out: dict[str, Any] = {"intent_id": intent, "document": document}
        if persist_plan:
            written, superseded = persist(document, intent_id=intent, root=repo)
            out["path"] = str(written)
            if superseded is not None:
                out["superseded"] = str(superseded)
        return out

    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _open_repo(repo_path) as repo:
            return await run(repo)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


def sdlc_approve(
    repo_path: str,
    intent_id: str,
    decided_by: str = "",
    note: str = "",
    reject: bool = False,
) -> dict[str, Any]:
    """Record that a **human** read a build document and decided. Binds the decision to a
    digest of the document body, so a plan that changes afterwards reads as *stale* rather
    than silently still approved — and `sdlc autorun` refuses to build without a current one.
    Needs `sdlc_plan` to have produced the document first. ``decided_by`` defaults to the
    repo's git identity; a decision nobody is named for is not recorded."""
    import datetime as _dt

    from orchestrator.sdlc.builddoc import (
        PlanApproval,
        decided_by_default,
        derived_at,
        plan_digest,
        plan_dir,
        save_approval,
    )

    def run(repo: Any) -> dict[str, Any]:
        document = plan_dir(repo) / f"{intent_id}-build.md"
        if not document.is_file():
            return {
                "error": f"no plan at {document} — run sdlc_plan for {intent_id} first",
            }
        # The tool cannot invent an approver. A host may know who its user is, but this
        # process does not, and an approval attributed to nobody is a rumour.
        who = decided_by or decided_by_default(repo)
        if not who:
            return {"error": "cannot tell who is approving — pass decided_by"}
        approval = PlanApproval(
            intent_id=intent_id,
            decision="REJECTED" if reject else "APPROVED",
            decided_by=who,
            decided_at=_dt.date.today().isoformat(),
            digest=plan_digest(document.read_text(encoding="utf-8")),
            commit=derived_at(repo),
            note=note,
        )
        return {
            "intent_id": intent_id,
            "decision": approval.decision,
            "decided_by": who,
            "decided_at": approval.decided_at,
            "path": str(save_approval(approval, root=repo)),
        }

    # No multi-repo note: an approval is a decision about one repository's plan, and a
    # nudge toward a merged graph here would be answering a question nobody asked.
    return _in_repo(repo_path, run, hint=False)


def docs_for(repo_path: str, symbol: str = "") -> dict[str, Any]:
    """Which docs describe the code — the doc-ingestion surface. With a ``symbol``, the doc pages
    that **MENTION** it (grounded to the repo's docs). Without one, a doc-coverage summary: how many
    docs are ingested, how many symbols they name, and the top **potential drift** (doc claims the
    graph can't resolve — renamed/removed code). Deterministic (no LLM). ``repo_path`` is a local
    path or a git URL."""

    def run(repo: Any) -> dict[str, Any]:
        from orchestrator.knowledge.current_state import load_current_state
        from orchestrator.pkg import FactStore

        state, batch = load_current_state(repo)
        if not state.docs:
            return {"repo": str(repo), "docs": 0, "note": "no docs ingested (no .md/.rst/.txt/.pdf found)"}
        if symbol:
            store = FactStore(batch)
            matches = store.find(symbol)
            if not matches:
                return {"symbol": symbol, "found": False, "matches": []}
            out: list[dict[str, Any]] = []
            lines = [f"# Docs describing `{symbol}`", ""]
            for node in matches[:5]:
                doc_names = [d.name for d in store.docs_for(node.id)]
                where = str(node.provenance) if node.provenance else None
                out.append({"id": node.id, "kind": node.kind.value, "where": where, "docs": doc_names})
                loc = f" @ {where}" if where else ""
                shown = ", ".join(f"`{d}`" for d in doc_names) or "_no docs mention this symbol_"
                lines += [f"### `{node.id}` — {node.kind.value}{loc}", f"- {shown}"]
            return {"symbol": symbol, "found": True, "matches": out, "markdown": "\n".join(lines)}

        pct = (
            round(100 * state.documented_symbols / state.coverable_symbols) if state.coverable_symbols else 0
        )
        drift_top = [{"claim": c, "doc": d} for c, d in state.doc_drift_top]
        lines = [
            "# Documentation coverage",
            "",
            f"- **{state.docs} docs** ingested; they name **{state.documented_symbols} of "
            f"{state.coverable_symbols} symbols** ({pct}% coverage).",
        ]
        if state.doc_drift_total:
            lines.append(
                f"- **{state.doc_drift_total} potential drift** — doc claims the graph can't resolve."
            )
            lines += ["", "| Doc claims… | …in |", "|---|---|"]
            lines += [f"| `{d['claim']}` | {d['doc']} |" for d in drift_top]
        return {
            "symbol": None,
            "docs": state.docs,
            "documented_symbols": state.documented_symbols,
            "coverable_symbols": state.coverable_symbols,
            "coverage_pct": pct,
            "drift_total": state.doc_drift_total,
            "drift_top": drift_top,
            "markdown": "\n".join(lines),
        }

    return _in_repo(repo_path, run)


def _blast_markdown(matches: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in matches:
        lines.append(f"### `{m['id']}` — {m['kind']}" + (f" @ {m['where']}" if m["where"] else ""))
        lines.append(
            f"- **Called by ({m['caller_count']}):** " + ", ".join(c["id"] for c in m["callers"][:10])
        )
        lines.append(f"- **Touches ({m['touch_count']}):** " + ", ".join(t["id"] for t in m["touches"][:10]))
        # Only on a merged graph. Rendered even at zero: "no dependents in other repos" is an
        # answer, and its absence would read the same as never having looked.
        if "cross_repo_count" in m:
            reached = ", ".join(f"{c['repo']}·`{c['id']}`" for c in m["cross_repo"][:10]) or "none"
            lines.append(f"- **Dependents in other repos ({m['cross_repo_count']}):** {reached}")
    return "\n".join(lines)


# ---- job-style autonomous run (the full gated SDLC workflow) ----------------
#
# Unlike ``sdlc_feature`` (one intent, runs to completion in a single call), the
# autonomous ``sdlc run`` is long and pauses at two human gates, so it can't be a
# single blocking tool call. These four tools drive it as a *job*: start → poll
# status → decide each gate → fetch result. They need Mode-B infra (a running
# Temporal worker on ``sdlc-tasks`` + Postgres).


async def sdlc_start_run(
    source: str,
    create_jira: bool = False,
    confirm: bool = False,
    max_features: int = 0,
    max_parallel: int = 2,
) -> dict[str, Any]:
    """Start the autonomous, gated SDLC workflow. Returns a run id immediately.

    Safe by default (``create_jira=False``): dry-run Jira, no external writes.
    ``create_jira=True`` writes real Jira issues and is **gated** — it requires
    ``confirm=true``. The run then pauses at two gates (``intents`` then
    ``merge``); poll ``sdlc_run_status`` and act with ``sdlc_decide_gate``.
    ``max_features=0`` means no cap.
    """
    if create_jira and not confirm:
        raise PermissionError(
            "create_jira=true writes real Jira issues; pass confirm=true to authorize the write."
        )
    from orchestrator.sdlc.run_control import start_run

    return await start_run(
        source=source, create_jira=create_jira, max_features=max_features, max_parallel=max_parallel
    )


async def sdlc_run_status(sdlc_id: str) -> dict[str, Any]:
    """Poll a run: Temporal workflow status + the gate (if any) awaiting a decision."""
    from orchestrator.sdlc.run_control import run_status

    return await run_status(sdlc_id)


async def sdlc_decide_gate(
    sdlc_id: str,
    gate: str,
    action: str,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Decide a pending gate so the run can continue (or stop).

    ``gate`` is ``"intents"``, ``"merge"``, or a raw approval id. ``action`` is
    ``"approve"``, ``"reject"``, or ``"modify_input"``. The decision is recorded
    (with audit) and signaled to the workflow.
    """
    from orchestrator.sdlc.run_control import decide_gate

    return await decide_gate(sdlc_id, gate, action, rationale=rationale)


async def sdlc_run_result(sdlc_id: str) -> dict[str, Any]:
    """Fetch a run's final result once it has COMPLETED (status only otherwise)."""
    from orchestrator.sdlc.run_control import run_result

    return await run_result(sdlc_id)


_TOOLS = (
    doctor,
    ingest_preview,
    pkg_grounding,
    read_memory_bank,
    # comprehension / graph-query (read-only; deterministic, except root_cause's opt-in LLM)
    map_repo,
    blast_radius,
    explain_symbol,
    investigate,
    localize,
    regression_gaps,
    root_cause,
    # multi-repo: one graph across several repositories (`.spine/repos.yaml`)
    pkg_joins,
    sdlc_plan,
    sdlc_approve,
    docs_for,
    # gated codegen / run control
    sdlc_feature,
    sdlc_start_run,
    sdlc_run_status,
    sdlc_decide_gate,
    sdlc_run_result,
)


def _import_server_class() -> Any:
    """The SDK's server class — ``MCPServer`` since v2 (was ``mcp.server.fastmcp.FastMCP``).

    It keeps the same surface this plugin uses: ``.tool()`` to register, ``.run()`` to
    serve. What moved is transport configuration — see :class:`HttpServer`.
    """
    try:
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "The orchestrator MCP plugin needs the 'mcp' extra: pip install 'synaptixs-spine[mcp]'"
        ) from exc
    return MCPServer


def _register_tools(server: Any) -> Any:
    for fn in _TOOLS:
        server.tool()(fn)
    return server


def build_server() -> Any:
    """Build the FastMCP server with the orchestrator's plugin tools registered.

    Stdio transport (Phase A): the local plugin a desktop host launches as a
    subprocess. For the remote HTTP transport see ``build_http_server``.
    """
    return _register_tools(_import_server_class()("synaptixs-spine"))


@dataclass(frozen=True)
class HttpServer:
    """A built server plus the transport settings the SDK takes at run time.

    v1 accepted ``host``/``port``/``streamable_http_path``/``stateless_http`` on the
    constructor, so a built server was self-contained. v2 moved them to
    ``run_streamable_http_async``, so they have to travel with it.
    """

    server: Any
    transport: dict[str, Any]

    def run(self) -> None:
        self.server.run("streamable-http", **self.transport)


def build_http_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/mcp",
    stateless: bool = False,
    allow_unauthenticated: bool = False,
) -> HttpServer:
    """Build the FastMCP server for the remote ``streamable-http`` transport (Phase C).

    Auth is derived from env (``orchestrator.plugin.auth.build_auth_from_env``):
    a verified bearer token (OAuth introspection or a static secret). Binding a
    non-loopback host **without** auth is refused unless ``allow_unauthenticated``
    is set — a public, unauthenticated SDLC control plane is never a default.
    """
    from orchestrator.plugin.auth import build_auth_from_env

    server_cls = _import_server_class()
    auth_settings, verifier = build_auth_from_env()

    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if auth_settings is None and not is_loopback and not allow_unauthenticated:
        raise RuntimeError(
            f"Refusing to serve on {host!r} without auth. Configure a bearer token "
            "(ORCHESTRATOR_MCP_TOKEN or ORCHESTRATOR_MCP_INTROSPECTION_URL), bind to "
            "127.0.0.1, or pass --allow-unauthenticated for a trusted private network."
        )

    # SDK v2 takes transport settings at *run* time, not on the constructor — only auth
    # stays on the server object. Carrying them together keeps the binding rule enforced
    # here (where the refusal above lives) rather than at the call site.
    server = server_cls("synaptixs-spine", auth=auth_settings, token_verifier=verifier)
    return HttpServer(
        server=_register_tools(server),
        transport={
            "host": host,
            "port": port,
            "streamable_http_path": path,
            "stateless_http": stateless,
        },
    )


__all__ = [
    "HttpServer",
    "blast_radius",
    "build_http_server",
    "build_server",
    "doctor",
    "explain_symbol",
    "ingest_preview",
    "investigate",
    "localize",
    "map_repo",
    "pkg_grounding",
    "read_memory_bank",
    "regression_gaps",
    "root_cause",
    "sdlc_decide_gate",
    "sdlc_feature",
    "sdlc_run_result",
    "sdlc_run_status",
    "sdlc_start_run",
]
