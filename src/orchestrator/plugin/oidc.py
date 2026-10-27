"""The company-login token check for the shared HTTP server: OIDC access tokens verified
against the provider's published keys (JWKS), the person read from configured claims, and
scopes either taken from the token or mapped from company groups/roles.

Why a third mode beside ``auth``'s static secret and RFC 7662 introspection: Entra and Google
publish no introspection endpoint, so the only standard check that works with every OIDC
provider is the signature against the keys at the discovery document's ``jwks_uri``. Nothing
here is provider-specific (D3) — Entra v1 and v2 are two accepted issuers and two accepted
audiences in one configuration, not code.

What a token must be, in the order it is checked (each failure refuses, logs the reason, and
never raises into the request):

1. a JWT whose header ``alg`` is one of the **pinned asymmetric** algorithms — ``none`` and
   ``HS*`` cannot even be configured, which closes the alg-confusion attack where the public
   key is used as an HMAC secret;
2. signed by the key its ``kid`` names in the provider's set (a header without ``kid`` only
   when the set holds one key; a key that names its own ``alg`` must agree with the header);
3. ``exp``, ``iss`` and ``aud`` present; ``exp``/``nbf`` within a 60 s leeway; ``aud`` one of
   the configured audiences; ``iss`` the configured issuer or an accepted extra one;
4. carrying the subject claim (D31: a token the audit cannot name a person for is refused).

**Scopes (D26).** With a role map, scopes come *only* from the roles/groups claim: a person
can often consent to a delegated scope for themselves, so honouring ``scp`` beside the map
would let them grant their own ``spine:plan``. Without a map, ``scp``/``scope`` is read and
anything that is not a Spine scope is dropped.

**Keys (D29).** The set is cached for an hour. An unknown ``kid`` triggers a refetch — that is
how a rotated key is picked up — but at most once per 60 s, so a stream of made-up ``kid``s
costs the provider one request a minute. A failed fetch keeps the keys already held.
Discovery is lazy: the server starts while the provider is unreachable, and the first token
is refused (and logged) until it answers.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken

logger = logging.getLogger("orchestrator.plugin.oidc")

#: The only algorithms a deployment may pin: public-key signatures. A symmetric algorithm
#: would need a secret Spine does not have, and ``none`` is no signature at all.
ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
#: The Spine scopes, in tier order — the order a token's scopes are reported in.
_SPINE_SCOPES = ("spine:read", "spine:plan", "spine:run")
#: Claims naming the calling app, by provider: Entra v2, Entra v1, RFC 9068, Okta.
_CLIENT_CLAIMS = ("azp", "appid", "client_id", "cid")

Fetch = Callable[[str], Awaitable[dict[str, Any]]]


def _split(value: str | None, seps: str = ", ") -> tuple[str, ...]:
    if not value:
        return ()
    for sep in seps[1:]:
        value = value.replace(sep, seps[0])
    return tuple(part for part in value.split(seps[0]) if part)


def parse_role_map(text: str) -> dict[str, frozenset[str]]:
    """``"spine-readers=spine:read; spine-planners=spine:read spine:plan"`` → a map from
    group/role to the Spine scopes it grants. Anything malformed is refused at start, naming
    the entry — a typo in a permission table must not quietly grant or withhold access."""
    mapping: dict[str, frozenset[str]] = {}
    for entry in (part.strip() for part in text.split(";")):
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(
                f"ORCHESTRATOR_MCP_ROLE_MAP entry {entry!r} needs the form group=scope [scope …]"
            )
        group, _, scopes_text = (piece.strip() for piece in entry.partition("="))
        if not group:
            raise ValueError(f"ORCHESTRATOR_MCP_ROLE_MAP entry {entry!r} names no group")
        scopes = scopes_text.replace(",", " ").split()
        if not scopes:
            raise ValueError(f"ORCHESTRATOR_MCP_ROLE_MAP entry {entry!r} grants no scope")
        unknown = [s for s in scopes if s not in _SPINE_SCOPES]
        if unknown:
            raise ValueError(
                f"ORCHESTRATOR_MCP_ROLE_MAP entry {entry!r}: unknown scope {', '.join(unknown)} "
                f"(known: {', '.join(_SPINE_SCOPES)})"
            )
        if group in mapping:
            raise ValueError(f"ORCHESTRATOR_MCP_ROLE_MAP names {group!r} twice")
        mapping[group] = frozenset(scopes)
    return mapping


def _require_https(name: str, url: str) -> None:
    """Keys and discovery must come over TLS — except from a provider on this machine, which
    is how a local test identity provider runs."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    host = parts.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme == "http" and loopback:
        return
    raise ValueError(f"{name} must be an https URL (got {url!r}); plain http is allowed only for localhost")


