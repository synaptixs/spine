"""``uvx synaptixs-spine@latest setup`` must be a real command line (D2, plan §9).

``uvx <package>`` runs the console script named after the package. Spine's scripts were
``orchestrator`` and ``orchestrator-mcp`` only, so the one-line start D2 chose would have
failed with "executable not found". A ``synaptixs-spine`` script pointing at the same
Typer app makes it work, with no ``--from`` to explain.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_the_package_name_is_also_a_console_script_for_the_cli() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]

    assert scripts["synaptixs-spine"] == scripts["orchestrator"] == "orchestrator.cli:app"


def test_setup_knows_the_same_levels_as_the_server() -> None:
    from orchestrator.plugin.server import LEVELS as SERVER_LEVELS
    from orchestrator.setup.entry import LEVELS

    assert tuple(SERVER_LEVELS) == LEVELS


def test_a_pin_that_is_not_a_version_is_refused() -> None:
    import pytest

    from orchestrator.setup.entry import desired_entry

    with pytest.raises(ValueError):
        desired_entry(pin="3.69.0 ; touch /tmp/x")
    assert desired_entry(pin="3.69.0").args[1].endswith("==3.69.0")
