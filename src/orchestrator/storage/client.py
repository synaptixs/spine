"""Async S3-compatible object-store client.

Wraps ``aioboto3``. Production targets real S3 (set ``OBJECT_STORE_ENDPOINT_URL``
to None — i.e. unset — and configure AWS creds normally). Local dev points at
the MinIO container in ``docker-compose.dev.yml`` (endpoint
``http://localhost:9000``, creds ``minio_admin`` / ``minio_admin_password``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import aioboto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]


class ObjectStoreError(RuntimeError):
    """Base class for object-store failures."""


class ArtifactNotFoundError(ObjectStoreError):
    """No object at the requested key."""


@dataclass(frozen=True)
class ObjectStoreSettings:
    endpoint_url: str | None
    region_name: str
    access_key_id: str | None
    secret_access_key: str | None
    artifacts_bucket: str
    documents_bucket: str

    @classmethod
    def from_env(cls) -> ObjectStoreSettings:
        return cls(
            endpoint_url=os.getenv("OBJECT_STORE_ENDPOINT_URL", "http://localhost:9000") or None,
            region_name=os.getenv("OBJECT_STORE_REGION", "us-east-1"),
            access_key_id=os.getenv("OBJECT_STORE_ACCESS_KEY_ID", "minio_admin"),
            secret_access_key=os.getenv("OBJECT_STORE_SECRET_ACCESS_KEY", "minio_admin_password"),
            artifacts_bucket=os.getenv("OBJECT_STORE_ARTIFACTS_BUCKET", "artifacts"),
            documents_bucket=os.getenv("OBJECT_STORE_DOCUMENTS_BUCKET", "documents"),
        )


class ObjectStoreClient:
    """Thin async wrapper over aioboto3's S3 client.

    Caches an ``aioboto3.Session`` and yields short-lived S3 clients per call
    via ``async with`` so connection state stays clean across long-running
    services.
    """

    def __init__(self, settings: ObjectStoreSettings | None = None) -> None:
        self._settings = settings or ObjectStoreSettings.from_env()
        self._session = aioboto3.Session(
            aws_access_key_id=self._settings.access_key_id,
            aws_secret_access_key=self._settings.secret_access_key,
            region_name=self._settings.region_name,
        )

    @property
    def settings(self) -> ObjectStoreSettings:
        return self._settings

    def _client(self) -> Any:
        return self._session.client("s3", endpoint_url=self._settings.endpoint_url)

    async def get_object(self, bucket: str, key: str) -> bytes:
        async with self._client() as s3:
            try:
                resp = await s3.get_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise ArtifactNotFoundError(f"{bucket}/{key} not found") from exc
                raise ObjectStoreError(f"{type(exc).__name__}: {exc}") from exc
            body: bytes = await resp["Body"].read()
            return body

    async def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        async with self._client() as s3:
            try:
                resp = await s3.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404", "NotFound"}:
                    raise ArtifactNotFoundError(f"{bucket}/{key} not found") from exc
                raise ObjectStoreError(f"{type(exc).__name__}: {exc}") from exc
            metadata: dict[str, Any] = {
                "content_type": resp.get("ContentType", "application/octet-stream"),
                "content_length": int(resp.get("ContentLength", 0)),
                "etag": resp.get("ETag", "").strip('"'),
                "last_modified": resp.get("LastModified"),
                "user_metadata": dict(resp.get("Metadata", {}) or {}),
            }
            return metadata

    async def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._client() as s3:
            try:
                await s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata=metadata or {},
                )
            except ClientError as exc:
                raise ObjectStoreError(f"{type(exc).__name__}: {exc}") from exc
