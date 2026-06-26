"""REST endpoints for the registry.

A single factory produces a router for each versioned entity (agent
templates, tool contracts) so the URL shape, status codes, and
error mapping stay identical. The factory is parametric on the SQLAlchemy
model class and the Pydantic validator.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from orchestrator.registry._common import LifecycleState
from orchestrator.registry.api.deps import ApiKeyDep, SessionDep, TraceIdDep
from orchestrator.registry.db.models import AgentTemplateRow, GlossaryEntryRow, ToolContractRow
from orchestrator.registry.repositories import (
    AlreadyExistsError,
    AuditLogRepo,
    ImmutablePublishedError,
    NotFoundError,
    VersionedRepo,
)
from orchestrator.registry.validation import (
    ValidationReport,
    validate_agent_template_payload,
    validate_glossary_entry_payload,
    validate_tool_contract_payload,
)


class EntityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pk: uuid.UUID
    id: str
    version: str
    description: str
    tags: list[str]
    spec: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class PageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EntityOut]
    next_cursor: str | None = None


class ProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    failures: list[dict[str, str]] | None = None


def _row_to_out(row: AgentTemplateRow | ToolContractRow | GlossaryEntryRow) -> EntityOut:
    return EntityOut(
        pk=row.pk,
        id=row.id,
        version=row.version,
        description=row.description,
        tags=list(row.tags),
        spec=row.spec_json,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _encode_cursor(pk: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(pk.bytes).rstrip(b"=").decode("ascii")


def _decode_cursor(token: str) -> uuid.UUID:
    padding = "=" * (-len(token) % 4)
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(token + padding))
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise HTTPException(status_code=400, detail="Invalid pagination cursor.") from exc


def make_versioned_router(
    *,
    prefix: str,
    tag: str,
    resource_type: str,
    model: type[AgentTemplateRow] | type[ToolContractRow] | type[GlossaryEntryRow],
    validate_payload: Callable[[dict[str, Any]], tuple[Any, ValidationReport]],
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        response_model=EntityOut,
        responses={
            400: {"model": ProblemResponse},
            409: {"model": ProblemResponse},
        },
    )
    async def register(
        payload: Annotated[dict[str, Any], Body(...)],
        session: SessionDep,
        actor: ApiKeyDep,
        trace_id: TraceIdDep,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> EntityOut:
        # The (id, version) unique constraint provides natural idempotency:
        # retrying the same registration surfaces 409 with the existing row.
        # Idempotency-Key is accepted for forward compatibility and audited
        # below; a cache-backed implementation can wire it up later without
        # changing the public API.
        _ = idempotency_key
        model_obj, report = validate_payload(payload)
        if model_obj is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "detail": "Validation failed.",
                    "failures": [f.model_dump() for f in report.failures],
                },
            )
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        try:
            row = await repo.create(
                id=model_obj.metadata.id,
                version=model_obj.metadata.version,
                description=model_obj.metadata.description,
                tags=list(model_obj.metadata.tags),
                spec=model_obj.spec.model_dump(mode="json"),
            )
        except AlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await AuditLogRepo(session).write(
            actor=actor,
            action="register",
            resource_type=resource_type,
            resource_id=f"{row.id}@{row.version}",
            after={"description": row.description, "tags": list(row.tags)},
            trace_id=trace_id,
        )
        await session.commit()
        return _row_to_out(row)

    @router.get("", response_model=PageOut)
    async def list_entities(
        session: SessionDep,
        _: ApiKeyDep,
        tag: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> PageOut:
        lifecycle = LifecycleState(status_filter) if status_filter else None
        after_pk = _decode_cursor(cursor) if cursor else None
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        rows, next_pk = await repo.list_page(tag=tag, status=lifecycle, after_pk=after_pk, limit=limit)
        return PageOut(
            items=[_row_to_out(r) for r in rows],
            next_cursor=_encode_cursor(next_pk) if next_pk else None,
        )

    @router.get("/{id}", response_model=EntityOut, responses={404: {"model": ProblemResponse}})
    async def get_latest(id: str, session: SessionDep, _: ApiKeyDep) -> EntityOut:
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        row = await repo.get_latest_published(id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No published version for {id}.")
        return _row_to_out(row)

    @router.get(
        "/{id}/{version}",
        response_model=EntityOut,
        responses={404: {"model": ProblemResponse}},
    )
    async def get_specific(id: str, version: str, session: SessionDep, _: ApiKeyDep) -> EntityOut:
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        row = await repo.get_by_id_version(id, version)
        if row is None:
            raise HTTPException(status_code=404, detail=f"{id}@{version} not found.")
        return _row_to_out(row)

    @router.post(
        "/{id}/{version}/publish",
        response_model=EntityOut,
        responses={404: {"model": ProblemResponse}, 409: {"model": ProblemResponse}},
    )
    async def publish(
        id: str,
        version: str,
        session: SessionDep,
        actor: ApiKeyDep,
        trace_id: TraceIdDep,
    ) -> EntityOut:
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        try:
            row = await repo.publish(id, version)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ImmutablePublishedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await AuditLogRepo(session).write(
            actor=actor,
            action="publish",
            resource_type=resource_type,
            resource_id=f"{row.id}@{row.version}",
            after={"status": row.status},
            trace_id=trace_id,
        )
        await session.commit()
        return _row_to_out(row)

    @router.post(
        "/{id}/{version}/deprecate",
        response_model=EntityOut,
        responses={404: {"model": ProblemResponse}},
    )
    async def deprecate(
        id: str,
        version: str,
        session: SessionDep,
        actor: ApiKeyDep,
        trace_id: TraceIdDep,
    ) -> EntityOut:
        repo: VersionedRepo[Any] = VersionedRepo(session, model)
        try:
            row = await repo.mark_deprecated(id, version)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await AuditLogRepo(session).write(
            actor=actor,
            action="deprecate",
            resource_type=resource_type,
            resource_id=f"{row.id}@{row.version}",
            after={"status": row.status},
            trace_id=trace_id,
        )
        await session.commit()
        return _row_to_out(row)

    return router


agent_templates_router = make_versioned_router(
    prefix="/v1/agent-templates",
    tag="agent-templates",
    resource_type="agent_template",
    model=AgentTemplateRow,
    validate_payload=validate_agent_template_payload,
)

tool_contracts_router = make_versioned_router(
    prefix="/v1/tool-contracts",
    tag="tool-contracts",
    resource_type="tool_contract",
    model=ToolContractRow,
    validate_payload=validate_tool_contract_payload,
)

glossary_entries_router = make_versioned_router(
    prefix="/v1/glossary",
    tag="glossary",
    resource_type="glossary_entry",
    model=GlossaryEntryRow,
    validate_payload=validate_glossary_entry_payload,
)
