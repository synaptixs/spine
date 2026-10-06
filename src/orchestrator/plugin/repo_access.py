"""Open a repository for a plugin tool — a local path, or a git URL shallow-cloned and cleaned up.

Lives apart from ``server.py`` so a tool module (``requirements_tools``) can open a repository
without importing the server that registers it: ``server`` imports each tool module lazily, and a
tool importing ``server`` back made a two-module import cycle.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def open_repo(repo_path: str) -> Iterator[Any]:
    """Yield a local repo ``Path`` for a local path OR a git URL (shallow-cloned + cleaned up),
    resolved through the same guard as the CLI's ``_repo_arg``."""
    from orchestrator.registry.api.config import Settings
    from orchestrator.registry.api.workspace import materialize_repo_source, resolve_repo_source

    source = resolve_repo_source(repo_path, Settings(repo_allow_any_local=True))
    with materialize_repo_source(source, log=lambda _m: None) as path:
        yield path
