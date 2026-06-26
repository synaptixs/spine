"""FastAPI application factory for the registry service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.codereview.webhook import router as github_webhook_router
from orchestrator.core.llm import LiteLLMClient, LLMClient
from orchestrator.registry.api.approvals import router as approvals_router
from orchestrator.registry.api.backlog import router as backlog_router
from orchestrator.registry.api.config import Settings, get_settings
from orchestrator.registry.api.console import router as console_router
from orchestrator.registry.api.inbox import router as inbox_router
from orchestrator.registry.api.middleware import TraceIdMiddleware
from orchestrator.registry.api.personas import router as personas_router
from orchestrator.registry.api.routes import (
    agent_templates_router,
    glossary_entries_router,
    tool_contracts_router,
)
from orchestrator.registry.api.runs import router as runs_router
from orchestrator.registry.api.stream import router as stream_router
from orchestrator.registry.api.tasks import router as tasks_router
from orchestrator.registry.api.trace import router as trace_router
from orchestrator.registry.api.web import STATIC_DIR, web_router
from orchestrator.registry.api.web.auth import WebAuthRequiredError, web_auth_redirect
from orchestrator.registry.api.web.auth import router as auth_router
from orchestrator.registry.db.session import make_engine, make_session_factory

logger = logging.getLogger("orchestrator.registry")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = make_engine(settings.database_url, echo=settings.db_echo)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    if app.state.llm_client is None:
        app.state.llm_client = LiteLLMClient()
    logger.info("registry.startup", extra={"database_url_host": _host_only(settings.database_url)})
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("registry.shutdown")


def _host_only(url: str) -> str:
    """Extract host:port for logging without leaking creds."""
    try:
        return url.split("@", 1)[1].split("/", 1)[0]
    except IndexError:
        return "unknown"


def create_app(
    settings: Settings | None = None,
    *,
    llm_client: LLMClient | None = None,
    github_app_config: GitHubAppConfig | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    github_app_config = github_app_config or GitHubAppConfig()

    app = FastAPI(
        title="Orchestrator Registry",
        version="0.0.0",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.llm_client = llm_client
    app.state.github_app_config = github_app_config
    # Backlog-preview service builder; None → the Confluence factory (tests inject a fake).
    app.state.intake_service_builder = None
    app.add_middleware(TraceIdMiddleware)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> dict[str, str]:
        engine = app.state.engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(agent_templates_router)
    app.include_router(tool_contracts_router)
    app.include_router(glossary_entries_router)
    app.include_router(tasks_router)
    app.include_router(trace_router)
    app.include_router(approvals_router)
    app.include_router(runs_router)
    app.include_router(personas_router)
    app.include_router(stream_router)
    app.include_router(console_router)

    # Unified UI (P0): login/logout, home landing, the folded-in backlog preview,
    # and the shared static assets (one nav, one stylesheet). The web pages require
    # a session (P0b); an unauthenticated navigation redirects to /login.
    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(inbox_router)
    app.include_router(backlog_router)
    app.add_exception_handler(WebAuthRequiredError, web_auth_redirect)  # type: ignore[arg-type]
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Block A: the PR-reviewer webhook mounts only when explicitly enabled,
    # so the platform's existing surface is unchanged when unconfigured.
    if github_app_config.enabled:
        app.include_router(github_webhook_router)

    return app


app = create_app