@dataclass(frozen=True)
class OidcConfig:
    """Everything the check needs, validated when built so a bad setting stops the server at
    start rather than refusing every token at run time."""

    issuer: str
    audiences: tuple[str, ...]
    accepted_issuers: tuple[str, ...] = ()
    jwks_url: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    subject_claim: str = "sub"
    email_claims: tuple[str, ...] = ("email", "preferred_username", "upn")
    roles_claim: str = "roles"
    role_map: Mapping[str, frozenset[str]] | None = None
    leeway: int = 60
    cache_ttl: float = 3600.0
    refetch_floor: float = 60.0

    def __post_init__(self) -> None:
        if not self.issuer:
            raise ValueError("the company-login check needs ORCHESTRATOR_MCP_ISSUER_URL")
        _require_https("ORCHESTRATOR_MCP_ISSUER_URL", self.issuer)
        if self.jwks_url:
            _require_https("ORCHESTRATOR_MCP_JWKS_URL", self.jwks_url)
        if not self.audiences:
            raise ValueError(
                "the company-login check needs at least one audience (ORCHESTRATOR_MCP_AUDIENCE)"
            )
        if not self.algorithms:
            raise ValueError("ORCHESTRATOR_MCP_JWT_ALGORITHMS names no algorithm")
        bad = [a for a in self.algorithms if a not in ASYMMETRIC_ALGORITHMS]
        if bad:
            raise ValueError(
                f"ORCHESTRATOR_MCP_JWT_ALGORITHMS: algorithm {', '.join(bad)} is not allowed; "
                f"pin public-key algorithms only ({', '.join(sorted(ASYMMETRIC_ALGORITHMS))})"
            )

    @property
    def issuers(self) -> tuple[str, ...]:
        return (self.issuer, *self.accepted_issuers)


async def _http_fetch(url: str) -> dict[str, Any]:
    """GET a JSON document from the identity provider. Module-level so tests replace it."""
    import httpx

    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return data


@dataclass
class _KeyCache:
    #: ``kid`` → (the key, the ``alg`` the provider published for it, if any)
    keys: dict[str, tuple[Any, str | None]] = field(default_factory=dict)
    fetched_at: float | None = None
    attempted_at: float | None = None


class _RefusedError(Exception):
    """A token failed a check; the message is the reason that is logged."""


