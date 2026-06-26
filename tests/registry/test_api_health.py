from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.deps import require_principal


def _no_db_app() -> object:
    """App without a real DB — overrides lifespan to skip engine setup."""
    settings = Settings(database_url="postgresql+psycopg://stub/stub")
    app = create_app(settings)
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


def _fake_request(api_key: str) -> object:
    """Stub Request just enough for require_api_key to read app.state.settings."""
    state = SimpleNamespace(settings=Settings(api_key=api_key))
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


async def test_healthz_returns_ok() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_require_api_key_rejects_missing() -> None:
    with pytest.raises(Exception) as exc:
        require_principal(_fake_request("secret"), x_api_key=None)  # type: ignore[arg-type]
    assert "API key" in str(exc.value.detail)  # type: ignore[attr-defined]


def test_require_api_key_rejects_wrong() -> None:
    with pytest.raises(Exception):
        require_principal(_fake_request("secret"), x_api_key="nope")  # type: ignore[arg-type]


def test_require_api_key_accepts_correct() -> None:
    # No principals map configured → default wildcard principal in tenant "default".
    principal = require_principal(_fake_request("secret"), x_api_key="secret")  # type: ignore[arg-type]
    assert principal.id == "secret"
    assert principal.tenant_id == "default"
    assert principal.has_role("anything")  # wildcard role
