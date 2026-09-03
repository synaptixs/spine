"""Spine's command surface — the ``orchestrator`` CLI.

One module per help panel, plus the two sub-apps large enough to stand alone (``sdlc``,
``pkg``). ``_app`` owns the root Typer app and the panel layout; ``_common`` owns the helpers
more than one module shares. Importing this package registers every command: the console
entry point is ``orchestrator.cli:app``.

Configuration via environment variables:
    ORCHESTRATOR_API_URL   default http://localhost:8000
    ORCHESTRATOR_API_KEY   default dev-key
"""

from __future__ import annotations

from . import build, change, media, pkg, registry, sdlc, start, understand  # noqa: F401  (register commands)
from ._app import PANEL_BUILD, PANEL_GRAPH, PANEL_PLUMBING, app

app.add_typer(registry.template_app, name="template", rich_help_panel=PANEL_PLUMBING)
app.add_typer(registry.contract_app, name="contract", rich_help_panel=PANEL_PLUMBING)
app.add_typer(registry.task_app, name="task", rich_help_panel=PANEL_PLUMBING)
app.add_typer(sdlc.sdlc_app, name="sdlc", rich_help_panel=PANEL_BUILD)
app.add_typer(registry.mcp_app, name="mcp", rich_help_panel=PANEL_PLUMBING)
app.add_typer(registry.catalog_app, name="catalog", rich_help_panel=PANEL_PLUMBING)
app.add_typer(build.openspec_app, name="openspec", rich_help_panel=PANEL_BUILD)
app.add_typer(media.media_app, name="media", rich_help_panel=PANEL_GRAPH)
app.add_typer(pkg.pkg_app, name="pkg", rich_help_panel=PANEL_GRAPH)

__all__ = ["app"]
