"""Tool invocation endpoint."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from orchestrator.gateway.auth import CredentialError, resolve_credentials
from orchestrator.gateway.invocation import InvocationContext, InvocationOutcome
from orchestrator.gateway.loader import LoadedTool, LoaderReport
from orchestrator.gateway.rate_limit import RateLimiter
from orchestrator.registry.api.deps import ApiKeyDep, SessionDep, TraceIdDep
from orchestrator.registry.repositories import AuditLogRepo

logger = logging.getLogger("orchestrator.gateway.invoke")

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class InvokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str
    tool_version: str
    output: dict[str, Any]
    elapsed_ms: float
    cost_usd: float


def _inputs_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post(
    "/{tool_id}/{version}/invoke",
    response_model=InvokeResponse,
    responses={
        404: {"description": "Tool not registered."},
        429: {"description": "Rate limit exceeded."},
        503: {"description": "Credential resolution failed."},
        500: {"description": "Handler raised."},
    },
)
async def invoke(
    tool_id: str,
    version: str,
    inputs: Annotated[dict[str, Any], Body(default_factory=dict)],
    request: Request,
    session: SessionDep,
    actor: ApiKeyDep,
    trace_id: TraceIdDep,
    task_id: Annotated[str | None, Header(alias="X-Task-Id")] = None,
    agent_template_id: Annotated[str | None, Header(alias="X-Agent-Template-Id")] = None,
) -> InvokeResponse:
    report: LoaderReport = request.app.state.loader_report
    limiter: RateLimiter = request.app.state.rate_limiter

    loaded: LoadedTool | None = report.by_id_version(tool_id, version)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Tool {tool_id}@{version} not registered.")

    tool_key = f"{tool_id}@{version}"
    allowed, retry_after = await limiter.check(tool_key, loaded.spec.get("rate_limits"))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {tool_key}.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    try:
        creds = resolve_credentials(
            contract_id=tool_id, version=version, auth_spec=loaded.spec.get("authentication")
        )
    except CredentialError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ctx = InvocationContext(
        tool_id=tool_id,
        tool_version=version,
        trace_id=trace_id,
        actor=actor,
        task_id=task_id,
        agent_template_id=agent_template_id,
        credentials=creds.secrets,
    )

    audit_payload: dict[str, Any] = {
        "tool_id": tool_id,
        "tool_version": version,
        "inputs_hash": _inputs_hash(inputs),
        "task_id": task_id,
        "agent_template_id": agent_template_id,
        "outcome": InvocationOutcome.SUCCESS.value,
        "elapsed_ms": 0.0,
        "cost_usd": 0.0,
    }

    start = time.perf_counter()
    try:
        output = await loaded.handler(inputs, ctx)
    except Exception as exc:  # handler exceptions are observable, not fatal
        elapsed_ms = (time.perf_counter() - start) * 1000
        audit_payload.update(
            outcome=InvocationOutcome.ERROR.value,
            elapsed_ms=round(elapsed_ms, 3),
            error_type=type(exc).__name__,
            error_message=str(exc)[:512],
        )
        await AuditLogRepo(session).write(
            actor=actor,
            action="tool_invocation",
            resource_type="tool_contract",
            resource_id=tool_key,
            after=audit_payload,
            trace_id=trace_id,
        )
        await session.commit()
        logger.exception("gateway.invoke.error", extra={"tool_id": tool_id, "version": version})
        raise HTTPException(status_code=500, detail=f"Handler raised: {type(exc).__name__}") from exc

    elapsed_ms = (time.perf_counter() - start) * 1000
    cost_usd = float(output.pop("__cost_usd__", 0.0)) if isinstance(output, dict) else 0.0
    audit_payload.update(
        elapsed_ms=round(elapsed_ms, 3),
        cost_usd=cost_usd,
    )
    await AuditLogRepo(session).write(
        actor=actor,
        action="tool_invocation",
        resource_type="tool_contract",
        resource_id=tool_key,
        after=audit_payload,
        trace_id=trace_id,
    )
    await session.commit()

    return InvokeResponse(
        tool_id=tool_id,
        tool_version=version,
        output=output,
        elapsed_ms=round(elapsed_ms, 3),
        cost_usd=cost_usd,
    )
