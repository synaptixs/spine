"""fetch_artifact: retrieve a stored artifact (notebook, dataframe, log, etc.).

Artifacts live in the configured object store under the ``artifacts`` bucket.
Returns the bytes (base64-encoded when not utf-8 text) plus metadata.
"""

from __future__ import annotations

import base64
from typing import Any

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.storage import ArtifactNotFoundError, ObjectStoreClient

MAX_INLINE_BYTES = 2 * 1024 * 1024  # 2 MiB — same cap fetch_url uses


class FetchArtifactHandler:
    contract_id: str = "tool.fetch_artifact"
    contract_version: str = "0.1.0"

    def __init__(self, client: ObjectStoreClient | None = None) -> None:
        self._client = client or ObjectStoreClient()

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx
        artifact_id = str(inputs.get("artifact_id", "")).strip()
        if not artifact_id:
            raise ValueError("fetch_artifact: 'artifact_id' is required")

        bucket = self._client.settings.artifacts_bucket
        try:
            metadata = await self._client.head_object(bucket, artifact_id)
        except ArtifactNotFoundError as exc:
            raise LookupError(str(exc)) from exc

        if metadata["content_length"] > MAX_INLINE_BYTES:
            return {
                "artifact_id": artifact_id,
                "content_type": metadata["content_type"],
                "content_length": metadata["content_length"],
                "etag": metadata["etag"],
                "user_metadata": metadata["user_metadata"],
                "truncated": True,
                "content": None,
                "content_base64": None,
            }

        body = await self._client.get_object(bucket, artifact_id)
        return _decode_body(artifact_id, body, metadata)


def _decode_body(artifact_id: str, body: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    content: str | None
    content_base64: str | None
    try:
        content = body.decode("utf-8")
        content_base64 = None
    except UnicodeDecodeError:
        content = None
        content_base64 = base64.b64encode(body).decode("ascii")
    return {
        "artifact_id": artifact_id,
        "content_type": metadata["content_type"],
        "content_length": metadata["content_length"],
        "etag": metadata["etag"],
        "user_metadata": metadata["user_metadata"],
        "truncated": False,
        "content": content,
        "content_base64": content_base64,
    }


FETCH_ARTIFACT_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.fetch_artifact",
        "version": "0.1.0",
        "description": "Retrieve a stored artifact by id from the artifacts bucket.",
        "tags": ["object-store", "artifact"],
    },
    "spec": {
        "purpose": "Return the bytes and metadata of an artifact identified by key.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 120, "burst": 20},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
