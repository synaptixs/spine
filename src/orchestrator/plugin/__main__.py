"""Run the orchestrator MCP plugin server (`orchestrator-mcp`).

Default transport is **stdio** (Phase A): a local subprocess a desktop host
launches and speaks MCP to over the pipe. Pass ``--http`` for the remote
``streamable-http`` transport (Phase C) so hosted clients (the Codex app,
claude.ai) can connect over the network — auth comes from env (see
``orchestrator.plugin.auth``).

    orchestrator-mcp                         # stdio (local)
    orchestrator-mcp --http                  # http on 127.0.0.1:8080/mcp
    orchestrator-mcp --http --host 0.0.0.0 --port 8080
    orchestrator-mcp --level build           # every tool (default: pkg, comprehension only)
    ORCHESTRATOR_MCP_REPOS=/srv/spine/.spine/repos.yaml orchestrator-mcp --http --host 0.0.0.0
                                             # shared: only the listed checkouts (plugin.shared)

``--level`` (or ``ORCHESTRATOR_MCP_LEVEL``) picks how much of Spine a host sees — see
``LEVELS`` in ``orchestrator.plugin.server``.
"""

from __future__ import annotations

import argparse
import os


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator-mcp", description="Run the orchestrator MCP plugin server."
    )
    parser.add_argument("--http", action="store_true", help="serve over streamable-http instead of stdio")
    parser.add_argument(
        "--host",
        default=os.getenv("ORCHESTRATOR_MCP_HOST", "127.0.0.1"),
        help="bind host (http; default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ORCHESTRATOR_MCP_PORT", "8080")),
        help="bind port (http; default 8080)",
    )
    parser.add_argument(
        "--path",
        default=os.getenv("ORCHESTRATOR_MCP_PATH", "/mcp"),
        help="streamable-http mount path (default /mcp)",
    )
    parser.add_argument(
        "--stateless", action="store_true", help="stateless http (no server-side session store)"
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="permit a non-loopback bind with no auth (trusted private network only)",
    )
    parser.add_argument(
        "--allow-any-repo",
        action="store_true",
        help="permit a non-loopback bind with no ORCHESTRATOR_MCP_REPOS list (a server only you use)",
    )
    parser.add_argument(
        "--level",
        choices=("pkg", "plan", "build"),
        default=None,
        help="tools to show: pkg (comprehension, default), plan (+ plan/approve), build (all); "
        "overrides ORCHESTRATOR_MCP_LEVEL",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # Bridge .env so the tools see provider/source/tracker creds, then serve.
    from orchestrator.core.env import load_local_env

    args = _build_parser().parse_args(argv)
    # A host (the Codex app, Claude Desktop) launches this as a subprocess whose cwd
    # isn't the repo, so cwd-relative `./.env` won't be found — `ORCHESTRATOR_DOTENV`
    # lets the host point at an absolute .env path without copying secrets into its
    # own config. Try cwd first, then the explicit path.
    load_local_env()
    dotenv = os.getenv("ORCHESTRATOR_DOTENV")
    if dotenv:
        load_local_env(dotenv)

    from orchestrator.plugin.server import resolve_level

    # Resolved once, up front: a bad ORCHESTRATOR_MCP_LEVEL stops the server with a usage
    # error instead of a traceback. The level reaches the server as an argument, and the
    # server's `doctor` reports it; the user's env is left as it was.
    try:
        level = resolve_level(args.level)
    except ValueError as exc:
        _build_parser().error(str(exc))
        raise  # unreachable — error() exits; says so to analysers that can't see NoReturn

    if args.http:
        from orchestrator.plugin import audit
        from orchestrator.plugin.server import build_http_server

        audit.configure_audit_logging()  # the container's log is the audit stream
        build_http_server(
            host=args.host,
            port=args.port,
            path=args.path,
            stateless=args.stateless,
            allow_unauthenticated=args.allow_unauthenticated,
            level=level,
            allow_any_repo=args.allow_any_repo,
        ).run()
    else:
        from orchestrator.plugin.server import build_server

        build_server(level=level).run()  # stdio transport by default


if __name__ == "__main__":
    main()
