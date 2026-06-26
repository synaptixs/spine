from __future__ import annotations

from typing import Any

import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import FetchArtifactHandler, QueryDocumentStoreHandler
from orchestrator.storage import ArtifactNotFoundError, ObjectStoreSettings


class _StubObjectStore:
    """Minimal stand-in for ObjectStoreClient that holds bytes in memory."""

    def __init__(self, blobs: dict[tuple[str, str], bytes]) -> None:
        self._blobs = blobs
        self.settings = ObjectStoreSettings(
            endpoint_url=None,
            region_name="us-east-1",
            access_key_id=None,
            secret_access_key=None,
            artifacts_bucket="artifacts",
            documents_bucket="documents",
        )

    async def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        body = self._blobs.get((bucket, key))
        if body is None:
            raise ArtifactNotFoundError(f"{bucket}/{key} not found")
        return {
            "content_type": "text/plain",
            "content_length": len(body),
            "etag": "abc123",
            "last_modified": None,
            "user_metadata": {},
        }

    async def get_object(self, bucket: str, key: str) -> bytes:
        body = self._blobs.get((bucket, key))
        if body is None:
            raise ArtifactNotFoundError(f"{bucket}/{key} not found")
        return body


def _ctx(tool_id: str) -> InvocationContext:
    return InvocationContext(tool_id=tool_id, tool_version="0.1.0", trace_id="t", actor="dev")


async def test_fetch_artifact_returns_inline_text() -> None:
    store = _StubObjectStore({("artifacts", "art_001"): b"hello world"})
    handler = FetchArtifactHandler(client=store)  # type: ignore[arg-type]
    out = await handler({"artifact_id": "art_001"}, _ctx("tool.fetch_artifact"))
    assert out["content"] == "hello world"
    assert out["content_base64"] is None
    assert out["truncated"] is False
    assert out["content_length"] == 11


async def test_fetch_artifact_returns_base64_for_binary() -> None:
    binary = bytes(range(256))
    store = _StubObjectStore({("artifacts", "art_bin"): binary})
    handler = FetchArtifactHandler(client=store)  # type: ignore[arg-type]
    out = await handler({"artifact_id": "art_bin"}, _ctx("tool.fetch_artifact"))
    assert out["content"] is None
    assert out["content_base64"] is not None


async def test_fetch_artifact_missing_raises_lookup_error() -> None:
    handler = FetchArtifactHandler(client=_StubObjectStore({}))  # type: ignore[arg-type]
    with pytest.raises(LookupError):
        await handler({"artifact_id": "missing"}, _ctx("tool.fetch_artifact"))


async def test_fetch_artifact_empty_id_rejected() -> None:
    handler = FetchArtifactHandler(client=_StubObjectStore({}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="artifact_id"):
        await handler({}, _ctx("tool.fetch_artifact"))


async def test_query_document_store_returns_inline_text() -> None:
    store = _StubObjectStore({("documents", "doc_001"): b"document body"})
    handler = QueryDocumentStoreHandler(client=store)  # type: ignore[arg-type]
    out = await handler({"document_id": "doc_001"}, _ctx("tool.query_document_store"))
    assert out["document_id"] == "doc_001"
    assert "artifact_id" not in out
    assert out["content"] == "document body"


async def test_query_document_store_missing_raises_lookup_error() -> None:
    handler = QueryDocumentStoreHandler(client=_StubObjectStore({}))  # type: ignore[arg-type]
    with pytest.raises(LookupError):
        await handler({"document_id": "missing"}, _ctx("tool.query_document_store"))