class JwksTokenVerifier:
    """Verify OIDC access tokens against the provider's JWKS — see the module docstring."""

    def __init__(
        self,
        config: OidcConfig,
        *,
        fetch: Fetch | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._fetch = fetch
        self._clock = clock
        self._jwks_url: str | None = config.jwks_url
        self._cache = _KeyCache()

    async def _get(self, url: str) -> dict[str, Any]:
        fetch = self._fetch or _http_fetch
        return await fetch(url)

    async def _discover(self) -> str:
        if self._jwks_url:
            return self._jwks_url
        doc = await self._get(self.config.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if doc.get("issuer") != self.config.issuer:
            raise _RefusedError(f"discovery names issuer {doc.get('issuer')!r}, not the configured one")
        jwks_uri = doc.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise _RefusedError("discovery document has no jwks_uri")
        _require_https("jwks_uri", jwks_uri)
        self._jwks_url = jwks_uri
        return jwks_uri

    async def _refresh(self) -> None:
        """Fetch the key set, unless one was attempted inside the refetch floor."""
        from jwt import PyJWK

        now = self._clock()
        attempted = self._cache.attempted_at
        if attempted is not None and now - attempted < self.config.refetch_floor:
            return
        self._cache.attempted_at = now
        try:
            doc = await self._get(await self._discover())
        except _RefusedError:
            raise
        except Exception as exc:  # unreachable, refused, not JSON — keep the keys we hold
            logger.warning("plugin.oidc.jwks_fetch_failed", extra={"error": str(exc)[:200]})
            return
        keys: dict[str, tuple[Any, str | None]] = {}
        for raw in doc.get("keys") or []:
            if not isinstance(raw, dict) or raw.get("use", "sig") != "sig":
                continue
            try:
                keys[str(raw.get("kid", ""))] = (PyJWK(raw), raw.get("alg"))
            except Exception:  # a key type this build cannot use — skip it, keep the rest
                continue
        self._cache.keys = keys
        self._cache.fetched_at = now

    def _expired(self) -> bool:
        fetched = self._cache.fetched_at
        return fetched is None or self._clock() - fetched >= self.config.cache_ttl

    def _lookup(self, kid: str | None) -> tuple[Any, str | None] | None:
        keys = self._cache.keys
        if kid is None:
            return next(iter(keys.values())) if len(keys) == 1 else None
        return keys.get(kid)

    async def _key_for(self, kid: str | None) -> tuple[Any, str | None]:
        if self._expired():
            await self._refresh()
        key = self._lookup(kid)
        if key is None:  # rotated, or made up: one refetch, rate-limited
            await self._refresh()
            key = self._lookup(kid)
        if key is None:
            raise _RefusedError(f"no key {kid!r} in the provider's key set")
        return key

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await self._verify(token)
        except _RefusedError as exc:
            logger.warning("plugin.oidc.token_refused", extra={"reason": str(exc)[:200]})
        except Exception as exc:  # malformed, bad signature, failed claim — refused either way
            logger.warning(
                "plugin.oidc.token_refused", extra={"reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
            )
        return None

    async def _verify(self, token: str) -> AccessToken:
        import jwt
        from mcp.server.auth.provider import AccessToken

        config = self.config
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        if alg not in config.algorithms:
            raise _RefusedError(f"algorithm {alg!r} is not pinned")
        kid = header.get("kid")
        key, key_alg = await self._key_for(str(kid) if kid is not None else None)
        if key_alg is not None and key_alg != alg:
            raise _RefusedError(f"key {kid!r} is for {key_alg}, token says {alg}")
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key.key,
            algorithms=[alg],
            audience=list(config.audiences),
            leeway=config.leeway,
            options={"require": ["exp", "iss", "aud"], "verify_iss": False},
        )
        issuer = claims.get("iss")
        if issuer not in config.issuers:
            raise _RefusedError(f"issuer {issuer!r} is not accepted")
        subject = claims.get(config.subject_claim)
        if not isinstance(subject, str) or not subject:
            raise _RefusedError(f"no {config.subject_claim!r} claim to name the person by")
        email = next((v for c in config.email_claims if isinstance(v := claims.get(c), str) and v), None)
        client_id = next((v for c in _CLIENT_CLAIMS if isinstance(v := claims.get(c), str) and v), "oidc")
        aud = claims.get("aud")
        auds = [aud] if isinstance(aud, str) else list(aud or [])
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=self._scopes(claims),
            expires_at=int(claims["exp"]),
            resource=next((a for a in config.audiences if a in auds), None),
            subject=subject,
            claims={"iss": issuer, "email": email},
        )

    def _scopes(self, claims: dict[str, Any]) -> list[str]:
        role_map = self.config.role_map
        if role_map is not None:
            granted: set[str] = set()
            for role in _as_list(claims.get(self.config.roles_claim)):
                granted |= role_map.get(role, frozenset())
        else:
            raw = claims["scp"] if "scp" in claims else claims.get("scope")
            granted = set(_as_list(raw))
        return [s for s in _SPINE_SCOPES if s in granted]


def _as_list(value: Any) -> list[str]:
    """A claim that is a space-delimited string (``scp`` in Entra) or a list (Okta, ``roles``)."""
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def jwks_verifier_from_env() -> JwksTokenVerifier:
    """Build the verifier from ``ORCHESTRATOR_MCP_*`` env; a bad setting raises here, at start."""
    audiences = _split(os.getenv("ORCHESTRATOR_MCP_AUDIENCE")) or _split(
        os.getenv("ORCHESTRATOR_MCP_RESOURCE_URL")
    )
    if not audiences:
        raise ValueError(
            "The company-login check needs the API it protects: set ORCHESTRATOR_MCP_AUDIENCE "
            "(e.g. the App ID URI and client id) or ORCHESTRATOR_MCP_RESOURCE_URL."
        )
    role_map_text = os.getenv("ORCHESTRATOR_MCP_ROLE_MAP", "").strip()
    kwargs: dict[str, Any] = {}
    if algorithms := _split(os.getenv("ORCHESTRATOR_MCP_JWT_ALGORITHMS")):
        kwargs["algorithms"] = algorithms
    if email_claims := _split(os.getenv("ORCHESTRATOR_MCP_EMAIL_CLAIMS")):
        kwargs["email_claims"] = email_claims
    config = OidcConfig(
        issuer=os.getenv("ORCHESTRATOR_MCP_ISSUER_URL", ""),
        audiences=audiences,
        accepted_issuers=_split(os.getenv("ORCHESTRATOR_MCP_ACCEPTED_ISSUERS")),
        jwks_url=os.getenv("ORCHESTRATOR_MCP_JWKS_URL") or None,
        subject_claim=os.getenv("ORCHESTRATOR_MCP_SUBJECT_CLAIM") or "sub",
        roles_claim=os.getenv("ORCHESTRATOR_MCP_ROLES_CLAIM") or "roles",
        role_map=parse_role_map(role_map_text) if role_map_text else None,
        **kwargs,
    )
    return JwksTokenVerifier(config)


__all__ = [
    "ASYMMETRIC_ALGORITHMS",
    "JwksTokenVerifier",
    "OidcConfig",
    "jwks_verifier_from_env",
    "parse_role_map",
]
