"""FastAPI app for the tool gateway."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orchestrator.gateway.api.routes import router as tools_router
from orchestrator.gateway.handlers import HandlerRegistry, get_default_registry
from orchestrator.gateway.loader import LoaderReport, load_published_tools
from orchestrator.gateway.rate_limit import RateLimiter
from orchestrator.registry.api.config import Settings, get_settings
from orchestrator.registry.api.middleware import TraceIdMiddleware
from orchestrator.registry.db.session import make_engine, make_session_factory

logger = logging.getLogger("orchestrator.gateway")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url, echo=settings.db_echo)
    factory = make_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = factory

    registry: HandlerRegistry = app.state.handler_registry
    async with factory() as session:
        # Onboard configured MCP servers' tools (publish contracts + register
        # handlers) before matching, so they appear in the loaded set. Best-effort:
        # a missing/down MCP server must never block gateway startup.
        try:
            from orchestrator.mcp.onboard import onboard_mcp_tools

            onboarded = await onboard_mcp_tools(session, registry)
            if onboarded:
                logger.info("gateway.mcp_onboarded", extra={"count": len(onboarded)})
        except Exception:  # noqa: BLE001 — MCP onboarding is optional, never fatal
            logger.warning("gateway.mcp_onboard_failed", exc_info=True)
        report: LoaderReport = await load_published_tools(session, registry)
    app.state.loader_report = report

    logger.info(
        "gateway.startup",
        extra={
            "loaded": len(report.loaded),
            "unmatched_handlers": len(report.unmatched_handlers),
            "unhandled_contracts": len(report.unhandled_contracts),
        },
    )
    for cid, ver in report.unmatched_handlers:
        logger.warning("gateway.unmatched_handler", extra={"tool_id": cid, "version": ver})
    for cid, ver in report.unhandled_contracts:
        logger.info("gateway.unhandled_contract", extra={"tool_id": cid, "version": ver})

    try:
        yield
    finally:
        await engine.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    handler_registry: HandlerRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Orchestrator Tool Gateway",
        version="0.0.0",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.handler_registry = handler_registry or get_default_registry()
    app.state.rate_limiter = RateLimiter()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> dict[str, object]:
        report: LoaderReport = app.state.loader_report
        return {"status": "ready", "loaded_tools": len(report.loaded)}

    app.include_router(tools_router)

    return app
