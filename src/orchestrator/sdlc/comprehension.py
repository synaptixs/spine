"""Repo comprehension milestone (M1): understand the repo → persisted artifacts.

Runs once per SDLC run, before the intent gate, on the base checkout (the same
one ``sdlc_profile_and_plan`` already ensures). Produces durable **architectural
artifacts** — the Product Knowledge Graph (a SQLite export + a bounded module
overview), the memory bank, and a current-state report — stored in the
``ArtifactStore`` under ``run/<sdlc_id>/comprehension/`` and summarised at Gate 1.

Deterministic, no LLM; the commit-SHA-keyed PKG cache makes re-runs on the same
commit cheap. The heavy extraction runs off the event loop so it never blocks the
activity worker.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from orchestrator.runtime import ArtifactStore

_KIND = "comprehension"


def _key(run_id: str, name: str) -> str:
    return f"run/{run_id}/{_KIND}/{name}"


def _compute(root: Path, sql_dialect: str | None) -> dict[str, Any]:
    """The deterministic, CPU-bound work (PKG + memory bank + current state).
    Returns raw bytes/strings; the async caller persists them."""
    from orchestrator.knowledge import build_memory_bank
    from orchestrator.knowledge.current_state import build_current_state
    from orchestrator.pkg import RepoCodeExtractor, export_sqlite
    from orchestrator.pkg.overview import build_overview

    batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root)
    overview = build_overview(batch)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "knowledge-graph.db"
        export_sqlite(batch, db_path)
        db_bytes = db_path.read_bytes()

    # Write the memory bank to a throwaway dir (never touch the checkout), read it back.
    mb_files: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        mb = build_memory_bank(root, out_dir=tmp, sql_dialect=sql_dialect)
        for p in sorted(Path(tmp).glob("*.md")):
            mb_files[p.name] = p.read_text(encoding="utf-8")

    current_state = build_current_state(root, lens="developer", sql_dialect=sql_dialect)
    return {
        "overview": overview,
        "db_bytes": db_bytes,
        "memory_bank": mb_files,
        "current_state": current_state,
        "greenfield": bool(mb.get("greenfield")),
        "summary": str(mb.get("summary") or ""),
    }


async def run_comprehension(
    root: Path | str,
    *,
    artifact_store: ArtifactStore,
    run_id: str,
    sql_dialect: str | None = None,
) -> dict[str, Any]:
    """Comprehend ``root``, persist the artifacts, and return a manifest.

    The manifest (JSON-safe) carries the headline counts + the artifact keys so
    the workflow can audit it and fold a summary into the intent gate."""
    computed = await asyncio.to_thread(_compute, Path(root), sql_dialect)

    artifacts: dict[str, str] = {}

    async def _put(name: str, data: bytes, content_type: str) -> None:
        key = _key(run_id, name)
        await artifact_store.put_bytes(key, data, content_type)
        artifacts[name] = key

    await _put("knowledge-graph.db", computed["db_bytes"], "application/vnd.sqlite3")
    await _put(
        "graph-overview.json",
        json.dumps(computed["overview"], default=str, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    for name, content in computed["memory_bank"].items():
        await _put(f"memory-bank/{name}", content.encode("utf-8"), "text/markdown")
    await _put("current-state.md", computed["current_state"].encode("utf-8"), "text/markdown")

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "greenfield": computed["greenfield"],
        "summary": computed["summary"],
        "counts": computed["overview"]["summary"],  # {nodes, grounded_nodes, external_nodes, edges}
        "kinds": computed["overview"].get("kinds", {}),
        "memory_bank_files": sorted(computed["memory_bank"]),
        "artifacts": dict(artifacts),
    }
    await _put(
        "comprehension.json",
        json.dumps(manifest, default=str, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    return manifest


__all__ = ["run_comprehension"]
