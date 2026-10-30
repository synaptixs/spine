"""Builds started from the editor (SSPN-274): the run knows its checkout, and the CLI picks its gate.

The extension lists *this* repository's runs from a run store every Spine on the machine shares, so a
record must say which checkout it reasoned about. And the `--review` gate can now ask in two ways: at
the terminal, as before, or by parking for an approval someone answers elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.sdlc.autorun import AutorunError, RunContext, autorun
from orchestrator.sdlc.runstate import RunRecord, RunStore

_SPEC = {
    "intent_id": "PROJ-42",
    "title": "Cart.total raises KeyError for an unknown sku",
    "summary": "Cart.total in shop/cart.py crashes when a sku has no price.",
    "acceptance_criteria": ["Cart.total skips skus without a price instead of raising KeyError"],
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(
        "class Cart:\n"
        "    def total(self, prices, items):\n"
        "        return sum(prices[s] * q for s, q in items)\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


# ---- the run record names its checkout ------------------------------------------------------------


def test_a_record_keeps_the_checkout_it_was_run_for(tmp_path: Path) -> None:
    store = RunStore(root=tmp_path)
    store.save(RunRecord(run_id="r1", source="s", repo="/work/shop"))

    loaded = store.load("r1")

    assert loaded is not None and loaded.repo == "/work/shop"


def test_a_record_written_before_it_named_a_checkout_still_loads(tmp_path: Path) -> None:
    """Records from an older Spine have no `repo`: they load, and say they belong to no checkout."""
    (tmp_path / "old.json").write_text(json.dumps({"run_id": "old", "source": "s", "status": "done"}))

    loaded = RunStore(root=tmp_path).load("old")

    assert loaded is not None and loaded.repo == "" and loaded.status == "done"


def test_autorun_records_the_checkout_it_reasons_about(checkout: Path, tmp_path: Path) -> None:
    """Set at the first checkpoint, so even a run that parks before any spend can be found by its repo."""
    store = RunStore(root=tmp_path / "state")

    with pytest.raises(AutorunError) as raised:
        asyncio.run(
            autorun(
                "file://./spec.md",
                spec=dict(_SPEC),
                root=checkout,
                artifacts_dir=tmp_path / "artifacts",
                approvals_dir=tmp_path / "approvals",
                store=store,
            )
        )

    assert raised.value.code == 6  # parked at the plan gate: nobody approved a plan
    (record,) = store.all()
    assert record.status == "parked"
    assert record.repo == str(checkout.resolve())


# ---- the CLI picks the gate -----------------------------------------------------------------------


def _gate_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *flags: str) -> Any:
    got: dict[str, Any] = {}

    async def _autorun(*_a: Any, **k: Any) -> RunContext:
        got.update(k)
        return RunContext(run_id="r1", source="s", live=False, root=tmp_path, artifacts_dir=tmp_path / "a")

    monkeypatch.setattr("orchestrator.sdlc.autorun.autorun", _autorun)
    result = CliRunner().invoke(
        app, ["sdlc", "autorun", "--source", "file://./spec.md", "--no-plan-gate", *flags]
    )
    assert result.exit_code == 0, result.output
    return got["gate"]


def test_without_review_the_run_asks_nobody(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _gate_passed(monkeypatch, tmp_path) is None


def test_review_asks_at_the_terminal_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = _gate_passed(monkeypatch, tmp_path, "--review")

    assert gate.__qualname__.startswith("_terminal_gate.")


def test_review_can_park_for_an_approval_instead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = _gate_passed(monkeypatch, tmp_path, "--review", "--gate", "park")

    assert gate.__module__ == "orchestrator.sdlc.review_park"


def test_naming_a_gate_turns_review_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--gate` without `--review` would otherwise be a gate that is silently never asked."""
    assert _gate_passed(monkeypatch, tmp_path, "--gate", "terminal").__qualname__.startswith(
        "_terminal_gate."
    )
    assert _gate_passed(monkeypatch, tmp_path, "--gate", "park").__module__ == "orchestrator.sdlc.review_park"


