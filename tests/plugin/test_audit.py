"""Per-person audit on the HTTP transport: every call and every refusal, against the token.

Every call is one JSON log line; the registry gets it too only when one is configured (D35)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from orchestrator.plugin.audit import (
    arguments_digest,
    configure_audit_logging,
    principal_of,
    record_invocation,
)


class _Token:
    def __init__(
        self,
        scopes: list[str],
        *,
        client_id: str = "app-1",
        subject: str | None = "ana",
        claims: dict[str, Any] | None = None,
    ) -> None:
        self.scopes, self.client_id, self.subject, self.claims = scopes, client_id, subject, claims


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_API_URL", raising=False)


def _lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "orchestrator.plugin.audit" and r.levelno == logging.INFO
    ]


def _registry_with(handler: object, monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.plugin.registry_client as rc

    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://test")
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
        "email": None,
        "issuer": None,
        "scopes": ["spine:read", "spine:run"],
    }


def test_the_principal_names_the_person_a_company_login_verified() -> None:
    token = _Token(
        ["spine:read"], subject="oid-7", claims={"email": "ada@corp.test", "iss": "https://idp.test"}
    )
    assert principal_of(token) == {
        "client_id": "app-1",
        "subject": "oid-7",
        "email": "ada@corp.test",
        "issuer": "https://idp.test",
        "scopes": ["spine:read"],
    }


async def test_every_call_is_one_json_line_naming_the_person_and_never_the_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = _Token(
        ["spine:read"], subject="oid-7", claims={"email": "ada@corp.test", "iss": "https://idp.test"}
    )
    with caplog.at_level(logging.INFO, logger="orchestrator.plugin.audit"):
        ok = await record_invocation(
            token=token,
            tool="blast_radius",
            scope="spine:read",
            arguments={"repo_path": "/repos/app", "symbol": "secret_fn"},
            outcome="ok",
        )
    assert ok is False  # no registry configured: the log line is the record
    (line,) = _lines(caplog)
    assert line["event"] == "mcp_tool_invoked" and line["tool"] == "blast_radius" and line["outcome"] == "ok"
    assert line["principal"]["subject"] == "oid-7" and line["principal"]["email"] == "ada@corp.test"
    assert line["arguments"]["keys"] == ["repo_path", "symbol"]
    assert "secret_fn" not in json.dumps(line) and "/repos/app" not in json.dumps(line)


async def test_without_a_registry_nothing_is_sent_and_nothing_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import orchestrator.plugin.registry_client as rc

    def boom() -> Any:
        raise AssertionError("the registry was called with no ORCHESTRATOR_API_URL set")

    monkeypatch.setattr(rc, "registry_client", boom)
    with caplog.at_level(logging.INFO, logger="orchestrator.plugin.audit"):
        await record_invocation(
            token=_Token(["spine:read"]), tool="map_repo", scope="spine:read", arguments={}, outcome="ok"
        )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(_lines(caplog)) == 1


async def test_a_refusal_is_recorded_with_what_was_refused(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="orchestrator.plugin.audit"):
        await record_invocation(
            token=_Token(["spine:read"]),
            tool="map_repo",
            scope="spine:read",
            arguments={"repo_path": "/etc"},
            outcome="refused",
            refused_argument="repo_path",
        )
    (line,) = _lines(caplog)
    assert line["event"] == "mcp_repo_refused" and line["refused_argument"] == "repo_path"
    assert "/etc" not in json.dumps(line)


def test_the_http_server_sends_audit_lines_to_stderr_as_bare_json() -> None:
    import sys

    logger = logging.getLogger("orchestrator.plugin.audit")
    before = list(logger.handlers)
    try:
        configure_audit_logging()
        configure_audit_logging()  # idempotent: one handler, not two
        added = [h for h in logger.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0], logging.StreamHandler)
        assert added[0].stream is sys.stderr and logger.level == logging.INFO
        assert logger.propagate is False  # one line per call, not a second copy through the root handler
        record = logging.LogRecord("orchestrator.plugin.audit", logging.INFO, "", 0, '{"a": 1}', None, None)
        assert added[0].format(record) == '{"a": 1}'
    finally:
        for h in list(logger.handlers):
            if h not in before:
                logger.removeHandler(h)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


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
    with caplog.at_level(logging.INFO, logger="orchestrator.plugin.audit"):
        ok = await record_invocation(
            token=_Token(["spine:run"]),
            tool="sdlc_feature",
            scope="spine:run",
            arguments={"a": 1},
            outcome="ok",
        )
    assert ok is False
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "registry" in warning.getMessage() and "refused" in warning.registry_error  # type: ignore[attr-defined]
    (line,) = _lines(caplog)  # the line is written whatever the registry does
    assert line["tool"] == "sdlc_feature" and line["outcome"] == "ok"


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


async def test_the_guard_records_every_call_and_every_denial(
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

    await _scoped(doctor, SCOPE_READ)()  # read tier: recorded too (D21, D35)
    assert len(recorded) == 2 and recorded[-1]["tool"] == "doctor" and recorded[-1]["outcome"] == "ok"

    _as_token([SCOPE_RUN])
    out = await _scoped(registry_decide, SCOPE_RUN)("g1", "shrug")  # the tool's own validation answers
    assert "error" in out
    assert recorded[-1]["outcome"] == "error" and recorded[-1]["token"].client_id == "app-1"

    _as_token(None)  # stdio: no token, nothing recorded
    await _scoped(registry_decide, SCOPE_RUN)("g1", "shrug")
    assert len(recorded) == 3
