"""The root Typer app, its help layout, and the global callback.

Every command module imports ``app`` and a panel name from here; the package ``__init__``
assembles the sub-apps onto it. Kept apart from ``__init__`` so command modules never import
the package root (which would be a cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup

# ---------------------------------------------------------------------------
# Top-level help layout.
#
# Typer prints commands in registration order, which here is the order features were
# added — a changelog, not a map. Every command and sub-app below declares one of these
# panels, and ``_SpineGroup`` lists them in ``_COMMAND_ORDER`` so the help screen reads as
# a workflow: set up, understand, investigate, build, then the plumbing. Names and
# behaviour are untouched; only the presentation is.
# ---------------------------------------------------------------------------

PANEL_START = "Get started"
PANEL_UNDERSTAND = "Understand a codebase"
PANEL_CHANGE = "Investigate & design a change"
PANEL_BUILD = "Plan & build"
PANEL_GRAPH = "Knowledge graph"
PANEL_PLUMBING = "Registry & integrations"

_COMMAND_ORDER: tuple[str, ...] = (
    # Get started
    "init",
    "doctor",
    "up",
    "models",
    "tui",
    # Understand a codebase
    "profile",
    "understand",
    "state",
    "audit",
    # Investigate & design a change
    "investigate",
    "design",
    "localize",
    "rca",
    "regression",
    # Plan & build
    "ingest",
    "backlog",
    "openspec",
    "sdlc",
    # Knowledge graph
    "pkg",
    "media",
    # Registry & integrations
    "template",
    "contract",
    "task",
    "mcp",
    "catalog",
)


class _SpineGroup(TyperGroup):
    """List top-level commands in ``_COMMAND_ORDER``; anything unlisted trails, in registration order.

    Rich groups commands into panels in the order it first meets each panel, so this
    ordering is what makes "Get started" print first rather than whichever panel holds
    the oldest command.
    """

    def list_commands(self, ctx: object) -> list[str]:  # ctx unused; typed loosely since Typer vendors Click
        rank = {name: i for i, name in enumerate(_COMMAND_ORDER)}
        names = list(self.commands)
        return sorted(names, key=lambda n: (rank.get(n, len(rank)), names.index(n)))


app = typer.Typer(
    help=(
        "Spine — understand a codebase, then design and deliver changes against it, all "
        "grounded in one code-true knowledge graph."
    ),
    no_args_is_help=True,
    cls=_SpineGroup,
)


def _version(show: bool) -> None:
    """`--version`: what is installed, and *where it came from*.

    The path is not decoration. CONTRIBUTING warns that "a bare command resolves to whichever
    install is on your PATH, which may be an older release that has no `pkg accuracy` at all" —
    and the first thing anyone does after installing is check the version. Printing only a
    number answers "which version exists"; printing the location answers "which one am I
    actually running", which is the question behind it.
    """
    if not show:
        return
    from orchestrator import __version__

    typer.echo(f"Spine {__version__}  (synaptixs-spine)")
    typer.echo(f"  running from {Path(__file__).resolve().parent}")
    raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit."),
    ] = False,
) -> None:
    """Spine — requirement in, reviewed pull request out, grounded in a graph of your code."""
