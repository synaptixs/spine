"""The shared server end to end: a real streamable-HTTP request through the SDK's auth
middleware, with company-login tokens minted here and the level as a ceiling.

The unit tests in ``test_oidc.py`` prove each check; these prove the checks are what stands
between a request and a tool — and that the level (what the server shows) and the token's
scopes (what this person may call) compose: the level hides, the scope refuses.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra")

import httpx
import jwt
from asgi_lifespan import LifespanManager
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

import orchestrator.plugin.oidc as oidc
from orchestrator.plugin.server import build_http_server

ISSUER = "https://idp.example.test/oauth2/default"
AUDIENCE = "https://mcp.example.test/mcp"
ROLE_MAP = "spine-readers=spine:read; spine-planners=spine:read spine:plan"

_ENV = {
    "ORCHESTRATOR_MCP_ISSUER_URL": ISSUER,
    "ORCHESTRATOR_MCP_RESOURCE_URL": AUDIENCE,
    "ORCHESTRATOR_MCP_ROLE_MAP": ROLE_MAP,
}
_CLEARED = (
    "ORCHESTRATOR_MCP_TOKEN",
    "ORCHESTRATOR_MCP_INTROSPECTION_URL",
    "ORCHESTRATOR_MCP_AUDIENCE",
    "ORCHESTRATOR_MCP_JWKS_URL",
    "ORCHESTRATOR_MCP_ACCEPTED_ISSUERS",
    "ORCHESTRATOR_MCP_JWT_ALGORITHMS",
    "ORCHESTRATOR_MCP_SUBJECT_CLAIM",
    "ORCHESTRATOR_MCP_ROLES_CLAIM",
    "ORCHESTRATOR_MCP_LEVEL",
    "ORCHESTRATOR_MCP_REPOS",
    "ORCHESTRATOR_MCP_ADVERTISED_SCOPES",
)


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> bool:
        rows.append(kwargs)
        return True

    monkeypatch.setattr("orchestrator.plugin.audit.record_invocation", record)
    return rows


@pytest.fixture(autouse=True)
def _company_login(monkeypatch: pytest.MonkeyPatch, key: rsa.RSAPrivateKey) -> None:
    for name in _CLEARED:
        monkeypatch.delenv(name, raising=False)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": "k1", "alg": "RS256", "use": "sig"}
    docs: dict[str, dict[str, Any]] = {
        ISSUER + "/.well-known/openid-configuration": {"issuer": ISSUER, "jwks_uri": ISSUER + "/v1/keys"},
        ISSUER + "/v1/keys": {"keys": [jwk]},
    }

    async def fetch(url: str) -> dict[str, Any]:
        return docs[url]

    monkeypatch.setattr(oidc, "_http_fetch", fetch)


def _token(key: rsa.RSAPrivateKey, *, roles: list[str], aud: str = AUDIENCE) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": aud,
        "sub": "person-1",
        "email": "ada@example.test",
        "cid": "claude-code",
        "exp": now + 600,
        "nbf": now - 10,
        "roles": roles,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


@pytest.fixture
async def client_for() -> AsyncIterator[Any]:
    managers: list[LifespanManager] = []

    async def make(level: str) -> httpx.AsyncClient:
        built = build_http_server(level=level)
        app = built.server.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True
        )
        manager = LifespanManager(app)
        await manager.__aenter__()
        managers.append(manager)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://127.0.0.1:8080"
        )

    yield make
    for manager in reversed(managers):
        await manager.__aexit__(None, None, None)


async def _rpc(
    client: httpx.AsyncClient, token: str | None, method: str, params: dict[str, Any]
) -> httpx.Response:
    headers = dict(_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    await client.post("/mcp", json=_INIT, headers=headers)
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers
    )


async def _tool_names(client: httpx.AsyncClient, token: str) -> set[str]:
    resp = await _rpc(client, token, "tools/list", {})
    assert resp.status_code == 200, resp.text
    return {tool["name"] for tool in resp.json()["result"]["tools"]}


async def test_no_token_and_a_token_for_another_api_are_both_turned_away(
    client_for: Any, key: rsa.RSAPrivateKey
) -> None:
    client = await client_for("plan")
    assert (await _rpc(client, None, "tools/list", {})).status_code == 401
    wrong = _token(key, roles=["spine-planners"], aud="api://another-api")
    assert (await _rpc(client, wrong, "tools/list", {})).status_code == 401


async def test_a_person_in_no_mapped_group_cannot_reach_any_tool(
    client_for: Any, key: rsa.RSAPrivateKey
) -> None:
    client = await client_for("plan")
    resp = await _rpc(client, _token(key, roles=["finance"]), "tools/list", {})
    assert resp.status_code == 403  # signed and valid, but below the read floor


async def test_a_reader_on_a_plan_server_sees_plan_tools_but_is_refused_by_scope(
    client_for: Any, key: rsa.RSAPrivateKey, audit: list[dict[str, Any]]
) -> None:
    client = await client_for("plan")
    reader = _token(key, roles=["spine-readers"])
    assert "sdlc_plan" in await _tool_names(client, reader)  # the level shows it
    resp = await _rpc(
        client,
        reader,
        "tools/call",
        {"name": "sdlc_plan", "arguments": {"repo_path": ".", "spec": {"title": "x"}}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["result"]
    text = body["content"][0]["text"]
    assert "spine:plan" in text and "needs" in text  # the scope refuses it
    (row,) = audit
    assert row["outcome"] == "denied" and row["denied_scope"] == "spine:plan"
    assert row["token"].subject == "person-1"  # the refusal is held against the person


async def test_a_planner_on_a_pkg_server_never_sees_plan_tools(
    client_for: Any, key: rsa.RSAPrivateKey
) -> None:
    client = await client_for("pkg")
    names = await _tool_names(client, _token(key, roles=["spine-planners"]))
    assert "sdlc_plan" not in names and "blast_radius" in names  # the level is the ceiling


async def test_a_planner_on_a_plan_server_sees_plan_tools(client_for: Any, key: rsa.RSAPrivateKey) -> None:
    client = await client_for("plan")
    names = await _tool_names(client, _token(key, roles=["spine-planners"]))
    assert {"sdlc_plan", "blast_radius"} <= names
    assert "sdlc_start_run" not in names  # build-level tools stay hidden whatever the token


async def test_an_unlisted_repository_is_refused_over_http_and_held_against_the_person(
    client_for: Any,
    key: rsa.RSAPrivateKey,
    audit: list[dict[str, Any]],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed, outside = tmp_path / "app", tmp_path / "outside"
    listed.mkdir()
    outside.mkdir()
    admin = tmp_path / "repos.yaml"
    admin.write_text(f"repos:\n  app: {listed}\n")
    monkeypatch.setenv("ORCHESTRATOR_MCP_REPOS", str(admin))
    client = await client_for("plan")
    reader = _token(key, roles=["spine-readers"])

    async def call(repo_path: str) -> str:
        args = {"name": "read_memory_bank", "arguments": {"repo_path": repo_path}}
        resp = await _rpc(client, reader, "tools/call", args)
        assert resp.status_code == 200, resp.text
        text: str = resp.json()["result"]["content"][0]["text"]
        return text

    assert "not a repository this server serves" in await call(str(outside))
    assert "not a repository this server serves" not in await call(str(listed))
    refused, served = audit
    assert refused["outcome"] == "refused" and refused["refused_argument"] == "repo_path"
    assert refused["token"].subject == "person-1" and refused["token"].claims["email"] == "ada@example.test"
    assert served["tool"] == "read_memory_bank" and served["outcome"] in ("ok", "error")


#: Where RFC 9728 puts the metadata for a resource at ``/mcp``.
_METADATA = "/.well-known/oauth-protected-resource/mcp"
#: Entra's name for the API's scope: the App ID URI plus the scope. A bare ``spine:read`` is
#: read as a Microsoft Graph scope and the sign-in fails with AADSTS9010010.
_ENTRA_SCOPE = AUDIENCE + "/Spine.Access"


async def test_the_metadata_advertises_the_providers_scope_name_while_the_floor_stays_read(
    client_for: Any, key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_ADVERTISED_SCOPES", _ENTRA_SCOPE)
    client = await client_for("plan")

    unauthenticated = await _rpc(client, None, "tools/list", {})
    assert unauthenticated.status_code == 401
    assert _METADATA in unauthenticated.headers["www-authenticate"]  # how a client finds the metadata
    metadata = (await client.get(_METADATA)).json()
    assert metadata["scopes_supported"] == [_ENTRA_SCOPE]
    assert metadata["resource"] == AUDIENCE

    # What the server *requires* is unchanged: a reader passes, a person below the floor does not.
    assert "blast_radius" in await _tool_names(client, _token(key, roles=["spine-readers"]))
    assert (await _rpc(client, _token(key, roles=["finance"]), "tools/list", {})).status_code == 403


@pytest.mark.parametrize("value", [None, "", "  ,  "])
async def test_without_an_advertised_list_the_metadata_names_the_floor(
    client_for: Any, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("ORCHESTRATOR_MCP_ADVERTISED_SCOPES", value)
    client = await client_for("plan")
    assert (await client.get(_METADATA)).json()["scopes_supported"] == ["spine:read"]
