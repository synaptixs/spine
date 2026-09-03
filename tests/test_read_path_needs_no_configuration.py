"""The read-only path runs with an **empty environment**. Phase 1 of the secrets spec.

This is the property the developer-adoption story rests on: `understand`, `state`, `investigate`
and `pkg extract` need no API key, no account, no database and no configuration. G4 states it as
an invariant — *"Read-only stays read-only and credential-free"* — and until this file nothing
defended it. The failure mode is not the initial design, which nobody gets wrong; it is a vault or
a tenant quietly becoming a requirement eighteen months later. This test is what makes that a red
build instead of a support ticket.

Measured 2026-09-02 before writing: `orchestrator state` on a scratch repository, in a process
holding only `PATH` and `HOME`, exited 0 with empty stderr. The test asserts that stays true.

Driven through the **console script**, not `python -m orchestrator.cli`: 3.29.1 turned `cli` into
a package and the `-m` form stopped working — the property held, the invocation did not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: The whole environment the subprocess gets. `PATH` finds the interpreter; `HOME` is what a
#: process needs to exist at all. Nothing else — no `ORCHESTRATOR_*`, no `*_API_KEY`, no `.env`.
EMPTY_ENV = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}


def _cli() -> str:
    beside = Path(sys.executable).parent / "orchestrator"
    if beside.exists():
        return str(beside)
    found = shutil.which("orchestrator")
    assert found, "the `orchestrator` console script is not installed in this environment"
    return found


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "orders.py").write_text(
        "def handle_order(order):\n    return validate_order(order)\n\n\n"
        "def validate_order(order):\n    return bool(order)\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text(
        "# Orders\n\n`validate_order` guards every path into `handle_order`.\n", encoding="utf-8"
    )
    return repo


READ_ONLY_COMMANDS = [
    pytest.param(["state", "{repo}"], id="state"),
    pytest.param(["understand", "{repo}"], id="understand"),
    pytest.param(["investigate", "{repo}", "--title", "order validation is wrong"], id="investigate"),
    pytest.param(["pkg", "extract", "{repo}"], id="pkg-extract"),
]


@pytest.mark.parametrize("argv", READ_ONLY_COMMANDS)
def test_a_read_only_command_needs_no_configuration(scratch_repo: Path, argv: list[str]) -> None:
    args = [a.format(repo=scratch_repo) for a in argv]
    result = subprocess.run(
        [_cli(), *args], capture_output=True, text=True, env=EMPTY_ENV, cwd=scratch_repo, timeout=600
    )
    assert result.returncode == 0, (
        f"`orchestrator {' '.join(argv)}` needs something from the environment.\nstderr:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, result.stderr


def test_the_harness_sees_a_failure(scratch_repo: Path) -> None:
    """A guard that always passes is worse than none. Prove a non-zero exit is observed."""
    result = subprocess.run(
        [_cli(), "no-such-command"], capture_output=True, text=True, env=EMPTY_ENV, cwd=scratch_repo
    )
    assert result.returncode != 0
