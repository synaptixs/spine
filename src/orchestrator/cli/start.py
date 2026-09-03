"""Get started: init, doctor, up, models, tui."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from ._app import PANEL_START, app


@app.command("tui", rich_help_panel=PANEL_START)
def tui(
    api_url: Annotated[
        str, typer.Option("--api-url", help="Registry API base URL.", envvar="ORCHESTRATOR_API_URL")
    ] = "http://localhost:8000",
    api_key: Annotated[
        str, typer.Option("--api-key", help="API key for the registry.", envvar="ORCHESTRATOR_API_KEY")
    ] = "dev-key",
) -> None:
    """Launch the terminal UI: watch runs, clear gates, and delegate a run.

    A keyboard-driven cousin of the web inbox over the same ``/v1`` API. Needs the
    ``tui`` extra: ``pip install 'synaptixs-spine[tui]'``.
    """
    try:
        from orchestrator.tui.app import run_tui
    except ImportError as exc:  # textual is the optional `tui` extra
        typer.echo("The TUI needs the 'tui' extra. Install it: pip install 'synaptixs-spine[tui]'.", err=True)
        raise typer.Exit(code=2) from exc
    run_tui(api_url, api_key)


@app.command("models", rich_help_panel=PANEL_START)
def models_cmd(
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Filter to one vendor: anthropic, openai, gemini, …"),
    ] = None,
    tools_only: Annotated[
        bool,
        typer.Option("--tools-only/--all", help="Only models that support tool calling."),
    ] = True,
) -> None:
    """What you can point the pipeline at, and what each stage is using now.

    Read from the installed LiteLLM's own catalog rather than a list maintained here,
    so it reflects the client actually making the calls.

    **Tool calling is a requirement, not a preference.** Codegen forces a
    `submit_files` call and the acceptance judge forces `submit_verdict`; on a model
    without it both fall back to parsing prose out of a text reply, which is exactly
    the failure the forced-tool work removed. `--all` shows the rest, marked.
    """
    from orchestrator.core.llm import catalog

    rows = catalog.catalog(provider)
    if tools_only:
        rows = [m for m in rows if m.usable]
    typer.echo("Current per-stage models:")
    for stage in ("codegen", "judge", "intake"):
        chain = " → ".join(f"${v}" for v in catalog.STAGE_ENV[stage])
        typer.echo(f"  {stage:8} {catalog.resolve(stage):24} ({chain} → built-in default)")
    typer.echo("")
    typer.echo(catalog.render(rows, current=catalog.resolve("codegen")))


@app.command("doctor", rich_help_panel=PANEL_START)
def doctor() -> None:
    """Check environment readiness and print a diagnostic report.

    Bridges ``.env`` into the process environment first (same as ``ingest`` /
    ``sdlc``), so the report reflects exactly what the pipeline will see — a
    real exported variable still wins over the file.
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.doctor import render_report, run_env_checks

    loaded = load_local_env()
    if loaded:
        typer.echo(f"Loaded {loaded} variable(s) from .env\n")

    results = run_env_checks()
    report = render_report(results)
    typer.echo(report)
    all_passed = all(r.passed for r in results)
    if not all_passed:
        raise typer.Exit(code=1)


@app.command("init", rich_help_panel=PANEL_START)
def init(
    path: Annotated[Path, typer.Option("--path", help="Directory to scaffold the .env into.")] = Path("."),
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing .env with a fresh template.")
    ] = False,
) -> None:
    """Scaffold a new project: create a .env from the template, then guide setup.

    Creates a commented .env skeleton (from the same env groups ``doctor``
    checks), then reports readiness. While required variables are still unset it
    exits non-zero with a call to fill them in and re-run — so ``init`` is the
    one-command setup loop: run it, fill the blanks, run it again until green.

    Safe to re-run: an existing .env is never overwritten (only missing keys are
    appended) unless --force.
    """
    from orchestrator.doctor import render_report, run_env_checks
    from orchestrator.init_scaffold import parse_env_file, scaffold_env

    env_path = path / ".env"
    existed = env_path.exists()
    wrote, added = scaffold_env(env_path, force=force)
    if not existed and wrote:
        typer.echo(f"Created {env_path} from the template.")
    elif added:
        typer.echo(f"Extended {env_path} (+{len(added)} key(s): {', '.join(added)}).")
    else:
        typer.echo(f"{env_path} already has every required key — nothing to add.")

    # Report readiness against what's now in the file (blank values don't count),
    # so the operator sees exactly what's left to provide.
    current = parse_env_file(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    results = run_env_checks({k: v for k, v in current.items() if v})
    typer.echo("")
    typer.echo(render_report(results))
    typer.echo("")

    if all(r.passed for r in results):
        typer.echo(f"✓ Environment ready — every required variable is set in {env_path}.")
        return

    failed = [r.name for r in results if not r.passed]
    typer.echo(f"Action required — fill in the variables for: {', '.join(failed)}.")
    typer.echo(f"  1. Open {env_path} and provide the values (see .env.example for the full annotated list).")
    typer.echo("  2. Re-run `orchestrator init` (or `orchestrator doctor`) to verify.")
    raise typer.Exit(code=1)


@app.command("up", rich_help_panel=PANEL_START)
def up(
    port: Annotated[int, typer.Option("--port", help="Port for the web UI + API.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Bind address for the API.")] = "127.0.0.1",
    no_docker: Annotated[
        bool,
        typer.Option("--no-docker", help="Don't manage Docker; assume Postgres + Temporal are already up."),
    ] = False,
    no_worker: Annotated[
        bool, typer.Option("--no-worker", help="Skip the Temporal worker (browse-only; can't delegate runs).")
    ] = False,
    compose_file: Annotated[
        Path | None, typer.Option("--compose-file", help="Override the docker compose file to use.")
    ] = None,
) -> None:
    """Bring up the whole local stack in one command, then open the inbox.

    Starts Docker infra (Postgres + Temporal), applies migrations, and launches
    the web/API server **and** the Temporal worker with sensible defaults — so a
    non-technical user reaches the delegation inbox at ``/app`` without wiring up
    three terminals. Streams logs until Ctrl-C, then stops the app processes
    (infra containers are left running for fast restarts).
    """
    from orchestrator.core.env import load_local_env
    from orchestrator.launch import LaunchConfig, LaunchError, run_up

    load_local_env()

    config = LaunchConfig(
        host=host,
        port=port,
        use_docker=not no_docker,
        start_worker=not no_worker,
        compose_file=compose_file,
        api_key=os.getenv("ORCHESTRATOR_API_KEY", "dev-key"),
        session_secret=os.getenv("ORCHESTRATOR_SESSION_SECRET", "dev-session-secret"),
    )
    try:
        code = run_up(config, echo=typer.echo)
    except LaunchError as exc:
        typer.echo(f"\n✗ {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)
