"""Registry & integrations: template, contract, task, mcp, catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ._common import _check, _client, _load_payload, _print, _repo_arg

template_app = typer.Typer(help="Manage agent templates in the registry.", no_args_is_help=True)
contract_app = typer.Typer(help="Manage tool contracts in the registry.", no_args_is_help=True)
task_app = typer.Typer(help="Submit tasks to the registry for execution.", no_args_is_help=True)


mcp_app = typer.Typer(help="Onboard external MCP servers (DBs, Atlassian, …).", no_args_is_help=True)
catalog_app = typer.Typer(help="Capability catalog — inspect what Spine can assemble.", no_args_is_help=True)


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
