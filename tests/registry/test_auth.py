"""Web login / session auth (unified UI — P0b)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.deps import principal_from_session, require_principal
from orchestrator.registry.api.session import COOKIE_NAME, read_session, sign_session


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


class TestSignedSession:
    def test_roundtrip(self) -> None:
        token = sign_session({"id": "alice", "tenant_id": "acme", "roles": ["approver"]}, "s3cr3t")
        data = read_session(token, "s3cr3t")
        assert data is not None and data["id"] == "alice" and data["roles"] == ["approver"]

    def test_rejects_wrong_secret(self) -> None:
        token = sign_session({"id": "alice"}, "s3cr3t")
        assert read_session(token, "different") is None

    def test_rejects_tamper(self) -> None:
        token = sign_session({"id": "alice"}, "s3cr3t")
        body, _, sig = token.partition(".")
        assert read_session(f"{body}x.{sig}", "s3cr3t") is None  # body changed → bad sig

    def test_rejects_expired(self) -> None:
        token = sign_session({"id": "alice"}, "s3cr3t", now=0.0)  # exp = 12h after epoch
        assert read_session(token, "s3cr3t", now=0.0) is not None
        assert read_session(token, "s3cr3t", now=10**12) is None  # far future → expired


def test_principal_from_session_reconstructs_roles() -> None:
    secret = "dev-session-secret"
    token = sign_session({"id": "bob", "tenant_id": "t1", "roles": ["a", "b"]}, secret)
    request = SimpleNamespace(
        cookies={COOKIE_NAME: token}, app=SimpleNamespace(state=SimpleNamespace(settings=Settings()))
    )
    principal = principal_from_session(request)  # type: ignore[arg-type]
    assert principal is not None
    assert principal.id == "bob" and principal.tenant_id == "t1"
    assert principal.roles == frozenset({"a", "b"})


def test_require_principal_accepts_a_session_cookie() -> None:
    secret = "dev-session-secret"
    token = sign_session({"id": "carol", "roles": ["*"]}, secret)
    request = SimpleNamespace(
        cookies={COOKIE_NAME: token}, app=SimpleNamespace(state=SimpleNamespace(settings=Settings()))
    )
    principal = require_principal(request, x_api_key=None)  # type: ignore[arg-type]
    assert principal.id == "carol"


async def test_login_sets_a_session_cookie_and_logout_clears_it() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/login", json={"api_key": "dev-key"})
        assert resp.status_code == 204
        assert COOKIE_NAME in resp.cookies
        # The cookie now authenticates the protected pages.
        page = await client.get("/app")
        assert page.status_code == 200
        # Logout clears the cookie and redirects to /login.
        out = await client.get("/logout")
        assert out.status_code == 303 and out.headers["location"] == "/login"


async def test_login_page_is_open() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Sign in" in resp.text and "/static/login.js" in resp.text
