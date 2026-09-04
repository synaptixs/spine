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

**The free half of the back half** — ``understand_repo`` (build or check the ``episteme/``
bank; the one write, under the repo), ``profile_repo``, ``design_change`` and
``sdlc_baseline``. Deterministic, no credentials, apart from ``design_change``'s opt-in
``use_llm``. ``state`` has no tool of its own because ``map_repo`` already is it.

**The gated half of the back half** — ``sdlc_address_review`` (push a fix for review
comments to the PR branch), ``sdlc_complete`` (transition the merged PR's ticket),
``sdlc_remediate`` (drift report → remediation runs; ``live`` opens PRs) and ``audit_repo``
(a persona reads the repo on a model, writes nothing). The first two have no local mode and
need ``confirm=true`` on every call; ``remediate`` gates ``live`` like ``sdlc_feature``.

**Operator tools** — ``registry_runs`` / ``registry_approvals`` / ``registry_decide`` /
``registry_trace``. "What is running, what is waiting on me", over HTTP to the registry
(``orchestrator up``) — the successor to the removed terminal UI. Observing is read-only;
deciding a gate is destructive, because a rejection ends a run.

An assistant should work down the tiers, not up: comprehend, then plan and get the plan
approved, then build. A run that starts at tier 3 is one nobody reviewed.

**The tiers are metadata, not only prose.** Every tool is registered with MCP
``ToolAnnotations`` derived from its tier (``_TIER`` → ``tool_annotations``): read-only,
destructive, idempotent, open-world. A host uses those for its confirmation UX, so the
tier model is enforced by the client rather than by a docstring a model may not read.
A tool without a tier does not register — ``_register_tools`` raises, and a test says so
before a host does.

**And the tiers are scopes, on HTTP.** Each tier names the OAuth scope a bearer token needs
(``spine:read`` / ``spine:plan`` / ``spine:run``, in ``orchestrator.plugin.auth``), and every
registered tool is wrapped in a guard that reads the verified token at call time and
refuses — naming the scope it needed and the scopes the token has — when it is missing.
The SDK only checks scopes server-wide, so the per-tool check has to live here. Over stdio
there is no token, and the guard passes: a local subprocess a host launched already holds
the user's ``.env``.

**Prompts and resources, for hosts that are not Claude Code** (``plugin/prompts.py``,
``plugin/resources.py``). The ``understand-codebase`` skill's "which tool, in which order"
ships as five MCP prompts; the committed ``episteme/`` bank, the build documents and the
state report are readable as ``spine://`` resources. Both register beside the tools.

Tool *implementations* are module-level functions (unit-testable without the ``mcp``
extra); ``build_server`` lazy-imports the SDK's server class and registers them.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.plugin.auth import SCOPE_PLAN, SCOPE_READ, SCOPE_RUN
from orchestrator.plugin.progress import Reporter

# The SDK injects its ``Context`` into a parameter annotated with this class and keeps it
# out of the input schema — but it resolves the annotation through this module's globals
# at registration, so the name must exist at runtime. Without the ``mcp`` extra (the tool
# implementations stay importable without it) it aliases to ``Any`` and nothing changes.
try:
    from mcp.server.mcpserver import Context
except ImportError:  # pragma: no cover - only without the extra
    Context = Any  # type: ignore[assignment,misc]


def doctor() -> dict[str, Any]:
    """Report environment readiness (LLM provider, Confluence/Jira, MCP, …) and which
    install is answering: ``server`` carries the package version, the interpreter, the
    MCP SDK version and the extras present — so a stale console script on a host's PATH
    is visible from the host, not just from a shell."""
    from orchestrator.doctor import run_env_checks, server_identity

    results = run_env_checks()
    return {
        "all_passed": all(r.passed for r in results),
        "server": server_identity(),
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
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build ONE intent end to end: spec → grounded codegen → tests → branch. Reports
    progress per stage (spec, layout, design, implement, tests, refine, judge, PR) when the
    host asks for it.

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

    progress = Reporter(ctx)
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
            log=progress.as_log(),
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


def explain_symbol(repo_path: str = "", symbol: str = "", repos: str | None = None) -> dict[str, Any]:
    """What a symbol is and how it connects: kind, location, who calls it, what it calls, and
    what it contains. Deterministic (no LLM).

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` to explain it across every
    declared repository: each match then says which repository it lives in and lists the
    symbols in *other* repositories a change to it reaches — the callers a single-repo graph
    cannot see."""
    if not symbol:
        return {"error": "provide a symbol"}
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

    def run(store: Any, _repo: Any) -> dict[str, Any]:
        matches = store.find(symbol)
        if not matches:
            return {"symbol": symbol, "found": False, "matches": []}
        out: list[dict[str, Any]] = []
        for node in matches[:5]:
            entry: dict[str, Any] = {
                "id": node.id,
                "kind": node.kind.value,
                "name": node.name,
                "language": node.language,
                "where": str(node.provenance) if node.provenance else None,
                "called_by": [cs.caller.id for cs in store.callers_of(node.id)[:15]],
                "calls": [n.id for n in store.callees_of(node.id)[:15]],
                "contains": [n.id for n in store.children_of(node.id)[:25]],
            }
            if repos:
                entry["repo"] = _repo_of_node(node)
                reach = _cross_repo_reach(store, node.id)
                entry["cross_repo_count"] = len(reach)
                entry["cross_repo"] = reach[:25]
            out.append(entry)
        return {"symbol": symbol, "found": True, "matches": out}

    if repos:
        return _in_repos_store(repos, run)
    return _in_repo_store(repo_path, run)


def _repo_of_node(node: Any) -> str:
    """Which repository a merged-graph node belongs to: its provenance, else its scoped id."""
    from orchestrator.pkg.scoping import unscope_id

    return (node.provenance.repo if node.provenance else "") or unscope_id(node.id)[0]


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


def localize(repo_path: str = "", trace: str = "", repos: str | None = None) -> dict[str, Any]:
    """Resolve a stack trace / traceback to the repo symbols it names, pointing at the likely
    fault site and its callers. Deterministic (no LLM).

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` for a trace that crosses
    services: each resolved frame then says which repository it landed in, and a frame whose
    path exists in more than one repository is reported as **ambiguous** with the candidates —
    the first match does not win silently."""
    if not trace.strip():
        return {"error": "provide a stack trace / traceback text"}
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

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
                    **({"repo": f.repo, "candidates": f.candidates} if repos else {}),
                }
                for f in loc.frames
            ],
            "callers": loc.callers,
            **(
                {
                    "ambiguous_frames": [
                        {"trace_at": f"{f.file}:{f.line}", "resolved": f.node_id, "also": f.candidates}
                        for f in loc.frames
                        if f.candidates
                    ]
                }
                if repos
                else {}
            ),
            "markdown": render_localization_md(loc),
        }

    if repos:
        return _in_repos_store(repos, run)
    return _in_repo_store(repo_path, run)


