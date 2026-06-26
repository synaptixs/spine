"""query_document_store: retrieve a document by id from the documents bucket.

Different bucket from fetch_artifact (documents vs artifacts) so the two tools
can carry independent retention and access policies once those are wired up.
"""

from __future__ import annotations

from typing import Any

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools.fetch_artifact import _decode_body
from orchestrator.storage import ArtifactNotFoundError, ObjectStoreClient

MAX_INLINE_BYTES = 4 * 1024 * 1024  # 4 MiB — documents can be larger than artifacts


class QueryDocumentStoreHandler:
    contract_id: str = "tool.query_document_store"
    contract_version: str = "0.1.0"

    def __init__(self, client: ObjectStoreClient | None = None) -> None:
        self._client = client or ObjectStoreClient()

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx
        document_id = str(inputs.get("document_id", "")).strip()
        if not document_id:
            raise ValueError("query_document_store: 'document_id' is required")

        bucket = self._client.settings.documents_bucket
        try:
            metadata = await self._client.head_object(bucket, document_id)
        except ArtifactNotFoundError as exc:
            raise LookupError(str(exc)) from exc

        if metadata["content_length"] > MAX_INLINE_BYTES:
            return {
                "document_id": document_id,
                "content_type": metadata["content_type"],
                "content_length": metadata["content_length"],
                "etag": metadata["etag"],
                "user_metadata": metadata["user_metadata"],
                "truncated": True,
                "content": None,
                "content_base64": None,
            }

        body = await self._client.get_object(bucket, document_id)
        decoded = _decode_body(document_id, body, metadata)
        decoded["document_id"] = decoded.pop("artifact_id")
        return decoded


QUERY_DOCUMENT_STORE_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.query_document_store",
        "version": "0.1.0",
        "description": "Retrieve a stored document by id from the documents bucket.",
        "tags": ["object-store", "document"],
    },
    "spec": {
        "purpose": "Return the bytes and metadata of a document identified by key.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 120, "burst": 20},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
