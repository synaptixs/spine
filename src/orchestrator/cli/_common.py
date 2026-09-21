"""Helpers shared by more than one command module: the HTTP client, printing, repo resolution."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import typer


def _client() -> httpx.Client:
    base_url = os.getenv("ORCHESTRATOR_API_URL", "http://localhost:8000")
    api_key = os.getenv("ORCHESTRATOR_API_KEY", "dev-key")
    timeout = float(os.getenv("ORCHESTRATOR_API_TIMEOUT_SECONDS", "60"))
    return httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=httpx.Timeout(timeout))


def _load_payload(path: Path) -> dict[str, Any]:
    import yaml

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded: dict[str, Any] = yaml.safe_load(text)
        return loaded
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _print(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


@contextlib.contextmanager
def _repo_arg(spec: str) -> Iterator[tuple[Path, bool]]:
    """Resolve a repo argument to an on-disk path, yielding ``(path, is_remote)``.

    ``spec`` is a **local path** (used as-is — the CLI is a trusted, single-user
    context) or a **git URL** (``https://``/``ssh://``/``git@host:…`` for
    github/bitbucket/gitlab, or a host in ``ORCHESTRATOR_REPO_ALLOWED_HOSTS``),
    which is shallow-cloned on demand and removed on exit. This mirrors the web
    ``/v1/capabilities/*`` resolution exactly (same SSRF guard + host allow-list),
    so ``understand``/``state``/``pkg``/``profile``/``catalog plan`` reach remote
    repos the way the UI does. ``is_remote`` lets a caller pick a sensible output
    location (a clone's files vanish on exit)."""
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import (
        RepoPathError,
        RepoSourceError,
        materialize_repo_source,
        resolve_repo_source,
    )

    try:
        # repo_allow_any_local: a local CLI path isn't sandboxed to a workspace root.
        source = resolve_repo_source(spec, Settings(repo_allow_any_local=True))
    except (RepoSourceError, RepoPathError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    with materialize_repo_source(source, log=lambda m: typer.echo(m, err=True)) as path:
        yield path, source.kind == "git"


def _merged_store(
    config: str,
    *,
    command: str,
    extractor: Any = None,
    warn_untrusted: bool = False,
) -> tuple[Any, Any, Any]:
    """``--repos``: every declared repository, merged into one scoped graph with its joins.

    Returns ``(FactStore, MergedFacts, RepoSet)``. Three commands spelled this by hand and had
    already drifted — a different error prefix each, a different return shape each, and the
    trust warning in exactly one of them. The prefix is why ``command`` is a parameter rather
    than a constant: ``RepoConfigError`` is the most common thing a user sees from these
    commands, and a message that names the wrong one sends them to the wrong place.

    ``extractor`` is forwarded **verbatim**, ``None`` included. `load_or_extract_repos` treats
    a missing extractor as "one fresh per repo" and resets `unresolved_calls` either way, so
    the two are equivalent today — but substituting one for the other here would be a silent
    change to join recall, and this helper exists to remove differences, not to introduce one.

    ``warn_untrusted`` prints the NOT REPRODUCIBLE line. It is off by default because a caller
    that *returns* its standing (the MCP server) must not print, and a caller that prints one
    without being asked would be inventing output.
    """
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.persistence import load_or_extract_repos
    from orchestrator.pkg.repos import RepoConfigError, load_repo_config

    try:
        repo_set = load_repo_config(config)
    except RepoConfigError as exc:
        typer.echo(f"{command}: {exc}")
        raise typer.Exit(code=1) from exc
    merged = load_or_extract_repos(repo_set, extractor=extractor)
    if warn_untrusted and not merged.trusted:
        # The brief is still useful, but it cannot be reproduced at a commit — and nothing in
        # the markdown would tell a reader that later.
        typer.echo(
            f"{command}: NOT REPRODUCIBLE — {', '.join(merged.untrusted_keys)} "
            "has uncommitted work or is not a git repo.",
            err=True,
        )
    return FactStore(merged.batch), merged, repo_set


def _check(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except json.JSONDecodeError:
            detail = resp.text
        typer.echo(f"Error {resp.status_code}: {json.dumps(detail, indent=2)}", err=True)
        raise typer.Exit(code=1)
    body: dict[str, Any] = resp.json()
    return body
