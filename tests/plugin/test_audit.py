"""Per-principal audit on the HTTP transport: run-scope calls and denials, against the token."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from orchestrator.plugin.audit import AUDITED_SCOPES, arguments_digest, principal_of, record_invocation


class _Token:
    def __init__(self, scopes: list[str], *, client_id: str = "app-1", subject: str | None = "ana") -> None:
        self.scopes, self.client_id, self.subject = scopes, client_id, subject


def _registry_with(handler: object, monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.plugin.registry_client as rc

    monkeypatch.setattr(
        rc,
        "registry_client",
        lambda: rc.RegistryClient("http://test", "k", transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


def test_the_digest_is_the_shape_of_a_call_not_its_contents() -> None:
    d = arguments_digest({"source": "file://./spec.md", "live": True, "confirm": True})
    assert d["keys"] == ["confirm", "live", "source"]
    assert len(d["sha256"]) == 64 and "spec.md" not in json.dumps(d)
    assert arguments_digest({"a": 1}) != arguments_digest({"a": 2})  # a different call, a different digest


def test_the_principal_is_what_the_token_carries() -> None:
    assert principal_of(_Token(["spine:run", "spine:read"])) == {
        "client_id": "app-1",
        "subject": "ana",
        "scopes": ["spine:read", "spine:run"],
    }


async def test_a_run_scope_call_is_recorded_against_the_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(201, json={"actor": "key-1", "action": seen["body"]["action"]})

    _registry_with(handler, monkeypatch)
    ok = await record_invocation(
        token=_Token(["spine:run"]),
        tool="sdlc_feature",
        scope="spine:run",
        arguments={"source": "file://./secret-spec.md"},
        outcome="ok",
    )
    assert ok is True and seen["path"] == "/v1/audit"
    body = seen["body"]
    assert (
        body["action"] == "mcp_tool_invoked"
        and body["resource_type"] == "mcp_tool"
        and body["resource_id"] == "sdlc_feature"
    )
    after = body["after"]
    assert (
        after["principal"]["client_id"] == "app-1"
        and after["outcome"] == "ok"
        and after["scope"] == "spine:run"
    )
    assert after["arguments"]["keys"] == ["source"] and "secret-spec" not in json.dumps(after)


async def test_a_denial_is_recorded_with_the_scope_it_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    _registry_with(handler, monkeypatch)
    await record_invocation(
        token=_Token(["spine:read"]),
        tool="registry_decide",
        scope="spine:run",
        arguments={},
        outcome="denied",
        denied_scope="spine:run",
    )
    assert (
        seen["body"]["action"] == "mcp_scope_denied" and seen["body"]["after"]["denied_scope"] == "spine:run"
    )


async def test_an_unreachable_registry_degrades_to_a_log_line_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _registry_with(handler, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="orchestrator.plugin.audit"):
        ok = await record_invocation(
            token=_Token(["spine:run"]),
            tool="sdlc_feature",
            scope="spine:run",
            arguments={"a": 1},
            outcome="ok",
        )
    assert ok is False
    record = next(r for r in caplog.records if r.name == "orchestrator.plugin.audit")
    assert "mcp_tool_invoked" in record.getMessage()
    assert record.tool == "sdlc_feature" and record.outcome == "ok" and "refused" in record.registry_error  # type: ignore[attr-defined]


def test_only_the_run_tier_is_audited() -> None:
    assert {"spine:run"} == AUDITED_SCOPES


# ---- through the guard ------------------------------------------------------------------


@pytest.fixture
def _as_token(monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("mcp")
    from mcp.server.auth.middleware.auth_context import (  # type: ignore[attr-defined]
        AuthenticatedUser,
        auth_context_var,
    )
    from mcp.server.auth.provider import AccessToken

    def as_token(scopes: list[str] | None) -> None:
        if scopes is None:
            auth_context_var.set(None)
            return
        auth_context_var.set(
            AuthenticatedUser(AccessToken(token="t", client_id="app-1", scopes=scopes, expires_at=None))
        )

    yield as_token
    auth_context_var.set(None)


async def test_the_guard_records_a_run_call_and_a_denial_but_not_a_read_call(
    _as_token: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.plugin.auth import SCOPE_READ, SCOPE_RUN
    from orchestrator.plugin.server import _scoped, doctor, registry_decide

    recorded: list[dict[str, Any]] = []

    async def fake_record(**kw: Any) -> bool:
        recorded.append(kw)
        return True

    monkeypatch.setattr("orchestrator.plugin.audit.record_invocation", fake_record)

    _as_token([SCOPE_READ])
    out = await _scoped(registry_decide, SCOPE_RUN)("g1", "approve")
    assert out["needs"] == SCOPE_RUN
    assert recorded[-1]["outcome"] == "denied" and recorded[-1]["denied_scope"] == SCOPE_RUN
    assert recorded[-1]["tool"] == "registry_decide"

    await _scoped(doctor, SCOPE_READ)()  # read tier: nothing recorded
    assert len(recorded) == 1

    _as_token([SCOPE_RUN])
    out = await _scoped(registry_decide, SCOPE_RUN)("g1", "shrug")  # the tool's own validation answers
    assert "error" in out
    assert recorded[-1]["outcome"] == "error" and recorded[-1]["token"].client_id == "app-1"

    _as_token(None)  # stdio: no token, nothing recorded
    await _scoped(registry_decide, SCOPE_RUN)("g1", "shrug")
    assert len(recorded) == 2