def regression_gaps(
    repo_path: str = "", symbol: str = "", trace: str = "", repos: str | None = None
) -> dict[str, Any]:
    """Blast-radius test-coverage gaps for a change: the production symbols a change to
    ``symbol`` (or the fault site in ``trace``) reaches that **no test covers**. Deterministic.

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` to answer across every
    declared repository: each impacted symbol then names its repository, and ``uncovered_elsewhere``
    is the headline — a change reaching a *different* service that has no test for it."""
    if not symbol and not trace.strip():
        return {"error": "provide a symbol name or a stack trace"}
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

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
        out: dict[str, Any] = {
            "target": plan.target,
            "found": True,
            "target_covered": plan.target_covered,
            "call_graph_available": plan.call_graph_available,
            "impacted_count": len(plan.impacted),
            "uncovered": [
                {"name": i.name, "where": i.where, **({"repo": i.repo} if repos else {})}
                for i in plan.impacted
                if not i.covered
            ],
            "covering_tests": plan.covering_tests,
            "truncated": plan.truncated,
            "markdown": render_regression_plan_md(plan),
        }
        if repos:
            home = _repo_of_node(store.node(target_id)) if store.node(target_id) else ""
            out["target_repo"] = home
            out["uncovered_elsewhere"] = [
                {"name": i.name, "where": i.where, "repo": i.repo}
                for i in plan.impacted
                if not i.covered and i.repo and i.repo != home
            ]
        return out

    if repos:
        return _in_repos_store(repos, run)
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


def docs_for(repo_path: str = "", symbol: str = "", repos: str | None = None) -> dict[str, Any]:
    """Which docs describe the code — the doc-ingestion surface. With a ``symbol``, the doc pages
    that **MENTION** it (grounded to the repo's docs). Without one, a doc-coverage summary: how many
    docs are ingested, how many symbols they name, and the top **potential drift** (doc claims the
    graph can't resolve — renamed/removed code). Deterministic (no LLM). ``repo_path`` is a local
    path or a git URL.

    Pass ``repos`` (a ``.spine/repos.yaml``) instead of ``repo_path`` to ask every declared
    repository — **each on its own**, keyed by repository. Docs are not merged across repos: a
    document describes the repository it lives in, and binding one repo's docs to another's
    symbols by name would be a false edge. Each entry carries its own ``reproducible`` flag."""
    if bool(repo_path) == bool(repos):
        return {"error": "provide exactly one of repo_path or repos"}

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

    if repos:
        return _per_repo(repos, run)
    return _in_repo(repo_path, run)


