"""The company-login token check: JWKS signature, claims, identity, and the role → scope map.

Every key here is generated when the test runs and every "identity provider" is a dict
behind a fake fetch — no network, nothing committed. Each refusal test changes exactly one
thing about an otherwise-good token, so a pass means *that* check refused it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra")

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from orchestrator.plugin.auth import SCOPE_PLAN, SCOPE_READ, SCOPE_RUN
from orchestrator.plugin.oidc import (
    JwksTokenVerifier,
    OidcConfig,
    jwks_verifier_from_env,
    parse_role_map,
)

ISSUER = "https://idp.example.test/oauth2/default"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
JWKS_URL = ISSUER + "/v1/keys"
AUDIENCE = "api://spine"

TENANT = "00000000-0000-0000-0000-0000000000aa"
ENTRA_V2 = f"https://login.microsoftonline.com/{TENANT}/v2.0"
ENTRA_V1 = f"https://sts.windows.net/{TENANT}/"
ENTRA_JWKS = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
APP_ID_URI = "api://spine-mcp"
API_CLIENT_ID = "11111111-2222-3333-4444-555555555555"


# ---- keys and tokens, minted here ---------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: Any, kid: str, alg: str | None = "RS256") -> dict[str, Any]:
    public = key.public_key()
    algo = ECAlgorithm if isinstance(public, ec.EllipticCurvePublicKey) else RSAAlgorithm
    jwk: dict[str, Any] = algo.to_jwk(public, as_dict=True)
    jwk.update({"kid": kid, "use": "sig"})
    if alg:
        jwk["alg"] = alg
    return jwk


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "person-1",
        "email": "ada@example.test",
        "exp": now + 600,
        "nbf": now - 10,
        "iat": now - 10,
        "scp": "spine:read spine:plan",
        "cid": "client-app",
    }
    for name, value in overrides.items():
        if value is _DROP:
            claims.pop(name, None)
        else:
            claims[name] = value
    return claims


_DROP = object()


def _mint(key: Any, claims: dict[str, Any], *, kid: str | None = "k1", alg: str = "RS256") -> str:
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, key, algorithm=alg, headers=headers)


def _unsigned(claims: dict[str, Any], *, alg: str = "none", kid: str = "k1") -> str:
    head = _b64(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}).encode())
    body = _b64(json.dumps(claims).encode())
    return f"{head}.{body}."


def _hs256_with(secret: bytes, claims: dict[str, Any], *, kid: str = "k1") -> str:
    """The key-confusion attack: an HMAC token whose secret is the server's *public* key."""
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


class FakeIdp:
    """The provider's two published documents, served from memory; records every fetch."""

    def __init__(self, keys: list[dict[str, Any]], *, issuer: str = ISSUER, jwks_url: str = JWKS_URL) -> None:
        self.docs: dict[str, Any] = {
            issuer.rstrip("/") + "/.well-known/openid-configuration": {
                "issuer": issuer,
                "jwks_uri": jwks_url,
            },
            jwks_url: {"keys": keys},
        }
        self.jwks_url = jwks_url
        self.calls: list[str] = []
        self.fail = False

    def rotate(self, keys: list[dict[str, Any]]) -> None:
        self.docs[self.jwks_url] = {"keys": keys}

    async def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if self.fail:
            raise OSError("idp unreachable")
        copy: dict[str, Any] = json.loads(json.dumps(self.docs[url]))  # a fresh copy per fetch
        return copy


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _config(**overrides: Any) -> OidcConfig:
    kwargs: dict[str, Any] = {"issuer": ISSUER, "audiences": (AUDIENCE,)}
    kwargs.update(overrides)
    return OidcConfig(**kwargs)


@pytest.fixture
def idp(rsa_key: rsa.RSAPrivateKey) -> FakeIdp:
    return FakeIdp([_jwk(rsa_key, "k1")])


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _verifier(idp: FakeIdp, clock: Clock | None = None, **overrides: Any) -> JwksTokenVerifier:
    return JwksTokenVerifier(_config(**overrides), fetch=idp, clock=clock or Clock())


