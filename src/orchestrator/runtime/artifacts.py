"""Artifact-by-reference protocol for specialist outputs.

The manager-with-specialists pattern keeps the manager's context bounded by
having specialists write their full output to the artifact store and pass
back only a small ``SpecialistReturn`` with the artifact id. The manager (or
any downstream consumer) reads the full output back through the gateway's
``fetch_artifact`` tool.

This module wraps an ``ObjectStoreClient`` into a typed writer/reader pair
the runtime can call directly without an HTTP hop, plus an
``InMemoryArtifactStore`` for unit tests.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from orchestrator.storage import ArtifactNotFoundError, ObjectStoreClient


class ArtifactStore(Protocol):
    """The minimum surface the runtime needs for artifact-by-reference."""

    async def put_json(self, artifact_id: str, body: dict[str, Any]) -> None: ...

    async def get_json(self, artifact_id: str) -> dict[str, Any]: ...


class ObjectStoreArtifactStore:
    """Production implementation backed by S3-compatible object storage."""

    def __init__(self, client: ObjectStoreClient | None = None) -> None:
        self._client = client or ObjectStoreClient()

    async def put_json(self, artifact_id: str, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str, ensure_ascii=False).encode("utf-8")
        await self._client.put_object(
            self._client.settings.artifacts_bucket,
            artifact_id,
            payload,
            content_type="application/json",
        )

    async def get_json(self, artifact_id: str) -> dict[str, Any]:
        try:
            raw = await self._client.get_object(self._client.settings.artifacts_bucket, artifact_id)
        except ArtifactNotFoundError as exc:
            raise LookupError(str(exc)) from exc
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"artifact {artifact_id!r} is not utf-8 JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"artifact {artifact_id!r} did not deserialise to a JSON object")
        return loaded


class InMemoryArtifactStore:
    """Dev/test implementation. Holds bytes in a process-local dict."""

    def __init__(self) -> None:
        self._blobs: dict[str, dict[str, Any]] = {}

    async def put_json(self, artifact_id: str, body: dict[str, Any]) -> None:
        self._blobs[artifact_id] = body

    async def get_json(self, artifact_id: str) -> dict[str, Any]:
        if artifact_id not in self._blobs:
            raise LookupError(f"artifact {artifact_id!r} not found")
        return dict(self._blobs[artifact_id])

    def keys(self) -> list[str]:
        return list(self._blobs)


def make_artifact_id(*, task_id: str, node_id: str, suffix: str = "output") -> str:
    """Stable key for a specialist's terminal output.

    The artifact namespace is flat; key shape is
    ``task/<task_id>/<node_id>/<suffix>.json`` so an operator browsing
    the bucket can locate any task's outputs without consulting the audit
    log.
    """
    return f"task/{task_id}/{node_id}/{suffix}.json"
