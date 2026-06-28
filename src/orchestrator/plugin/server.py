"""Expose the orchestrator *as* an MCP server (the plugin surface).

This is the inverse of ``orchestrator.mcp`` (which *consumes* MCP servers):
here the orchestrator is the server, so Claude Code / Codex / Claude Desktop can
call its capabilities as tools. The MCP server is a thin façade — each tool runs
the real engine (intake, PKG grounding, readiness).

Slice 1 exposes read/safe tools only (no external writes): ``doctor``,
``ingest_preview`` (dry-run), ``pkg_grounding``. The heavy gated ``sdlc`` run is
a later, job-style addition. Tool *implementations* are module-level functions
(unit-testable without the ``mcp`` extra); ``build_server`` lazy-imports
``FastMCP`` and registers them.
"""

from __future__ import annotations

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
    "build_http_server",
    "build_server",
    "doctor",
    "ingest_preview",
    "pkg_grounding",
    "read_memory_bank",
    "sdlc_decide_gate",
    "sdlc_feature",
    "sdlc_run_result",
    "sdlc_run_status",
    "sdlc_start_run",
]