# ---- the good path --------------------------------------------------------------------


async def test_a_good_token_carries_the_person_their_scopes_and_the_app(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey
) -> None:
    claims = _claims()
    tok = await _verifier(idp).verify_token(_mint(rsa_key, claims))
    assert tok is not None
    assert tok.subject == "person-1"
    assert tok.claims is not None and tok.claims["email"] == "ada@example.test"
    assert tok.claims["iss"] == ISSUER  # the SDK keys a principal on (client, issuer, subject)
    assert tok.scopes == [SCOPE_READ, SCOPE_PLAN]
    assert tok.client_id == "client-app"
    assert tok.expires_at == claims["exp"]
    assert tok.resource == AUDIENCE


async def test_discovery_finds_the_key_set_and_both_are_fetched_once(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey
) -> None:
    verifier = _verifier(idp)
    for _ in range(3):
        assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    assert idp.calls == [DISCOVERY, JWKS_URL]


async def test_a_configured_key_set_url_skips_discovery(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    verifier = _verifier(idp, jwks_url=JWKS_URL)
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    assert idp.calls == [JWKS_URL]


# ---- one check each -------------------------------------------------------------------


async def test_a_token_from_another_issuer_is_refused(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    token = _mint(rsa_key, _claims(iss="https://evil.example.test"))
    assert await _verifier(idp).verify_token(token) is None


async def test_a_token_for_another_audience_is_refused(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    token = _mint(rsa_key, _claims(aud="api://some-other-api"))
    assert await _verifier(idp).verify_token(token) is None


async def test_an_expired_token_is_refused(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    token = _mint(rsa_key, _claims(exp=int(time.time()) - 120))
    assert await _verifier(idp).verify_token(token) is None


async def test_a_token_not_valid_yet_is_refused(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    token = _mint(rsa_key, _claims(nbf=int(time.time()) + 120))
    assert await _verifier(idp).verify_token(token) is None


async def test_clock_skew_inside_the_leeway_is_forgiven(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    now = int(time.time())
    token = _mint(rsa_key, _claims(exp=now - 30, nbf=now + 30))
    assert await _verifier(idp).verify_token(token) is not None


@pytest.mark.parametrize("claim", ["exp", "iss", "aud"])
async def test_a_token_missing_a_required_claim_is_refused(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, claim: str
) -> None:
    token = _mint(rsa_key, _claims(**{claim: _DROP}))
    assert await _verifier(idp).verify_token(token) is None


async def test_alg_none_is_refused(idp: FakeIdp) -> None:
    assert await _verifier(idp).verify_token(_unsigned(_claims())) is None


async def test_an_hmac_token_keyed_with_the_public_key_is_refused(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey
) -> None:
    pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert await _verifier(idp).verify_token(_hs256_with(pem, _claims())) is None
    n = idp.docs[JWKS_URL]["keys"][0]["n"].encode()
    assert await _verifier(idp).verify_token(_hs256_with(n, _claims())) is None


async def test_an_algorithm_outside_the_pinned_list_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    idp = FakeIdp([_jwk(rsa_key, "k1", alg=None)])  # the key itself names no alg
    token = _mint(rsa_key, _claims(), alg="RS512")
    assert await _verifier(idp).verify_token(token) is None
    assert await _verifier(idp, algorithms=("RS256", "RS512")).verify_token(token) is not None


async def test_a_token_whose_alg_differs_from_its_key_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    idp = FakeIdp([_jwk(rsa_key, "k1", alg="RS384")])
    token = _mint(rsa_key, _claims(), alg="RS256")
    assert await _verifier(idp, algorithms=("RS256", "RS384")).verify_token(token) is None


async def test_a_token_signed_by_another_key_is_refused(
    idp: FakeIdp, other_rsa_key: rsa.RSAPrivateKey
) -> None:
    assert await _verifier(idp).verify_token(_mint(other_rsa_key, _claims())) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "....."])
async def test_garbage_is_refused_without_raising(idp: FakeIdp, token: str) -> None:
    assert await _verifier(idp).verify_token(token) is None


# ---- keys: rotation, unknown kid, outages ---------------------------------------------


async def test_a_rotated_key_is_picked_up_with_one_refetch(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, other_rsa_key: rsa.RSAPrivateKey, clock: Clock
) -> None:
    verifier = _verifier(idp, clock)
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    idp.rotate([_jwk(rsa_key, "k1"), _jwk(other_rsa_key, "k2")])
    clock.now += 120
    assert await verifier.verify_token(_mint(other_rsa_key, _claims(), kid="k2")) is not None
    assert idp.calls == [DISCOVERY, JWKS_URL, JWKS_URL]


async def test_an_unknown_kid_is_refused_and_cannot_force_a_fetch_storm(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, clock: Clock
) -> None:
    verifier = _verifier(idp, clock)
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    clock.now += 120
    assert await verifier.verify_token(_mint(rsa_key, _claims(), kid="made-up-1")) is None
    assert idp.calls.count(JWKS_URL) == 2  # one refetch for the first unknown kid
    for n in range(5):
        clock.now += 5
        assert await verifier.verify_token(_mint(rsa_key, _claims(), kid=f"made-up-{n + 2}")) is None
    assert idp.calls.count(JWKS_URL) == 2  # inside the 60 s floor: no more fetches
    clock.now += 60
    assert await verifier.verify_token(_mint(rsa_key, _claims(), kid="made-up-9")) is None
    assert idp.calls.count(JWKS_URL) == 3  # the floor has passed: one more


async def test_the_key_set_is_refreshed_when_its_cache_expires(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, clock: Clock
) -> None:
    verifier = _verifier(idp, clock)
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    clock.now += 3599
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    assert idp.calls.count(JWKS_URL) == 1
    clock.now += 2
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    assert idp.calls.count(JWKS_URL) == 2


async def test_an_unreachable_provider_refuses_the_token_without_raising(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey
) -> None:
    idp.fail = True
    assert await _verifier(idp).verify_token(_mint(rsa_key, _claims())) is None


async def test_a_failed_refresh_keeps_the_keys_already_held(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, clock: Clock
) -> None:
    verifier = _verifier(idp, clock)
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None
    idp.fail = True
    clock.now += 4000  # cache expired, refresh fails
    assert await verifier.verify_token(_mint(rsa_key, _claims())) is not None


async def test_discovery_naming_another_issuer_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    idp = FakeIdp([_jwk(rsa_key, "k1")])
    idp.docs[DISCOVERY]["issuer"] = "https://evil.example.test"
    assert await _verifier(idp).verify_token(_mint(rsa_key, _claims())) is None
    assert JWKS_URL not in idp.calls


async def test_a_discovered_key_set_must_be_served_over_https(rsa_key: rsa.RSAPrivateKey) -> None:
    plain = "http://idp.example.test/keys"
    idp = FakeIdp([_jwk(rsa_key, "k1")], jwks_url=plain)
    assert await _verifier(idp).verify_token(_mint(rsa_key, _claims())) is None
    assert plain not in idp.calls


async def test_discovery_without_a_key_set_is_refused_and_says_so(
    rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    idp = FakeIdp([_jwk(rsa_key, "k1")])
    del idp.docs[DISCOVERY]["jwks_uri"]
    with caplog.at_level("WARNING", logger="orchestrator.plugin.oidc"):
        assert await _verifier(idp).verify_token(_mint(rsa_key, _claims())) is None
    assert any("no jwks_uri" in getattr(r, "reason", "") for r in caplog.records)


async def test_an_encryption_key_is_never_used_to_check_a_signature(rsa_key: rsa.RSAPrivateKey) -> None:
    idp = FakeIdp([_jwk(rsa_key, "k1") | {"use": "enc"}])
    assert await _verifier(idp).verify_token(_mint(rsa_key, _claims())) is None


async def test_a_token_without_kid_is_accepted_only_when_the_set_has_one_key(
    rsa_key: rsa.RSAPrivateKey, other_rsa_key: rsa.RSAPrivateKey
) -> None:
    token = _mint(rsa_key, _claims(), kid=None)
    assert await _verifier(FakeIdp([_jwk(rsa_key, "k1")])).verify_token(token) is not None
    # The signing key listed first: a fallback to "any key" would verify, so this bites.
    two = FakeIdp([_jwk(rsa_key, "k1"), _jwk(other_rsa_key, "k0")])
    assert await _verifier(two).verify_token(token) is None


async def test_an_ec_key_works_when_its_algorithm_is_pinned() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    idp = FakeIdp([_jwk(key, "e1", alg="ES256")])
    token = _mint(key, _claims(), kid="e1", alg="ES256")
    assert await _verifier(idp).verify_token(token) is None  # default pin is RS256 only
    assert await _verifier(idp, algorithms=("ES256",)).verify_token(token) is not None


# ---- identity -------------------------------------------------------------------------


async def test_a_token_without_the_subject_claim_is_refused(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    assert await _verifier(idp).verify_token(_mint(rsa_key, _claims(sub=_DROP))) is None
    token = _mint(rsa_key, _claims(oid="oid-7"))
    tok = await _verifier(idp, subject_claim="oid").verify_token(token)
    assert tok is not None and tok.subject == "oid-7"
    assert await _verifier(idp, subject_claim="oid").verify_token(_mint(rsa_key, _claims())) is None


async def test_email_comes_from_the_first_claim_present(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    token = _mint(rsa_key, _claims(email=_DROP, preferred_username="ada@corp.test", upn="other@corp.test"))
    tok = await _verifier(idp).verify_token(token)
    assert tok is not None and tok.claims is not None and tok.claims["email"] == "ada@corp.test"
    bare = await _verifier(idp).verify_token(_mint(rsa_key, _claims(email=_DROP)))
    assert bare is not None and bare.claims is not None and bare.claims["email"] is None


@pytest.mark.parametrize(
    ("claim", "value"),
    [("azp", "app-v2"), ("appid", "app-v1"), ("client_id", "app-gen"), ("cid", "app-okta")],
)
async def test_the_calling_app_is_read_from_the_claim_the_provider_uses(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, claim: str, value: str
) -> None:
    claims = _claims(cid=_DROP)
    claims[claim] = value
    tok = await _verifier(idp).verify_token(_mint(rsa_key, claims))
    assert tok is not None and tok.client_id == value


# ---- scopes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"scp": "spine:read"}, [SCOPE_READ]),
        ({"scp": ["spine:read", "spine:plan"]}, [SCOPE_READ, SCOPE_PLAN]),  # Okta: a list
        ({"scp": _DROP, "scope": "spine:plan spine:read"}, [SCOPE_READ, SCOPE_PLAN]),
        ({"scp": "openid profile spine:read admin"}, [SCOPE_READ]),  # unknown names dropped
        ({"scp": _DROP}, []),
    ],
)
async def test_without_a_role_map_scopes_come_from_the_token(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, claims: dict[str, Any], expected: list[str]
) -> None:
    tok = await _verifier(idp).verify_token(_mint(rsa_key, _claims(**claims)))
    assert tok is not None and tok.scopes == expected


ROLE_MAP = {"spine-readers": frozenset({SCOPE_READ}), "spine-planners": frozenset({SCOPE_READ, SCOPE_PLAN})}


async def test_with_a_role_map_scopes_come_only_from_the_groups(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey
) -> None:
    """D26: a scope the person consented to for themselves does not count beside the map."""
    verifier = _verifier(idp, role_map=ROLE_MAP)
    reader = await verifier.verify_token(_mint(rsa_key, _claims(roles=["spine-readers"], scp="spine:plan")))
    assert reader is not None and reader.scopes == [SCOPE_READ]
    planner = await verifier.verify_token(_mint(rsa_key, _claims(roles=["spine-planners", "x"])))
    assert planner is not None and planner.scopes == [SCOPE_READ, SCOPE_PLAN]
    nobody = await verifier.verify_token(_mint(rsa_key, _claims(roles=["finance"])))
    assert nobody is not None and nobody.scopes == []
    no_claim = await verifier.verify_token(_mint(rsa_key, _claims()))
    assert no_claim is not None and no_claim.scopes == []


async def test_a_genuine_token_that_grants_nothing_says_what_it_carried(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    """The SDK turns a token with no Spine scope into a bare 403; the admin needs the why."""
    verifier = _verifier(idp, role_map=ROLE_MAP)
    with caplog.at_level("WARNING", logger="orchestrator.plugin.oidc"):
        await verifier.verify_token(_mint(rsa_key, _claims(roles=["finance", "Spine.Reader"])))
        await verifier.verify_token(_mint(rsa_key, _claims()))
        await verifier.verify_token(_mint(rsa_key, _claims(roles=["spine-readers"])))
    rows = [r for r in caplog.records if r.getMessage() == "plugin.oidc.no_spine_scope"]
    assert [(getattr(r, "claim", None), getattr(r, "found", None)) for r in rows] == [
        ("roles", ["finance", "Spine.Reader"]),
        ("roles", []),
    ]  # the reader's token is not reported
    assert all(getattr(r, "subject", None) == "person-1" for r in rows)


async def test_the_reason_keeps_at_most_twenty_values(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    """A person in hundreds of groups must not turn one refusal into a log line of hundreds."""
    groups = [f"team-{i:02d}" for i in range(30)]
    with caplog.at_level("WARNING", logger="orchestrator.plugin.oidc"):
        await _verifier(idp, role_map=ROLE_MAP).verify_token(_mint(rsa_key, _claims(roles=groups)))
    (row,) = [r for r in caplog.records if r.getMessage() == "plugin.oidc.no_spine_scope"]
    assert getattr(row, "found", None) == groups[:20]


async def test_a_token_whose_roles_miss_the_read_floor_says_what_it_carried(
    idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    """The SDK requires read of every token, so a map granting plan alone ends in the same bare
    403 as a map granting nothing — the misconfiguration the reason exists to explain."""
    verifier = _verifier(idp, role_map={"spine-planners": frozenset({SCOPE_PLAN})})
    with caplog.at_level("WARNING", logger="orchestrator.plugin.oidc"):
        token = await verifier.verify_token(_mint(rsa_key, _claims(roles=["spine-planners"])))
    assert token is not None and token.scopes == [SCOPE_PLAN]
    rows = [r for r in caplog.records if r.getMessage() == "plugin.oidc.no_spine_scope"]
    assert [(getattr(r, "claim", None), getattr(r, "found", None)) for r in rows] == [
        ("roles", ["spine-planners"])
    ]


@pytest.mark.parametrize("claim", ["scp", "scope"])
async def test_without_a_role_map_the_reason_names_the_scope_claim_the_token_used(
    claim: str, idp: FakeIdp, rsa_key: rsa.RSAPrivateKey, caplog: pytest.LogCaptureFixture
) -> None:
    """Okta writes ``scp`` as a list, others ``scope`` as a string: the reason reads whichever is there."""
    claims = _claims(scp=_DROP)
    claims[claim] = "openid email"
    with caplog.at_level("WARNING", logger="orchestrator.plugin.oidc"):
        await _verifier(idp).verify_token(_mint(rsa_key, claims))
    (row,) = [r for r in caplog.records if r.getMessage() == "plugin.oidc.no_spine_scope"]
    assert (getattr(row, "claim", None), getattr(row, "found", None)) == (claim, ["openid", "email"])


async def test_the_roles_claim_is_configurable(idp: FakeIdp, rsa_key: rsa.RSAPrivateKey) -> None:
    verifier = _verifier(idp, role_map=ROLE_MAP, roles_claim="groups")
    tok = await verifier.verify_token(
        _mint(rsa_key, _claims(groups=["spine-planners"], roles=["spine-readers"]))
    )
    assert tok is not None and tok.scopes == [SCOPE_READ, SCOPE_PLAN]


# ---- Entra v1 and v2, one configuration -----------------------------------------------


async def test_entra_v1_and_v2_tokens_pass_the_same_configuration(rsa_key: rsa.RSAPrivateKey) -> None:
    idp = FakeIdp([_jwk(rsa_key, "entra-1")], issuer=ENTRA_V2, jwks_url=ENTRA_JWKS)
    verifier = JwksTokenVerifier(
        OidcConfig(
            issuer=ENTRA_V2,
            accepted_issuers=(ENTRA_V1,),
            audiences=(APP_ID_URI, API_CLIENT_ID),
            subject_claim="oid",
            role_map={
                "Spine.Read": frozenset({SCOPE_READ}),
                "Spine.Plan": frozenset({SCOPE_READ, SCOPE_PLAN}),
            },
        ),
        fetch=idp,
        clock=Clock(),
    )
    now = int(time.time())
    common = {"exp": now + 600, "nbf": now - 10, "iat": now - 10, "tid": TENANT}
    v1 = {
        **common,
        "iss": ENTRA_V1,
        "aud": APP_ID_URI,
        "ver": "1.0",
        "appid": "client-v1",
        "oid": "oid-v1",
        "sub": "pairwise-v1",
        "upn": "ada@corp.test",
        "scp": "user_impersonation",
        "roles": ["Spine.Read"],
    }
    v2 = {
        **common,
        "iss": ENTRA_V2,
        "aud": API_CLIENT_ID,
        "ver": "2.0",
        "azp": "client-v2",
        "oid": "oid-v2",
        "sub": "pairwise-v2",
        "preferred_username": "bo@corp.test",
        "roles": ["Spine.Plan"],
    }
    t1 = await verifier.verify_token(_mint(rsa_key, v1, kid="entra-1"))
    t2 = await verifier.verify_token(_mint(rsa_key, v2, kid="entra-1"))
    assert t1 is not None and (t1.subject, t1.client_id, t1.scopes) == ("oid-v1", "client-v1", [SCOPE_READ])
    assert t1.claims is not None and t1.claims["email"] == "ada@corp.test" and t1.claims["iss"] == ENTRA_V1
    assert t2 is not None and (t2.subject, t2.client_id, t2.scopes) == (
        "oid-v2",
        "client-v2",
        [SCOPE_READ, SCOPE_PLAN],
    )
    assert t2.claims is not None and t2.claims["email"] == "bo@corp.test"
    # Another tenant's v1 issuer is not accepted just because it looks like Entra.
    other = {**v1, "iss": "https://sts.windows.net/99999999-0000-0000-0000-000000000000/"}
    assert await verifier.verify_token(_mint(rsa_key, other, kid="entra-1")) is None


# ---- configuration --------------------------------------------------------------------


def test_the_role_map_is_parsed_from_one_line() -> None:
    parsed = parse_role_map("spine-readers=spine:read; spine-planners = spine:read spine:plan ;")
    assert parsed == ROLE_MAP


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("spine-readers", "="),
        ("=spine:read", "group"),
        ("spine-readers=", "scope"),
        ("spine-readers=spine:admin", "spine:admin"),
        ("a=spine:read; a=spine:plan", "twice"),
    ],
)
def test_a_bad_role_map_is_refused_at_start(text: str, complaint: str) -> None:
    with pytest.raises(ValueError, match=complaint):
        parse_role_map(text)


@pytest.mark.parametrize("alg", ["none", "HS256", "HS512", "XX999"])
def test_only_asymmetric_algorithms_can_be_pinned(alg: str) -> None:
    with pytest.raises(ValueError, match=alg):
        _config(algorithms=("RS256", alg))


def test_a_configuration_needs_an_audience_and_https() -> None:
    with pytest.raises(ValueError, match="audience"):
        _config(audiences=())
    with pytest.raises(ValueError, match="https"):
        _config(issuer="http://idp.example.test")
    with pytest.raises(ValueError, match="https"):
        _config(jwks_url="http://idp.example.test/keys")
    assert _config(issuer="http://127.0.0.1:9000/realm").issuer  # a local test IdP is fine


_OIDC_ENV = (
    "ORCHESTRATOR_MCP_ISSUER_URL",
    "ORCHESTRATOR_MCP_RESOURCE_URL",
    "ORCHESTRATOR_MCP_AUDIENCE",
    "ORCHESTRATOR_MCP_ACCEPTED_ISSUERS",
    "ORCHESTRATOR_MCP_JWKS_URL",
    "ORCHESTRATOR_MCP_JWT_ALGORITHMS",
    "ORCHESTRATOR_MCP_SUBJECT_CLAIM",
    "ORCHESTRATOR_MCP_EMAIL_CLAIMS",
    "ORCHESTRATOR_MCP_ROLES_CLAIM",
    "ORCHESTRATOR_MCP_ROLE_MAP",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _OIDC_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_verifier_is_configured_from_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ORCHESTRATOR_MCP_ISSUER_URL", ENTRA_V2)
    clean_env.setenv("ORCHESTRATOR_MCP_AUDIENCE", f"{APP_ID_URI}, {API_CLIENT_ID}")
    clean_env.setenv("ORCHESTRATOR_MCP_ACCEPTED_ISSUERS", ENTRA_V1)
    clean_env.setenv("ORCHESTRATOR_MCP_JWKS_URL", ENTRA_JWKS)
    clean_env.setenv("ORCHESTRATOR_MCP_JWT_ALGORITHMS", "RS256 PS256")
    clean_env.setenv("ORCHESTRATOR_MCP_SUBJECT_CLAIM", "oid")
    clean_env.setenv("ORCHESTRATOR_MCP_EMAIL_CLAIMS", "upn,email")
    clean_env.setenv("ORCHESTRATOR_MCP_ROLES_CLAIM", "groups")
    clean_env.setenv("ORCHESTRATOR_MCP_ROLE_MAP", "g1=spine:read")
    config = jwks_verifier_from_env().config
    assert config.issuer == ENTRA_V2
    assert config.audiences == (APP_ID_URI, API_CLIENT_ID)
    assert config.accepted_issuers == (ENTRA_V1,)
    assert config.jwks_url == ENTRA_JWKS
    assert config.algorithms == ("RS256", "PS256")
    assert config.subject_claim == "oid"
    assert config.email_claims == ("upn", "email")
    assert config.roles_claim == "groups"
    assert config.role_map == {"g1": frozenset({SCOPE_READ})}


def test_env_defaults(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ORCHESTRATOR_MCP_ISSUER_URL", ISSUER)
    clean_env.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.test/mcp")
    config = jwks_verifier_from_env().config
    assert config.audiences == ("https://mcp.example.test/mcp",)  # the resource URL by default
    assert config.accepted_issuers == ()
    assert config.jwks_url is None
    assert config.algorithms == ("RS256",)
    assert config.subject_claim == "sub"
    assert config.email_claims == ("email", "preferred_username", "upn")
    assert config.roles_claim == "roles"
    assert config.role_map is None


def test_env_without_an_audience_is_refused(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("ORCHESTRATOR_MCP_ISSUER_URL", ISSUER)
    with pytest.raises(ValueError, match="AUDIENCE"):
        jwks_verifier_from_env()


def test_run_is_a_scope_a_role_can_map_to() -> None:
    """v1 serves read + plan, but the map does not second-guess the level: a `build` server's
    admin may grant `spine:run`; the level ceiling is the control for what is served."""
    assert parse_role_map("ops=spine:run") == {"ops": frozenset({SCOPE_RUN})}
