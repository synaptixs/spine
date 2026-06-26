"""Block A.2: GitHub App authentication.

A GitHub App authenticates in two hops:

  1. **App JWT** — a short-lived (<=10 min) RS256 JWT signed with the App's
     private key. Proves "I am this App." Claims: ``iat`` (backdated 60s for
     clock skew), ``exp``, ``iss`` (the App ID).
  2. **Installation token** — POST the App JWT to
     ``/app/installations/{id}/access_tokens`` to mint a ~1-hour token scoped
     to one org/repo installation. This token is what the github tools
     (Block A.3) send as ``Authorization: Bearer <token>``.

``GitHubAppAuth`` owns the second hop plus a per-installation cache: tokens
are reused until they're within a refresh skew of expiry, then re-minted.
A single lock serialises refreshes so a burst of webhook deliveries for the
same installation doesn't stampede the token endpoint.

The HTTP client is injectable so tests can mint tokens against a mock
transport without hitting github.com.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from orchestrator.codereview.config import GitHubAppConfig

# App JWTs may live at most 10 minutes; we use a conservative window.
_JWT_TTL = timedelta(minutes=9)
_JWT_BACKDATE = timedelta(seconds=60)  # tolerate clock skew between us and GitHub
# Re-mint an installation token once it's within this window of expiry.
_TOKEN_REFRESH_SKEW = timedelta(minutes=5)


class GitHubAuthError(RuntimeError):
    """Raised when the App can't authenticate or mint an installation token."""


def build_app_jwt(app_id: str, private_key: str, *, now: datetime | None = None) -> str:
    """Sign an app-level RS256 JWT. Pure function — testable by decoding back.

    ``now`` is injectable for deterministic tests; defaults to UTC now.
    """
    if not app_id or not private_key:
        raise GitHubAuthError("GitHub App id and private key are both required to sign a JWT.")
    issued = (now or datetime.now(UTC)) - _JWT_BACKDATE
    payload = {
        "iat": int(issued.timestamp()),
        "exp": int((issued + _JWT_TTL).timestamp()),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


@dataclass
class _CachedToken:
    token: str
    expires_at: datetime

    def is_fresh(self, *, now: datetime) -> bool:
        return now < self.expires_at - _TOKEN_REFRESH_SKEW


class GitHubAppAuth:
    """Mints + caches installation access tokens for a configured App."""

    def __init__(self, config: GitHubAppConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None
        self._cache: dict[int, _CachedToken] = {}
        self._lock = asyncio.Lock()

    def app_jwt(self, *, now: datetime | None = None) -> str:
        return build_app_jwt(self._config.app_id, self._config.private_key, now=now)

    async def installation_token(self, installation_id: int, *, now: datetime | None = None) -> str:
        """Return a valid installation token, minting + caching as needed."""
        if not self._config.api_configured:
            raise GitHubAuthError("GitHub App not configured for API calls (need app_id + private_key).")
        moment = now or datetime.now(UTC)
        async with self._lock:
            cached = self._cache.get(installation_id)
            if cached is not None and cached.is_fresh(now=moment):
                return cached.token
            minted = await self._mint_installation_token(installation_id)
            self._cache[installation_id] = minted
            return minted.token

    async def _mint_installation_token(self, installation_id: int) -> _CachedToken:
        url = f"{self._config.api_base_url}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {self.app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.post(url, headers=headers)
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()

        if resp.status_code != httpx.codes.CREATED:
            raise GitHubAuthError(
                f"installation-token mint failed for installation {installation_id}: "
                f"HTTP {resp.status_code} {resp.text[:256]}"
            )
        data = resp.json()
        token = data.get("token")
        if not token:
            raise GitHubAuthError(f"installation-token response had no token: {data!r}")
        expires_at = _parse_expiry(data.get("expires_at"))
        return _CachedToken(token=token, expires_at=expires_at)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()


def _parse_expiry(raw: str | None) -> datetime:
    """Parse GitHub's ISO-8601 ``expires_at`` (e.g. '2026-05-29T12:00:00Z').

    Falls back to "1 hour from now" when absent/unparseable — GitHub
    installation tokens are 1-hour-lived, so this is a safe default that
    keeps the cache honest even if the field shape changes.
    """
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC) + timedelta(hours=1)
