"""Remote plugin auth: token verifiers + env-driven AuthSettings wiring."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra")

from orchestrator.plugin.auth import (
    ALL_SCOPES,
    SCOPE_PLAN,
    SCOPE_READ,
    SCOPE_RUN,
    IntrospectionTokenVerifier,
    StaticTokenVerifier,
    build_auth_from_env,
    expand_scopes,  # noqa: E402
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
    v = StaticTokenVerifier("s3cret", scopes=[SCOPE_READ])
    tok = await v.verify_token("s3cret")
    assert tok is not None and tok.scopes == [SCOPE_READ] and tok.client_id == "static"


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
    # The IdP's scopes as issued — with the legacy `sdlc` expanded to the three tier scopes.
    assert set(tok.scopes) == {"admin", *ALL_SCOPES}


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


async def test_env_static_token_builds_resource_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.setenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES", "sdlc, admin")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, StaticTokenVerifier)
    # The env var is what the static token *carries* (legacy `sdlc` expanded); what the SDK
    # requires of every token is the floor, `spine:read` — the tiers above are per tool.
    assert settings is not None and settings.required_scopes == [SCOPE_READ]
    tok = await verifier.verify_token("s3cret")
    assert tok is not None and set(tok.scopes) == {"admin", *ALL_SCOPES}
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


# ---- scopes follow the tiers -------------------------------------------------------


def test_the_legacy_scope_expands_to_every_tier_once() -> None:
    """A token minted for the one `sdlc` scope keeps working for a release: it reads as all
    three tier scopes. Anything else passes through untouched, order kept."""
    assert expand_scopes(["sdlc"]) == ALL_SCOPES
    assert expand_scopes(["admin", "sdlc"]) == ["admin", *ALL_SCOPES]
    assert expand_scopes([SCOPE_READ]) == [SCOPE_READ]
    assert expand_scopes([SCOPE_RUN, "sdlc"]) == [SCOPE_RUN, SCOPE_READ, SCOPE_PLAN]  # no duplicate


async def test_a_static_token_carries_every_tier_by_default() -> None:
    """Unset env → the single-tenant self-host behaves as before: one token, every tier."""
    tok = await StaticTokenVerifier("s3cret").verify_token("s3cret")
    assert tok is not None and tok.scopes == ALL_SCOPES


async def test_a_read_only_static_token_is_one_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.setenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES", SCOPE_READ)
    settings, verifier = build_auth_from_env()
    assert settings is not None and verifier is not None
    tok = await verifier.verify_token("s3cret")
    assert tok is not None and tok.scopes == [SCOPE_READ]
    assert settings.required_scopes == [SCOPE_READ]  # the floor lets it in; the guard keeps it to tier 1