def _per_repo(repos: str, fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """Run ``fn(root)`` for every repository a ``repos.yaml`` declares, keyed by repository —
    for questions that do not merge (docs describe their own repo). Each entry says whether
    that checkout is reproducible, the way a merged graph's ``standing`` does for all of them."""
    from orchestrator.pkg.persistence import repo_state
    from orchestrator.pkg.repos import RepoConfigError, load_repo_config

    try:
        repo_set = load_repo_config(repos)
    except RepoConfigError as exc:
        return {"error": str(exc)}
    out: dict[str, Any] = {}
    for key, root in repo_set.roots:
        sha, dirty = repo_state(root)
        entry = fn(root)
        entry["reproducible"] = sha is not None and not dirty
        out[key] = entry
    return {
        "repos": out,
        "standing": {
            "repos": [k for k, _ in repo_set.roots],
            "reproducible": all(v["reproducible"] for v in out.values()),
            "untrusted": [k for k, v in out.items() if not v["reproducible"]],
        },
        "markdown": "\n\n".join(
            f"## {key}\n\n{v.get('markdown') or v.get('note') or ''}" for key, v in out.items()
        ),
    }


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


# ---- the free half of the back half: understand, profile, design, baseline -----------
#
# Commands that existed only in the CLI (gap 5 of docs/specs/mcp-plugin-surface.md). These
# four are deterministic and need no credentials; the gated half — address-review,
# complete, remediate, audit — is its own step, because each of those spends money or
# writes outside the repo. ``state`` is not here because ``map_repo`` already is it.

#: The three pages a reader starts from. Named rather than "N files written", the way the
#: CLI reports it — an index is not a place to start.
_BANK_ENTRY_PAGES = ("README.md", "architecture.md", "domain-model.md")


async def understand_repo(
    repo_path: str,
    check: bool = False,
    refresh: bool = False,
    out: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Build a repo's ``episteme/`` knowledge base — or, with ``check=true``, verify the
    committed one still matches the code. **Deterministic, no LLM, no credentials**: the
    same pages ``orchestrator understand`` writes, so an assistant can bootstrap a repo
    that has no bank yet, then ``read_memory_bank`` it. Writes only under ``episteme/``
    (or ``out``). ``check`` writes nothing and reports the pages that are missing, stale
    (the code moved on) or orphaned (describing code that is gone). ``refresh`` re-extracts
    the graph instead of using the commit cache. A **build on a git URL is refused** unless
    ``out`` is an absolute directory — the clone vanishes, and a bank written into it with
    it; ``check`` on a URL is fine. Returns the three entry pages and the counts, not every
    path."""
    from orchestrator.knowledge.understand import build_memory_bank, check_memory_bank
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import RepoSourceError, resolve_repo_source

    try:
        source = resolve_repo_source(repo_path, Settings(repo_allow_any_local=True))
    except RepoSourceError as exc:
        return {"error": str(exc)}
    out_dir = Path(out).expanduser() if out else None
    if source.kind == "git" and not check and (out_dir is None or not out_dir.is_absolute()):
        return {
            "error": "understand_repo writes into the repo, and a git URL is a clone that vanishes — "
            "pass a local repo_path, or out=<absolute directory> to keep the bank.",
        }

    progress = Reporter(ctx, phases=("extract", "understand"))
    await progress.step(1, 2, "[extract] extracting the graph and rendering the pages")

    def run(repo: Any) -> dict[str, Any]:
        if check:
            report = check_memory_bank(repo, out_dir=out_dir, refresh=refresh, log=progress.as_log())
            return {
                "ok": report.ok,
                "absent": report.absent,
                "bank_dir": str(report.bank_dir),
                "missing": list(report.missing),
                "stale": list(report.stale),
                "orphaned": list(report.orphaned),
                "commit": report.commit,
                "dirty": report.dirty,
                "summary": report.summary_line(),
            }
        result = build_memory_bank(repo, out_dir=out_dir, refresh=refresh, log=progress.as_log())
        files = list(result.get("files") or [])
        return {
            "dir": result["dir"],
            "greenfield": result.get("greenfield", False),
            "entry_pages": [f for f in _BANK_ENTRY_PAGES if f in files],
            "files_written": len(files),
            "summary": result.get("summary") or {},
            "profile": result.get("profile") or {},
            "markdown": f"Wrote {len(files)} pages to `{result['dir']}`. Start with "
            + ", ".join(f"`{f}`" for f in _BANK_ENTRY_PAGES if f in files)
            + ". Read them with `read_memory_bank`.",
        }

    return _in_repo(repo_path, run, hint=False)


def profile_repo(repo_path: str, intent: str | None = None) -> dict[str, Any]:
    """Profile a project: languages, framework, database and migrations, test runner, and
    — given an ``intent`` title — the task type Spine would classify it as. Read-only,
    deterministic; the same profile the catalog uses to pick skills. ``repo_path`` is a
    local path or a git URL."""

    def run(repo: Any) -> dict[str, Any]:
        from orchestrator.catalog import ProjectProfile

        prof = ProjectProfile.from_repo(repo, intent_title=intent)
        data = prof.to_dict()
        lines = [
            f"languages: {', '.join(sorted(prof.languages)) or '(none detected)'}",
            f"framework: {prof.framework or '-'}",
            f"database: {'yes' if prof.has_db else 'no'} "
            f"(migrations: {'yes' if prof.has_migrations else 'no'})",
            f"test runner: {prof.test_runner or '-'}",
            f"task type: {prof.task_type}",
        ]
        return {**data, "markdown": "\n".join(f"- {ln}" for ln in lines)}

    return _in_repo(repo_path, run, hint=False)


async def design_change(repo_path: str, spec: dict[str, Any], use_llm: bool = False) -> dict[str, Any]:
    """A grounded design for one feature: the spec × the knowledge graph → an approach
    anchored to the repo's real structure, its **blast radius** (modules touched, who
    depends on them, call hotspots) and any **unverified references** (paths the spec names
    that the graph does not have). Takes the same ``spec`` object as ``sdlc_plan``
    (``intent_id``, ``title``, ``summary``, ``acceptance_criteria``), validated the same way.
    **Deterministic by default**; ``use_llm=true`` lets a model write the prose (needs
    ``ORCHESTRATOR_INTAKE_MODEL``). Returns the design and its markdown — it never writes.
    For the full twelve-section build document, use ``sdlc_plan``."""
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.sdlc.spec_file import SpecFileError, validate_spec

    if not isinstance(spec, dict):
        return {"error": f"spec must be an object, got {type(spec).__name__}"}
    try:
        resolved = validate_spec(spec, where="spec")
    except SpecFileError as exc:
        return {"error": str(exc), "valid_fields": sorted(FeatureSpec.model_fields)}

    client: Any = None
    if use_llm:
        from orchestrator.core.env import load_local_env
        from orchestrator.sdlc.codegen import resolve_codegen_model

        load_local_env()
        if not resolve_codegen_model():
            return {
                "error": "use_llm=true needs a model — set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL)."
            }
        from orchestrator.core.llm import LiteLLMClient

        client = LiteLLMClient()

    async def run(repo: Any) -> dict[str, Any]:
        from orchestrator.pkg import FactStore, load_or_extract
        from orchestrator.pkg.overview import build_overview
        from orchestrator.sdlc.design import produce_design, render_design_md

        batch = load_or_extract(repo)
        design = await produce_design(
            resolved,
            overview=build_overview(batch),
            memory_bank=_design_bank(repo),
            store=FactStore(batch),
            llm=client,
            root=repo,
        )
        unverified = (design.get("blast_radius") or {}).get("unverified_references") or []
        return {
            "title": resolved.get("title"),
            "design": design,
            "unverified_references": unverified,
            "used_llm": client is not None,
            "markdown": render_design_md(resolved, design),
        }

    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _open_repo(repo_path) as repo:
            return await run(repo)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


def _design_bank(repo: Path) -> dict[str, str]:
    """The committed ``episteme/`` pages a design draws conventions from, when present —
    the same three the CLI's ``design`` reads."""
    import contextlib

    from orchestrator.knowledge.understand import existing_bank_dir

    out: dict[str, str] = {}
    with contextlib.suppress(Exception):
        bank = existing_bank_dir(repo)
        for name in ("domain-model.md", "tech-context.md", "conventions.md"):
            page = bank / name
            if page.exists():
                out[name] = page.read_text(encoding="utf-8")
    return out


def sdlc_baseline(repo_path: str) -> dict[str, Any]:
    """Score the run agent against a corpus of tickets whose right answer is known, and
    summarize the durable run records. **Deterministic and free**: the validity gate reads
    each ticket and this repo's real graph; run metrics are observations of what ran.
    False refusals and missed refusals are counted separately — one accuracy number would
    let each hide behind the other. ``repo_path`` is a local path or a git URL."""

    def run(repo: Any) -> dict[str, Any]:
        from orchestrator.evals.agent_corpus import render_report, score_gate, score_runs
        from orchestrator.pkg import FactStore, load_or_extract
        from orchestrator.sdlc.runstate import RunStore

        gate = score_gate(FactStore(load_or_extract(repo)))
        runs = score_runs(RunStore().all())
        return {
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
            "markdown": render_report(gate, runs),
        }

    return _in_repo(repo_path, run, hint=False)


# ---- the gated half of the back half: address-review, complete, remediate, audit ------
#
# Each spends money or writes outside the repo — a push to a PR branch, a ticket
# transition, opened PRs, a model run — so each is run scope. The two that have no local
# mode (address-review pushes, complete transitions) need ``confirm=true`` on every call;
# ``remediate`` has a safe mode and gates ``live`` the way ``sdlc_feature`` does.

_CONFIRM_HINT = (
    "pass confirm=true to authorize it — an explicit human authorization, on top of the host's own."
)


async def sdlc_address_review(
    pr: str,
    repo: str | None = None,
    bot_login: str | None = None,
    max_refines: int = 3,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Address the human review comments on an open PR and **push a fix to its branch**:
    clone the repo, check the PR out, feed the comments to codegen, re-drive the change to
    green (tests + preflight), push. **Gated — needs ``confirm=true``**: there is no local
    mode, a successful call writes to the PR. Spends tokens. Needs ``git``, an authenticated
    ``gh``, a model, and the run backend's database. ``repo`` defaults to ``SDLC_REPO_URL``;
    ``bot_login`` skips the agent's own comments."""
    if not confirm:
        return {"error": f"sdlc_address_review pushes to the PR branch; {_CONFIRM_HINT}"}
    import os

    from orchestrator.core.env import load_local_env

    load_local_env()
    repo_url = repo or os.getenv("SDLC_REPO_URL")
    if not repo_url:
        return {"error": "pass repo, or set SDLC_REPO_URL to the repo clone URL."}

    from orchestrator.sdlc.review_response import (
        PRCheckoutError,
        checkout_pr_worktree,
        respond_to_pr_feedback,
    )
    from orchestrator.sdlc.worker import build_deps

    progress = Reporter(ctx, phases=("checkout", "respond", "done"))
    await progress.step(1, 3, f"[checkout] cloning and checking out {pr}")
    try:
        workdir, branch = await checkout_pr_worktree(repo_url, pr)
    except PRCheckoutError as exc:
        return {"error": str(exc), "step": exc.step}
    await progress.step(2, 3, f"[respond] addressing review comments on {branch}")
    result = await respond_to_pr_feedback(
        build_deps(),
        pr_url=pr,
        branch=branch,
        path=str(workdir),
        bot_login=bot_login,
        max_refines=max_refines,
    )
    await progress.step(
        3, 3, f"[done] {result.detail or ('pushed' if result.addressed else 'nothing to push')}"
    )
    return {"pr": pr, "branch": branch, **result.__dict__}


async def sdlc_complete(
    pr: str,
    issue: str | None = None,
    status: str = "Done",
    allow_unmerged: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Close the tracker issue for a **merged** PR — the merge → Done bookend. Verifies the
    PR is merged (``gh``), derives the issue key from its head branch
    (``feat/<sdlc_id>/<KEY>``) unless ``issue`` is given, transitions the issue to
    ``status``, comments the merge, and marks the backlog intent done. **Gated — needs
    ``confirm=true``**: it writes to the tracker for real, never dry-run. Needs an
    authenticated ``gh`` and Jira credentials."""
    if not confirm:
        return {"error": f"sdlc_complete transitions a real tracker issue; {_CONFIRM_HINT}"}
    from orchestrator.core.env import load_local_env
    from orchestrator.sdlc.complete import CompleteError, complete_issue_for_pr

    load_local_env()
    try:
        result = await complete_issue_for_pr(pr, issue=issue, status=status, allow_unmerged=allow_unmerged)
    except CompleteError as exc:
        return {"error": str(exc), "code": exc.code}
    return dict(result.__dict__)


async def sdlc_remediate(
    report_path: str,
    mappings_path: str,
    repo: str | None = None,
    min_severity: str = "warning",
    live: bool = False,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Turn an infodrift drift report into remediation feature runs, one per material
    finding at or above ``min_severity`` (``warning`` | ``critical``). ``report_path`` is
    the ``full_report`` JSON, ``mappings_path`` the confirmed code↔ontology mapping store —
    both on this machine. Spends tokens. Safe by default (``live=false``): each task leaves
    a local branch + diff; ``live=true`` opens PRs and is **gated on ``confirm=true``**, like
    ``sdlc_feature``. ``repo`` defaults to ``SDLC_REPO_URL``."""
    if live and not confirm:
        return {"error": f"live=true opens real PRs; {_CONFIRM_HINT}"}
    if min_severity not in ("warning", "critical"):
        return {"error": "min_severity must be 'warning' or 'critical'"}
    import json

    from orchestrator.sdlc.feature_runner import FeatureRunError, run_feature
    from orchestrator.spine import (
        DriftReport,
        MappingStore,
        RemediationTask,
        execute_remediations,
        infer_entity_iris,
    )

    try:
        payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"could not read the drift report at {report_path!r}: {exc}"}
    report = DriftReport.from_infodrift(payload)
    try:
        store = MappingStore(mappings_path)
        mappings = store.load()
    except (OSError, ValueError) as exc:
        return {"error": f"could not read the mapping store at {mappings_path!r}: {exc}"}
    entity_iris = infer_entity_iris(report, mappings)

    progress = Reporter(ctx)
    done = 0

    async def runner(task: RemediationTask) -> str:
        nonlocal done
        done += 1
        await progress.step(done, done, f"[remediate #{done}] {task.spec.get('title', task.entity_key)}")
        result = await run_feature(source="spine://remediation", spec=task.spec, repo=repo, live=live)
        return result.branch

    try:
        outcomes = await execute_remediations(
            report,
            runner=runner,
            entity_iris=entity_iris,
            code_for_iri=store.code_for_iri(),
            min_severity=min_severity,
        )
    except FeatureRunError as exc:
        return {"error": str(exc), "code": exc.code}
    return {
        "live": live,
        "tasks": len(outcomes),
        "ok": sum(1 for o in outcomes if o.ok),
        "outcomes": [
            {"entity": o.entity_key, "title": o.title, "ok": o.ok, "detail": o.detail, "result": o.result}
            for o in outcomes
        ],
        "markdown": "_No material drift findings — nothing to remediate._"
        if not outcomes
        else "\n".join(
            f"- [{'OK' if o.ok else 'FAILED'}] `{o.entity_key}`: {o.detail}"
            + (f" → {o.result}" if o.result else "")
            for o in outcomes
        ),
    }


def _finding_dict(f: Any) -> dict[str, Any]:
    return {"title": f.title, "file": f.file, "line": f.line, "severity": f.severity, "detail": f.detail}


async def audit_repo(
    repo_path: str,
    focus: str = "general code quality, correctness risks, and security",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """A codebase-auditor persona reads the repo — the graph plus file reads, **no writes**
    — and reports findings anchored to a real ``file:line`` (claims that resolve to nothing
    are listed separately as ``unresolved``). **Spends tokens**: needs a tool-calling model
    (``ORCHESTRATOR_INTAKE_MODEL``). ``repo_path`` is a local path or a git URL; ``focus``
    says what to look for. Progress is start and done only — the audit loop has no
    per-step hook."""
    from orchestrator.core.env import load_local_env
    from orchestrator.sdlc.codegen import resolve_codegen_model

    load_local_env()
    model = resolve_codegen_model()
    if not model:
        return {
            "error": "audit_repo needs a tool-calling model — "
            "set ORCHESTRATOR_INTAKE_MODEL (or SDLC_CODEGEN_MODEL)."
        }

    async def run(repo: Any) -> dict[str, Any]:
        from orchestrator.core.llm import LiteLLMClient
        from orchestrator.personas import render_findings_markdown, run_audit

        progress = Reporter(ctx, phases=("audit", "done"))
        await progress.step(1, 2, f"[audit] reading the repo with focus: {focus}")
        result = await run_audit(repo, llm=LiteLLMClient(), model=model, focus=focus)
        await progress.step(2, 2, f"[done] {len(result.findings)} finding(s); {result.stopped_reason}")
        return {
            "summary": result.summary,
            "findings": [_finding_dict(f) for f in result.findings],
            "unresolved": [_finding_dict(f) for f in result.unresolved],
            "steps": result.steps,
            "stopped_reason": result.stopped_reason,
            "markdown": render_findings_markdown(result, title=f"Audit — {Path(repo).resolve().name}"),
        }

    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _open_repo(repo_path) as repo:
            return await run(repo)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


# ---- operator tools: what is running, what is waiting on me — over the registry -------
#
# The successor to the terminal UI removed in #316. These go over HTTP to the registry
# (``plugin/registry_client.py`` says why), so they need ``orchestrator up`` — or a
# ``ORCHESTRATOR_API_URL`` pointing at a running one — and nothing else.


async def _registry(fn: Callable[[Any], Any]) -> dict[str, Any]:
    """Run ``fn(client)`` against the registry; an unreachable or refusing registry returns
    ``{"error": …, "hint": …}`` rather than raising, like a bad ``repo_path`` does."""
    from orchestrator.plugin.registry_client import RegistryError, registry_client

    client = registry_client()
    try:
        out = await fn(client)
        return dict(out)
    except RegistryError as exc:
        return {"error": str(exc), "hint": exc.hint, "registry": client.base_url}
    finally:
        await client.aclose()


async def registry_runs(limit: int = 20) -> dict[str, Any]:
    """Recent SDLC runs at the registry, most recently active first: id, state
    (``running`` / ``merged`` / ``failed`` / ``denied`` / ``cancelled``), last action and
    timestamps. Read-only; scoped to the API key's tenant. Needs the registry up
    (``orchestrator up``). ``limit`` is capped at 200 by the server."""

    async def go(client: Any) -> dict[str, Any]:
        items = await client.runs(limit=limit)
        return {"count": len(items), "items": items, "markdown": _runs_markdown(items)}

    return await _registry(go)


async def registry_approvals(limit: int = 50) -> dict[str, Any]:
    """Pending approvals at the registry — the gates waiting on a human — latest first,
    each with its id, title, risk classification and the run it belongs to. Read-only.
    Decide one with ``registry_decide``. Needs the registry up."""

    async def go(client: Any) -> dict[str, Any]:
        items = await client.approvals(limit=limit)
        return {"count": len(items), "items": items, "markdown": _approvals_markdown(items)}

    return await _registry(go)


async def registry_decide(
    approval_id: str,
    action: str,
    rationale: str = "",
    modified_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide a pending approval at the registry so its run continues (or stops).

    ``action`` is ``approve``, ``reject`` or ``modify_input`` (which requires
    ``modified_input``, the patch the run should continue with). A rejection ends the
    run, which is why a host treats this as destructive. The registry records the API-key
    principal as the actor and signals the workflow — the same path as the web inbox.
    ``sdlc_decide_gate`` does the same in-process for a run this plugin started; use that
    when there is no registry, and this when there is."""
    if action not in ("approve", "reject", "modify_input"):
        return {"error": f"action must be approve, reject or modify_input, not {action!r}"}
    if action == "modify_input" and not modified_input:
        return {"error": "modify_input needs a non-empty modified_input patch"}

    async def go(client: Any) -> dict[str, Any]:
        record = await client.decide(
            approval_id, action, rationale=rationale or None, modified_input=modified_input
        )
        return {"approval_id": approval_id, "action": action, "approval": record}

    return await _registry(go)


async def registry_trace(sdlc_id: str, tail: int = 50) -> dict[str, Any]:
    """A run's trace at the registry: the newest ``tail`` audit entries and tool
    invocations, the verifier outcome, and the replan count against its budget. Bounded —
    ``truncated`` says how many older entries were left out. Read-only. Pass the run id as
    ``registry_runs`` lists it — the same id the web console links its trace with."""

    async def go(client: Any) -> dict[str, Any]:
        # The audit rows of an SDLC run carry the run id itself (`trace_id == resource_id ==
        # sdlc_id`); `task-<id>` is the *Temporal workflow* id, and the trace endpoint does
        # not know it. The console fetches `/v1/tasks/<sdlc_id>/trace`, so do the same.
        task_id = sdlc_id
        trace = await client.trace(task_id)
        audit = list(trace.get("audit") or [])
        tools = list(trace.get("tool_invocations") or [])
        kept_audit, kept_tools = audit[-tail:] if tail > 0 else [], tools[-tail:] if tail > 0 else []
        return {
            "sdlc_id": sdlc_id,
            "task_id": task_id,
            "verifier_outcome": trace.get("verifier_outcome"),
            "workflow_pattern": trace.get("workflow_pattern"),
            "replan_count": trace.get("replan_count", 0),
            "replan_budget": trace.get("replan_budget", 0),
            "audit": kept_audit,
            "tool_invocations": kept_tools,
            "truncated": {
                "audit": max(len(audit) - len(kept_audit), 0),
                "tool_invocations": max(len(tools) - len(kept_tools), 0),
            },
            "markdown": _trace_markdown(task_id, trace, kept_audit, len(audit)),
        }

    return await _registry(go)


def _runs_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_No runs recorded at this registry._"
    lines = ["| run | state | last action | updated |", "|---|---|---|---|"]
    for r in items:
        lines.append(
            f"| `{r.get('sdlc_id', '')}` | {r.get('state', '')} | {r.get('last_action', '')} | "
            f"{r.get('updated_at', '')} |"
        )
    return "\n".join(lines)


def _approvals_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_Nothing is waiting on a decision._"
    lines = ["| approval | risk | run | title |", "|---|---|---|---|"]
    for a in items:
        lines.append(
            f"| `{a.get('id', '')}` | {a.get('risk_classification', '')} | `{a.get('task_id', '')}` | "
            f"{a.get('title', '')} |"
        )
    return "\n".join(lines)


def _trace_markdown(task_id: str, trace: dict[str, Any], audit: list[dict[str, Any]], total: int) -> str:
    head = [
        f"**{task_id}** — verifier: {trace.get('verifier_outcome') or '—'} · "
        f"replans: {trace.get('replan_count', 0)}/{trace.get('replan_budget', 0)}",
    ]
    if not audit:
        return head[0] + "\n\n_No audit entries._"
    shown = f"last {len(audit)} of {total}" if total > len(audit) else f"all {total}"
    lines = [*head, "", f"Audit ({shown}):", "", "| when | actor | action | resource |", "|---|---|---|---|"]
    for e in audit:
        lines.append(
            f"| {e.get('timestamp', '')} | {e.get('actor', '')} | {e.get('action', '')} | "
            f"{e.get('resource_type', '')}:{e.get('resource_id', '')} |"
        )
    return "\n".join(lines)


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
    # the free half of the back half (gap 5): understand, profile, design, baseline
    understand_repo,
    profile_repo,
    design_change,
    sdlc_baseline,
    # the gated half of the back half (gap 5): each spends money or writes externally
    sdlc_address_review,
    sdlc_complete,
    sdlc_remediate,
    audit_repo,
    # operator: what is running, what is waiting on me — over the registry
    registry_runs,
    registry_approvals,
    registry_decide,
    registry_trace,
)


@dataclass(frozen=True)
class Tier:
    """What a tool can cost you if it is wrong — the four hints MCP hosts understand, and
    the OAuth scope a bearer token needs on the HTTP transport."""

    name: str
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    scope: str


#: Read-only comprehension. ``open_world`` because ``repo_path`` may be a git URL (a
#: shallow clone) and a requirements ``source`` may be Confluence or Notion.
COMPREHEND = Tier(
    "comprehend", read_only=True, destructive=False, idempotent=True, open_world=True, scope=SCOPE_READ
)
#: The same, for tools that only ever read this machine.
COMPREHEND_LOCAL = Tier(
    "comprehend", read_only=True, destructive=False, idempotent=True, open_world=False, scope=SCOPE_READ
)
#: Plan and decide: writes under ``.spine/`` only, re-running rewrites the same document.
PLAN = Tier("plan", read_only=False, destructive=False, idempotent=True, open_world=False, scope=SCOPE_PLAN)
#: Plan-tier semantics for a local write that may follow a clone: ``understand_repo``
#: writes under ``episteme/`` (or ``out``) and nowhere else, but its ``repo_path`` may be a
#: URL, so it is not local-only the way ``sdlc_plan`` is.
PLAN_REMOTE = Tier(
    "plan", read_only=False, destructive=False, idempotent=True, open_world=True, scope=SCOPE_PLAN
)
#: Observing a run: reads Temporal state, changes nothing.
RUN_OBSERVE = Tier(
    "run", read_only=True, destructive=False, idempotent=True, open_world=True, scope=SCOPE_READ
)
#: An agent that only reads, on a model: ``audit_repo`` writes nothing, but it spends
#: tokens and no two runs are the same — read-only for the host, run scope for the token.
AUDIT = Tier("audit", read_only=True, destructive=False, idempotent=False, open_world=True, scope=SCOPE_RUN)
#: Driving a run: spends money, and with ``live``/``confirm`` writes where it cannot be
#: taken back. A rejected gate ends a run, which is why deciding one is destructive too.
RUN = Tier("run", read_only=False, destructive=True, idempotent=False, open_world=True, scope=SCOPE_RUN)

#: Every registered tool's tier, by function name. Keep it total: registration refuses a
#: tool that is missing here, and ``tests/plugin`` asserts the two stay in step.
_TIER: dict[str, Tier] = {
    "doctor": COMPREHEND_LOCAL,
    "ingest_preview": COMPREHEND,
    "pkg_grounding": COMPREHEND,
    "read_memory_bank": COMPREHEND,
    "map_repo": COMPREHEND,
    "blast_radius": COMPREHEND,
    "explain_symbol": COMPREHEND,
    "investigate": COMPREHEND,
    "localize": COMPREHEND,
    "regression_gaps": COMPREHEND,
    "root_cause": COMPREHEND,  # `use_llm` spends tokens; it still never changes code
    "pkg_joins": COMPREHEND_LOCAL,
    "sdlc_plan": PLAN,
    "sdlc_approve": PLAN,
    "docs_for": COMPREHEND,
    "sdlc_feature": RUN,
    "sdlc_start_run": RUN,
    "sdlc_run_status": RUN_OBSERVE,
    "sdlc_decide_gate": RUN,
    "sdlc_run_result": RUN_OBSERVE,
    "understand_repo": PLAN_REMOTE,
    "profile_repo": COMPREHEND,
    "design_change": COMPREHEND,  # `use_llm` spends tokens; it still never writes
    "sdlc_baseline": COMPREHEND,
    "sdlc_address_review": RUN,  # pushes to the PR branch
    "sdlc_complete": RUN,  # transitions a real tracker issue
    "sdlc_remediate": RUN,  # spends tokens; live opens PRs
    "audit_repo": AUDIT,
    "registry_runs": RUN_OBSERVE,
    "registry_approvals": RUN_OBSERVE,
    "registry_decide": RUN,  # a rejection ends a run
    "registry_trace": RUN_OBSERVE,
}


def tool_annotations(name: str) -> dict[str, bool]:
    """The MCP ``ToolAnnotations`` fields for a registered tool, as plain data.

    Plain so it is testable without the ``mcp`` extra; ``_register_tools`` wraps it in
    the SDK type. Raises ``KeyError`` for a tool with no tier — deliberately, so a new
    tool cannot reach a host with its cost unstated.
    """
    tier = _TIER[name]
    return {
        "read_only_hint": tier.read_only,
        "destructive_hint": tier.destructive,
        "idempotent_hint": tier.idempotent,
        "open_world_hint": tier.open_world,
    }


def tool_scope(name: str) -> str:
    """The OAuth scope a bearer token needs to call ``name`` over HTTP. ``KeyError`` for a
    tool with no tier — the same refusal as ``tool_annotations``."""
    return _TIER[name].scope


def current_token() -> Any:
    """The verified bearer token the SDK put in its auth context for this request, or
    ``None`` — stdio, an unauthenticated loopback bind, or no ``mcp`` extra at all."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:  # pragma: no cover - without the `mcp` extra nothing is served
        return None
    return get_access_token()


def scope_denial(name: str, scope: str, token: Any = None) -> dict[str, Any] | None:
    """``None`` when the current caller may call ``name``; otherwise the error to return.

    The caller is the verified bearer token for this request (looked up when not given).
    No token — stdio, or an unauthenticated loopback bind — means no check.
    """
    token = token if token is not None else current_token()
    if token is None or scope in token.scopes:
        return None
    return {
        "error": f"{name} needs scope {scope!r}; this token has {sorted(token.scopes)}",
        "needs": scope,
        "has": sorted(token.scopes),
        "hint": "Ask for a token that carries the scope, or use a tier this token is allowed.",
    }


def _scoped(fn: Callable[..., Any], scope: str) -> Callable[..., Any]:
    """Wrap a tool so the scope guard runs before it. ``functools.wraps`` carries the
    signature, annotations and docstring across, which is what the SDK builds the input
    schema and description from — a guarded tool advertises exactly what the bare one did."""

    @functools.wraps(fn)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        from orchestrator.plugin.audit import AUDITED_SCOPES, record_invocation

        token = current_token()
        denied = scope_denial(fn.__name__, scope, token)
        if denied is not None:
            # Every denial is recorded: a token probing above its tier is worth knowing about.
            await record_invocation(
                token=token,
                tool=fn.__name__,
                scope=scope,
                arguments=kwargs,
                outcome="denied",
                denied_scope=scope,
            )
            return denied
        out = fn(*args, **kwargs)
        result = await out if inspect.isawaitable(out) else out
        if token is not None and scope in AUDITED_SCOPES:
            outcome = "error" if isinstance(result, dict) and "error" in result else "ok"
            await record_invocation(
                token=token, tool=fn.__name__, scope=scope, arguments=kwargs, outcome=outcome
            )
        return result

    return guarded


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
    from mcp.types import ToolAnnotations

    for fn in _TOOLS:
        try:
            hints = tool_annotations(fn.__name__)
        except KeyError as exc:
            raise RuntimeError(f"tool {fn.__name__!r} has no tier in _TIER — say what it can cost") from exc
        server.tool(
            annotations=ToolAnnotations(
                read_only_hint=hints["read_only_hint"],
                destructive_hint=hints["destructive_hint"],
                idempotent_hint=hints["idempotent_hint"],
                open_world_hint=hints["open_world_hint"],
            )
        )(_scoped(fn, tool_scope(fn.__name__)))
    return server


def _register_prompts(server: Any) -> Any:
    """The skill's workflow as MCP prompts — ``plugin.prompts`` says why they live there."""
    from orchestrator.plugin.prompts import _PROMPTS

    for name, fn in _PROMPTS:
        first_line = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else None
        server.prompt(name=name, description=first_line)(fn)
    return server


def _register_resources(server: Any) -> Any:
    """The committed bank, the build documents and the state report as MCP resources —
    ``plugin.resources`` says why they address the default repository."""
    from orchestrator.plugin.resources import _RESOURCES

    for spec in _RESOURCES:
        server.resource(spec.uri, name=spec.name, description=spec.description, mime_type="text/markdown")(
            spec.fn
        )
    return server


def _register_all(server: Any) -> Any:
    return _register_resources(_register_prompts(_register_tools(server)))


def build_server() -> Any:
    """Build the FastMCP server with the orchestrator's plugin tools, prompts and
    resources registered.

    Stdio transport (Phase A): the local plugin a desktop host launches as a
    subprocess. For the remote HTTP transport see ``build_http_server``.
    """
    return _register_all(_import_server_class()("synaptixs-spine"))


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
        server=_register_all(server),
        transport={
            "host": host,
            "port": port,
            "streamable_http_path": path,
            "stateless_http": stateless,
        },
    )


__all__ = [
    "audit_repo",
    "blast_radius",
    "build_http_server",
    "build_server",
    "design_change",
    "docs_for",
    "doctor",
    "explain_symbol",
    "HttpServer",
    "ingest_preview",
    "investigate",
    "localize",
    "map_repo",
    "pkg_grounding",
    "pkg_joins",
    "profile_repo",
    "read_memory_bank",
    "registry_approvals",
    "registry_decide",
    "registry_runs",
    "registry_trace",
    "regression_gaps",
    "root_cause",
    "scope_denial",
    "sdlc_address_review",
    "sdlc_approve",
    "sdlc_baseline",
    "sdlc_complete",
    "sdlc_decide_gate",
    "sdlc_feature",
    "sdlc_plan",
    "sdlc_remediate",
    "sdlc_run_result",
    "sdlc_run_status",
    "sdlc_start_run",
    "Tier",
    "tool_annotations",
    "tool_scope",
    "understand_repo",
]