def test_an_unknown_gate_is_refused_before_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: list[bool] = []

    async def _autorun(*_a: Any, **_k: Any) -> RunContext:
        called.append(True)
        raise AssertionError("the run must not start")

    monkeypatch.setattr("orchestrator.sdlc.autorun.autorun", _autorun)
    result = CliRunner().invoke(
        app, ["sdlc", "autorun", "--source", "file://./spec.md", "--no-plan-gate", "--gate", "email"]
    )

    assert result.exit_code == 2 and not called


# ---- a local checkout is the build source (D72) ---------------------------------------------------
#
# `sdlc_build` passes the open checkout as `--repo` and its current branch as `--base`. These pin what
# that means without changing `WorkspaceManager` (41 callers): the build starts from the checkout's
# last commit, in a worktree of Spine's own clone, and the checkout itself is never written to.


def _out(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _make(checkout: Path, tmp_path: Path, run: str) -> Path:
    from orchestrator.sdlc.workspace import WorkspaceManager

    branch = _out(checkout, "rev-parse", "--abbrev-ref", "HEAD")
    manager = WorkspaceManager(root=tmp_path / "ws", repo_url=str(checkout), base_branch=branch)
    return asyncio.run(manager.create(run, "PROJ-42"))


def test_a_build_from_a_checkout_starts_at_its_last_commit_and_leaves_it_alone(
    checkout: Path, tmp_path: Path
) -> None:
    (checkout / "shop" / "cart.py").write_text("# an edit nobody committed\n", encoding="utf-8")
    (checkout / "notes.txt").write_text("untracked\n", encoding="utf-8")
    head, status = _out(checkout, "rev-parse", "HEAD"), _out(checkout, "status", "--porcelain")

    worktree = _make(checkout, tmp_path, "run1")
    (worktree / "shop" / "cart.py").write_text("# the build's change\n", encoding="utf-8")
    _git(worktree, "commit", "-qam", "the build")

    assert _out(worktree, "rev-parse", "HEAD~1") == head  # built on the last commit
    assert not (worktree / "notes.txt").exists()  # uncommitted work is not in the build
    assert not worktree.is_relative_to(checkout)
    # The checkout is exactly as it was: same commit, same uncommitted edits, no new branch.
    assert _out(checkout, "rev-parse", "HEAD") == head
    assert _out(checkout, "status", "--porcelain") == status
    assert _out(checkout, "branch", "--list", "feat/*") == ""


def test_the_next_build_sees_a_commit_made_since(checkout: Path, tmp_path: Path) -> None:
    _make(checkout, tmp_path, "run1")
    (checkout / "shop" / "tax.py").write_text("RATE = 0.2\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "add tax")

    worktree = _make(checkout, tmp_path, "run2")

    assert _out(worktree, "rev-parse", "HEAD") == _out(checkout, "rev-parse", "HEAD")


def test_a_build_follows_the_branch_the_checkout_is_on(checkout: Path, tmp_path: Path) -> None:
    _make(checkout, tmp_path, "run1")
    _git(checkout, "switch", "-qc", "topic")
    (checkout / "shop" / "topic.py").write_text("X = 1\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "topic work")

    worktree = _make(checkout, tmp_path, "run2")

    assert (worktree / "shop" / "topic.py").is_file()
    assert _out(worktree, "rev-parse", "HEAD") == _out(checkout, "rev-parse", "HEAD")


def test_autorun_hands_its_run_to_a_gate_that_parks(checkout: Path, tmp_path: Path) -> None:
    """The park gate needs the run's id, approvals and record; autorun gives them before any stage."""
    attached: list[Any] = []

    class _Gate:
        def attach(self, ctx: Any) -> None:
            attached.append(ctx)

        async def __call__(self, _path: Path, _files: list[str]) -> bool:
            return False

    with pytest.raises(AutorunError):
        asyncio.run(
            autorun(
                "file://./spec.md",
                spec=dict(_SPEC),
                root=checkout,
                gate=_Gate(),
                artifacts_dir=tmp_path / "artifacts",
                approvals_dir=tmp_path / "approvals",
                store=RunStore(root=tmp_path / "state"),
            )
        )

    (ctx,) = attached
    assert ctx.record is not None and ctx.approvals_dir == tmp_path / "approvals"
