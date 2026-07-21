"""Expose the orchestrator *as* an MCP server (the plugin surface).

This is the inverse of ``orchestrator.mcp`` (which *consumes* MCP servers):
here the orchestrator is the server, so Claude Code / Codex / Claude Desktop can
call its capabilities as tools. The MCP server is a thin façade — each tool runs
the real engine (intake, PKG grounding, readiness).

Two tiers of tools. **Read-only comprehension** (no writes) — ``doctor``, ``pkg_grounding``,
``read_memory_bank``, ``ingest_preview`` (dry-run), and the graph-query set ``map_repo`` /
``blast_radius`` / ``explain_symbol`` / ``investigate`` / ``localize`` / ``regression_gaps`` /
``root_cause`` — hands an assistant Spine's *engineering decisions* (what breaks, what's
untested, where a change lands) with ``file:line`` provenance. All deterministic + no
credentials, except ``root_cause``'s opt-in ``use_llm`` enrichment. The graph-query tools take
a local path **or a git URL** (shallow-cloned behind the CLI's SSRF/host-allow-list guard). The
heavy **gated ``sdlc``** run (real writes → PR) is the second tier. Tool *implementations* are
module-level functions (unit-testable without the ``mcp`` extra); ``build_server`` lazy-imports
``FastMCP`` and registers them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
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


def _in_repo(repo_path: str, fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """Run ``fn(repo)`` inside a resolved repo; a bad path / URL returns ``{"error": …}``."""
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _open_repo(repo_path) as repo:
            return fn(repo)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


def _in_repo_store(repo_path: str, fn: Callable[[Any, Any], dict[str, Any]]) -> dict[str, Any]:
    """Run ``fn(store, repo)`` inside a resolved repo; a bad path / URL returns ``{"error": …}``."""
    from orchestrator.registry.api.workspace import RepoPathError, RepoSourceError

    try:
        with _repo_store(repo_path) as (store, repo):
            return fn(store, repo)
    except (RepoSourceError, RepoPathError) as exc:
        return {"error": str(exc)}


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


def blast_radius(repo_path: str, symbol: str) -> dict[str, Any]:
    """ "What breaks if I change X" — a symbol's direct callers plus the cross-layer set a
    change ripples into (CALLS + IMPORTS + REFERENCES), each with ``file:line``. Deterministic."""

    def run(store: Any, _repo: Any) -> dict[str, Any]:
        matches = store.find(symbol)
        if not matches:
            return {"symbol": symbol, "found": False, "matches": []}
        out: list[dict[str, Any]] = []
        for node in matches[:5]:
            callers = store.callers_of(node.id)
            touched = store.touches(node.id)
            out.append(
                {
                    "id": node.id,
                    "kind": node.kind.value,
                    "where": str(node.provenance) if node.provenance else None,
                    "caller_count": len(callers),
                    "callers": [{"id": cs.caller.id, "at": cs.at} for cs in callers[:25]],
                    "touch_count": len(touched),
                    "touches": [
                        {"id": t.id, "where": str(t.provenance) if t.provenance else None}
                        for t in touched[:25]
                    ],
                }
            )
        return {"symbol": symbol, "found": True, "matches": out, "markdown": _blast_markdown(out)}

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


def investigate(repo_path: str, title: str, problem: str = "") -> dict[str, Any]:
    """Where a ticket lands in the code: the real symbols to start from (``file:line`` + caller
    counts), the owning areas, and any committed ``episteme/`` knowledge. Deterministic (no LLM)."""
    if not title and not problem:
        return {"error": "provide a ticket title (and optionally a problem description)"}

    def run(store: Any, repo: Any) -> dict[str, Any]:
        from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

        inv = build_investigation(title, problem, store=store, root=repo)
        return {
            "title": inv.title,
            "landing": [
                {"name": h.name, "kind": h.kind, "where": h.where, "callers": h.callers, "module": h.module}
                for h in inv.landing
            ],
            "areas": inv.areas,
            "has_knowledge": bool(inv.knowledge),
            "markdown": render_investigation_md(inv),
        }

    return _in_repo_store(repo_path, run)


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


def _blast_markdown(matches: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in matches:
        lines.append(f"### `{m['id']}` — {m['kind']}" + (f" @ {m['where']}" if m["where"] else ""))
        lines.append(
            f"- **Called by ({m['caller_count']}):** " + ", ".join(c["id"] for c in m["callers"][:10])
        )
        lines.append(f"- **Touches ({m['touch_count']}):** " + ", ".join(t["id"] for t in m["touches"][:10]))
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
    # gated codegen / run control
    sdlc_feature,
    sdlc_start_run,
    sdlc_run_status,
    sdlc_decide_gate,
    sdlc_run_result,
)


def _import_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "The orchestrator MCP plugin needs the 'mcp' extra: pip install 'synaptixs-spine[mcp]'"
        ) from exc
    return FastMCP


def _register_tools(server: Any) -> Any:
    for fn in _TOOLS:
        server.tool()(fn)
    return server


def build_server() -> Any:
    """Build the FastMCP server with the orchestrator's plugin tools registered.

    Stdio transport (Phase A): the local plugin a desktop host launches as a
    subprocess. For the remote HTTP transport see ``build_http_server``.
    """
    return _register_tools(_import_fastmcp()("synaptixs-spine"))


def build_http_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    path: str = "/mcp",
    stateless: bool = False,
    allow_unauthenticated: bool = False,
) -> Any:
    """Build the FastMCP server for the remote ``streamable-http`` transport (Phase C).

    Auth is derived from env (``orchestrator.plugin.auth.build_auth_from_env``):
    a verified bearer token (OAuth introspection or a static secret). Binding a
    non-loopback host **without** auth is refused unless ``allow_unauthenticated``
    is set — a public, unauthenticated SDLC control plane is never a default.
    """
    from orchestrator.plugin.auth import build_auth_from_env

    fastmcp = _import_fastmcp()
    auth_settings, verifier = build_auth_from_env()

    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if auth_settings is None and not is_loopback and not allow_unauthenticated:
        raise RuntimeError(
            f"Refusing to serve on {host!r} without auth. Configure a bearer token "
            "(ORCHESTRATOR_MCP_TOKEN or ORCHESTRATOR_MCP_INTROSPECTION_URL), bind to "
            "127.0.0.1, or pass --allow-unauthenticated for a trusted private network."
        )

    server = fastmcp(
        "synaptixs-spine",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=stateless,
        auth=auth_settings,
        token_verifier=verifier,
    )
    return _register_tools(server)


__all__ = [
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
