"""Unified web UI for the registry service (P0 — one shell, one nav, real assets).

The shared shell (``page_shell``) + the home router are the start of folding the
previously-separate console / trace / intake surfaces under one app, one nav, and
one stylesheet served from ``web/static``.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.registry.api.web.home import router as web_router
from orchestrator.registry.api.web.shell import NAV, page_shell

STATIC_DIR = Path(__file__).parent / "static"

__all__ = ["NAV", "STATIC_DIR", "page_shell", "web_router"]
