"""Block A.2 unit tests: GitHub App JWT signing + installation-token cache.

An ephemeral RSA keypair is generated per-test (no committed key material).
JWTs are verified by decoding back with the public key. The installation-
token exchange is driven against an httpx MockTransport so no network /
github.com is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from orchestrator.codereview.auth import GitHubAppAuth, GitHubAuthError, build_app_jwt
from orchestrator.codereview.config import GitHubAppConfig


def _rsa_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) as PEM strings."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


# ---- JWT signing ----------------------------------------------------------


def test_build_app_jwt_round_trips() -> None:
    private_pem, public_pem = _rsa_keypair()
    token = build_app_jwt("12345", private_pem)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "12345"
    assert decoded["exp"] > decoded["iat"]
    # exp must be within GitHub's 10-minute ceiling of iat.
    assert decoded["exp"] - decoded["iat"] <= 600


def test_build_app_jwt_backdates_iat_for_clock_skew() -> None:
    private_pem, public_pem = _rsa_keypair()
    now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)
    token = build_app_jwt("12345", private_pem, now=now)
    # The fixed `now` makes exp deterministic but possibly in the past relative
    # to wall-clock; we only assert the iat claim, so skip exp validation.
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    # iat is backdated 60s relative to `now`.
    assert decoded["iat"] == int((now - timedelta(seconds=60)).timestamp())


def test_build_app_jwt_requires_id_and_key() -> None:
    with pytest.raises(GitHubAuthError):
        build_app_jwt("", "key")
    with pytest.raises(GitHubAuthError):
        build_app_jwt("123", "")


# ---- installation-token exchange + cache ----------------------------------


def _config_with_key() -> GitHubAppConfig:
    private_pem, _ = _rsa_keypair()
    return GitHubAppConfig(app_id="42", private_key=private_pem)


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="https://api.github.com")


async def test_installation_token_mints_and_caches() -> None:
    calls = {"n": 0}
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["Authorization"].startswith("Bearer ")
        assert "/app/installations/99/access_tokens" in str(request.url)
        return httpx.Response(201, json={"token": "ghs_minted_token", "expires_at": future})

    auth = GitHubAppAuth(_config_with_key(), http_client=_mock_client(httpx.MockTransport(handler)))
    try:
        t1 = await auth.installation_token(99)
        t2 = await auth.installation_token(99)  # second call should hit the cache
    finally:
        await auth.aclose()

    assert t1 == "ghs_minted_token"
    assert t2 == "ghs_minted_token"
    assert calls["n"] == 1  # only minted once


async def test_installation_token_refreshes_when_expiring() -> None:
    calls = {"n": 0}
    # First response expires in 1 minute → inside the 5-minute refresh skew,
    # so the next call must re-mint.
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        expiry = soon if calls["n"] == 1 else later
        return httpx.Response(201, json={"token": f"ghs_{calls['n']}", "expires_at": expiry})

    auth = GitHubAppAuth(_config_with_key(), http_client=_mock_client(httpx.MockTransport(handler)))
    try:
        t1 = await auth.installation_token(7)
        t2 = await auth.installation_token(7)
    finally:
        await auth.aclose()

    assert t1 == "ghs_1"
    assert t2 == "ghs_2"  # re-minted because the first was expiring
    assert calls["n"] == 2


async def test_installation_token_raises_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    auth = GitHubAppAuth(_config_with_key(), http_client=_mock_client(httpx.MockTransport(handler)))
    try:
        with pytest.raises(GitHubAuthError, match="mint failed"):
            await auth.installation_token(123)
    finally:
        await auth.aclose()


async def test_installation_token_requires_api_config() -> None:
    # webhook-only config (no app_id / private_key) can't mint tokens.
    # _env_file=None so a developer's local .env (live-test creds) can't make
    # this "configured" and turn the assertion into a real GitHub call.
    auth = GitHubAppAuth(GitHubAppConfig(webhook_secret="s", _env_file=None))  # type: ignore[call-arg]
    with pytest.raises(GitHubAuthError, match="not configured for API calls"):
        await auth.installation_token(1)
