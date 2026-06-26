"""Remote plugin auth: token verifiers + env-driven AuthSettings wiring."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra")

from orchestrator.plugin.auth import (  # noqa: E402
    IntrospectionTokenVerifier,
    StaticTokenVerifier,
    build_auth_from_env,
)

_MCP_ENV = (
    "ORCHESTRATOR_MCP_TOKEN",
    "ORCHESTRATOR_MCP_INTROSPECTION_URL",
    "ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_ID",
    "ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_SECRET",
    "ORCHESTRATOR_MCP_ISSUER_URL",
    "ORCHESTRATOR_MCP_RESOURCE_URL",
    "ORCHESTRATOR_MCP_REQUIRED_SCOPES",
)


@pytest.fixture(autouse=True)
def _clean_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _MCP_ENV:
        monkeypatch.delenv(name, raising=False)


# ---- StaticTokenVerifier ----------------------------------------------------


async def test_static_accepts_exact_secret() -> None:
    v = StaticTokenVerifier("s3cret", scopes=["sdlc"])
    tok = await v.verify_token("s3cret")
    assert tok is not None and tok.scopes == ["sdlc"] and tok.client_id == "static"


async def test_static_rejects_wrong_or_empty_token() -> None:
    v = StaticTokenVerifier("s3cret")
    assert await v.verify_token("nope") is None
    assert await v.verify_token("") is None


def test_static_requires_a_secret() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        StaticTokenVerifier("")


# ---- IntrospectionTokenVerifier (RFC 7662) ----------------------------------


def _patch_introspection(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Route the verifier's httpx client at a MockTransport (capturing the real class)."""
    import httpx

    real = httpx.AsyncClient  # bind before patching, or the lambda recurses
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


async def test_introspection_active_token_maps_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _patch_introspection(
        monkeypatch,
        lambda r: httpx.Response(200, json={"active": True, "scope": "sdlc admin", "client_id": "app-1"}),
    )
    v = IntrospectionTokenVerifier("https://idp.example/introspect")
    tok = await v.verify_token("opaque")
    assert tok is not None and tok.client_id == "app-1"
    assert set(tok.scopes) == {"sdlc", "admin"}


async def test_introspection_inactive_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _patch_introspection(monkeypatch, lambda r: httpx.Response(200, json={"active": False}))
    v = IntrospectionTokenVerifier("https://idp.example/introspect")
    assert await v.verify_token("opaque") is None


async def test_introspection_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    expired = {"active": True, "scope": "sdlc", "exp": int(time.time()) - 60}
    _patch_introspection(monkeypatch, lambda r: httpx.Response(200, json=expired))
    v = IntrospectionTokenVerifier("https://idp.example/introspect")
    assert await v.verify_token("opaque") is None


async def test_introspection_network_error_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _patch_introspection(monkeypatch, _boom)
    v = IntrospectionTokenVerifier("https://idp.example/introspect")
    assert await v.verify_token("opaque") is None


# ---- build_auth_from_env ----------------------------------------------------


def test_env_no_verifier_returns_none() -> None:
    assert build_auth_from_env() == (None, None)


def test_env_static_token_builds_resource_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.setenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES", "sdlc, admin")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, StaticTokenVerifier)
    assert settings is not None and settings.required_scopes == ["sdlc", "admin"]
    # issuer defaults to the resource URL for the static (no real AS) path.
    assert str(settings.issuer_url).startswith("https://mcp.example.com")


def test_env_introspection_wins_over_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", "https://idp.example/introspect")
    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, IntrospectionTokenVerifier)
    assert settings is not None


def test_env_verifier_without_any_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")  # no issuer/resource URL
    with pytest.raises(ValueError, match="ISSUER_URL"):
        build_auth_from_env()
