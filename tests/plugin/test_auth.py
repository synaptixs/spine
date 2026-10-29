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
)

_MCP_ENV = (
    "ORCHESTRATOR_MCP_TOKEN",
    "ORCHESTRATOR_MCP_INTROSPECTION_URL",
    "ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_ID",
    "ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_SECRET",
    "ORCHESTRATOR_MCP_ISSUER_URL",
    "ORCHESTRATOR_MCP_RESOURCE_URL",
    "ORCHESTRATOR_MCP_REQUIRED_SCOPES",
    "ORCHESTRATOR_MCP_AUDIENCE",
    "ORCHESTRATOR_MCP_JWKS_URL",
    "ORCHESTRATOR_MCP_JWT_ALGORITHMS",
    "ORCHESTRATOR_MCP_ROLE_MAP",
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
    # The IdP's scopes exactly as issued. `sdlc` is no longer an alias for the tiers: a token
    # that carries only it has no tier scope, and the floor refuses it.
    assert set(tok.scopes) == {"admin", "sdlc"}


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


async def test_introspection_names_the_person_the_idp_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """A company login reached through introspection names a person too, so an approval is
    recorded under them, not under a name the caller types (B114)."""
    import httpx

    from orchestrator.plugin.server import _signed_in_person

    answer = {
        "active": True,
        "scope": "spine:read spine:plan",
        "client_id": "app-1",
        "sub": "sub-ann",
        "email": "ann@example.test",
        "iss": "https://idp.example",
    }
    _patch_introspection(monkeypatch, lambda r: httpx.Response(200, json=answer))
    tok = await IntrospectionTokenVerifier("https://idp.example/introspect").verify_token("opaque")

    assert tok is not None and tok.subject == "sub-ann"
    assert tok.claims == {"iss": "https://idp.example", "email": "ann@example.test"}
    assert _signed_in_person(tok) == ("ann@example.test", frozenset({"ann@example.test", "sub-ann"}))


@pytest.mark.parametrize("extra", [{}, {"sub": 42, "email": ["ann@example.test"]}, {"sub": "", "email": ""}])
async def test_an_introspection_answer_naming_no_person_names_nobody(
    monkeypatch: pytest.MonkeyPatch, extra: dict[str, object]
) -> None:
    import httpx

    from orchestrator.plugin.server import _signed_in_person

    _patch_introspection(
        monkeypatch, lambda r: httpx.Response(200, json={"active": True, "scope": "spine:read", **extra})
    )
    tok = await IntrospectionTokenVerifier("https://idp.example/introspect").verify_token("opaque")

    assert tok is not None and tok.subject is None
    assert _signed_in_person(tok) is None


# ---- build_auth_from_env ----------------------------------------------------


def test_env_no_verifier_returns_none() -> None:
    assert build_auth_from_env() == (None, None)


async def test_env_static_token_builds_resource_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.setenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES", "sdlc, admin")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, StaticTokenVerifier)
    # The env var is what the static token *carries*, verbatim; what the SDK requires of every
    # token is the floor, `spine:read` — the tiers above are per tool. A static token set to
    # the retired `sdlc` scope therefore fails the floor: the deprecation #322 announced.
    assert settings is not None and settings.required_scopes == [SCOPE_READ]
    tok = await verifier.verify_token("s3cret")
    assert tok is not None and set(tok.scopes) == {"admin", "sdlc"}
    assert SCOPE_READ not in tok.scopes
    # issuer defaults to the resource URL for the static (no real AS) path.
    assert str(settings.issuer_url).startswith("https://mcp.example.com")


def test_env_introspection_wins_over_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", "https://idp.example/introspect")
    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, IntrospectionTokenVerifier)
    assert settings is not None


def test_env_issuer_alone_selects_the_company_login_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """D25: an issuer with no introspection URL and no static token means the company's OIDC
    login — tokens checked against its published keys."""
    from orchestrator.plugin.oidc import JwksTokenVerifier

    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example/oauth2/default")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com/mcp")
    settings, verifier = build_auth_from_env()
    assert isinstance(verifier, JwksTokenVerifier)
    assert settings is not None and settings.required_scopes == [SCOPE_READ]
    assert str(settings.issuer_url) == "https://idp.example/oauth2/default"
    assert str(settings.resource_server_url) == "https://mcp.example.com/mcp"
    # The verifier checks `aud` against the configured audiences (an Entra App ID URI is not
    # the resource URL), so the SDK's own resource check is off — on purpose, not by default.
    assert settings.validate_token_resource is False


def test_env_a_static_token_still_wins_over_an_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    """D25: a self-host that set an issuer beside its static token keeps the static token."""
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example")
    _, verifier = build_auth_from_env()
    assert isinstance(verifier, StaticTokenVerifier)


def test_env_introspection_still_wins_over_an_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", "https://idp.example/introspect")
    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example")
    monkeypatch.setenv("ORCHESTRATOR_MCP_AUDIENCE", "api://spine")
    _, verifier = build_auth_from_env()
    assert isinstance(verifier, IntrospectionTokenVerifier)


@pytest.mark.parametrize("algs", ["HS256", "none", "RS256 HS256"])
def test_env_a_symmetric_or_none_algorithm_stops_the_server_at_start(
    monkeypatch: pytest.MonkeyPatch, algs: str
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_ISSUER_URL", "https://idp.example")
    monkeypatch.setenv("ORCHESTRATOR_MCP_AUDIENCE", "api://spine")
    monkeypatch.setenv("ORCHESTRATOR_MCP_JWT_ALGORITHMS", algs)
    with pytest.raises(ValueError, match="algorithm"):
        build_auth_from_env()


def test_env_verifier_without_any_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")  # no issuer/resource URL
    with pytest.raises(ValueError, match="ISSUER_URL"):
        build_auth_from_env()


# ---- scopes follow the tiers -------------------------------------------------------


def test_the_retired_sdlc_scope_is_not_an_alias_any_more() -> None:
    """3.31.0 accepted `sdlc` as all three tiers for one release, with a warning. That release
    has shipped; a token carrying only `sdlc` now has no tier scope at all."""
    import orchestrator.plugin.auth as auth

    assert not hasattr(auth, "expand_scopes") and not hasattr(auth, "LEGACY_SCOPE")
    assert "sdlc" not in {SCOPE_READ, SCOPE_PLAN, SCOPE_RUN}


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
