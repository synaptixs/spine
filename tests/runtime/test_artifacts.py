from __future__ import annotations

import pytest

from orchestrator.runtime.artifacts import (
    ArtifactStore,
    InMemoryArtifactStore,
    make_artifact_id,
)


async def test_in_memory_round_trip() -> None:
    store: ArtifactStore = InMemoryArtifactStore()
    payload = {"confidence": 0.9, "findings": "x", "claims": [{"id": "c_1"}]}
    await store.put_json("task/t1/n_analyst/output.json", payload)
    assert await store.get_json("task/t1/n_analyst/output.json") == payload


async def test_in_memory_missing_raises_lookup_error() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(LookupError):
        await store.get_json("task/missing/n/x.json")


def test_make_artifact_id_shape() -> None:
    assert make_artifact_id(task_id="t1", node_id="n_analyst") == "task/t1/n_analyst/output.json"
    assert make_artifact_id(task_id="t1", node_id="n_writer", suffix="draft") == "task/t1/n_writer/draft.json"
