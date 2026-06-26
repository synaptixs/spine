from __future__ import annotations

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings


def _no_db_app() -> object:
    settings = Settings(database_url="postgresql+psycopg://stub/stub")
    app = create_app(settings)
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def test_trace_id_is_stamped_when_missing() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert "X-Trace-Id" in response.headers
    assert len(response.headers["X-Trace-Id"]) >= 16


async def test_trace_id_is_propagated_when_provided() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz", headers={"X-Trace-Id": "fixed-trace-123"})
    assert response.headers["X-Trace-Id"] == "fixed-trace-123"
