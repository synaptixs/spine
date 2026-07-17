"""M1 — repo comprehension: the service, the activity, and the intent-gate fold."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.runtime.artifacts import InMemoryArtifactStore
from orchestrator.sdlc.activities import SDLCActivities
from orchestrator.sdlc.comprehension import run_comprehension
from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.workflows import _comprehension_summary_line, _intents_gate_description


def _small_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\n\ndef main():\n    return greet('x')\n",
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
async def test_run_comprehension_persists_artifacts_and_manifest(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    manifest = await run_comprehension(_small_repo(tmp_path), artifact_store=store, run_id="R1")

    assert manifest["counts"]["nodes"] > 0
    arts = manifest["artifacts"]
    assert {"knowledge-graph.db", "graph-overview.json", "current-state.md"} <= set(arts)
    assert any(k.startswith("memory-bank/") for k in arts)

    # Artifacts are really stored under the run-scoped namespace.
    assert await store.get_bytes("run/R1/comprehension/knowledge-graph.db")  # non-empty
    md = (await store.get_bytes(arts["current-state.md"])).decode("utf-8")
    assert "#" in md  # a markdown report
    overview = json.loads((await store.get_bytes(arts["graph-overview.json"])).decode("utf-8"))
    assert overview["summary"]["nodes"] == manifest["counts"]["nodes"]


# --------------------------------------------------------------------------- #
# Activity
# --------------------------------------------------------------------------- #
class _StubSession:
    async def __aenter__(self) -> _StubSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


def _session_factory() -> Any:
    return lambda: _StubSession()


class _FakeWorkspace:
    def __init__(self, base: Path) -> None:
        self._base = base

    async def ensure_base_repo(self) -> Path:
        return self._base


def _acts(workspace: Any) -> SDLCActivities:
    deps = SDLCDeps(
        session_factory=_session_factory(),
        workspace=workspace,
        artifact_store=InMemoryArtifactStore(),
    )
    return SDLCActivities(deps)


async def test_activity_runs_and_returns_manifest(tmp_path: Path) -> None:
    out = await _acts(_FakeWorkspace(_small_repo(tmp_path))).comprehend_repo({"sdlc_id": "R2"})
    assert not out.get("skipped") and out["counts"]["nodes"] > 0
    assert out["artifacts"]["current-state.md"] == "run/R2/comprehension/current-state.md"


async def test_activity_skipped_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_COMPREHEND", "0")
    out = await _acts(_FakeWorkspace(_small_repo(tmp_path))).comprehend_repo({"sdlc_id": "R3"})
    assert out == {"skipped": True, "reason": "disabled"}


async def test_activity_skipped_when_no_repo() -> None:
    class _BadWorkspace:
        async def ensure_base_repo(self) -> Path:
            raise RuntimeError("no repo url configured")

    out = await _acts(_BadWorkspace()).comprehend_repo({"sdlc_id": "R4"})
    assert out["skipped"] is True and "no base repo" in out["reason"]


# --------------------------------------------------------------------------- #
# Intent-gate fold
# --------------------------------------------------------------------------- #
def test_gate_description_includes_comprehension() -> None:
    d = _intents_gate_description(
        2, [], None, {"counts": {"nodes": 50, "edges": 80}, "memory_bank_files": ["a.md", "b.md"]}
    )
    assert "Understood the repo: 50 code entities, 80 relationships" in d
    assert "2 memory-bank docs" in d


def test_comprehension_summary_line_variants() -> None:
    assert _comprehension_summary_line(None) is None
    assert "skipped" in (_comprehension_summary_line({"skipped": True, "reason": "disabled"}) or "")
    assert "Greenfield" in (_comprehension_summary_line({"greenfield": True, "counts": {}}) or "")


# --------------------------------------------------------------------------- #
# M1b — a feature worktree is seeded with the run's memory bank
# --------------------------------------------------------------------------- #
class _SeedWorkspace:
    def __init__(self, worktree: Path) -> None:
        self._wt = worktree

    async def create(self, sdlc_id: str, issue_key: str) -> Path:
        return self._wt


async def test_create_workspace_seeds_memory_bank(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    store = InMemoryArtifactStore()
    await store.put_bytes("run/R/comprehension/memory-bank/domain-model.md", b"# Domain\n", "text/markdown")
    await store.put_bytes("run/R/comprehension/memory-bank/glossary.md", b"# Glossary\n", "text/markdown")
    deps = SDLCDeps(
        session_factory=_session_factory(),
        workspace=_SeedWorkspace(worktree),  # type: ignore[arg-type]
        artifact_store=store,
    )
    comprehension = {
        "artifacts": {
            "current-state.md": "run/R/comprehension/current-state.md",  # not a memory-bank file → skipped
            "memory-bank/domain-model.md": "run/R/comprehension/memory-bank/domain-model.md",
            "memory-bank/glossary.md": "run/R/comprehension/memory-bank/glossary.md",
        }
    }
    out = await SDLCActivities(deps).create_workspace(
        {"sdlc_id": "R", "issue_key": "SDLC-1", "comprehension": comprehension}
    )
    assert out["memory_bank_seeded"] == 2
    assert (worktree / "episteme" / "domain-model.md").read_text(encoding="utf-8") == "# Domain\n"
    assert (worktree / "episteme" / "glossary.md").exists()


async def test_create_workspace_without_comprehension_seeds_nothing(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    deps = SDLCDeps(session_factory=_session_factory(), workspace=_SeedWorkspace(worktree))  # type: ignore[arg-type]
    out = await SDLCActivities(deps).create_workspace({"sdlc_id": "R", "issue_key": "SDLC-1"})
    assert out["memory_bank_seeded"] == 0
    assert not (worktree / "episteme").exists()
