"""A CLI command that cannot reach the API says so in one line (B36).

``template list`` against a closed port used to end in a 245-line traceback and exit 1: only an
HTTP *status* of 400 or more was handled, and a connect or timeout error never produced one.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(status: int, body: Any) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server's name
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def ok_api() -> Iterator[str]:
    yield from _serve(200, {"items": ["a"]})


@pytest.fixture()
def rejecting_api() -> Iterator[str]:
    yield from _serve(401, {"detail": "invalid api key"})


def test_a_closed_port_is_one_line_not_a_traceback(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"http://127.0.0.1:{_closed_port()}"
    monkeypatch.setenv("ORCHESTRATOR_API_URL", url)

    result = runner.invoke(app, ["template", "list"])

    assert result.exit_code == 1
    assert not isinstance(result.exception, httpx.HTTPError), "the transport error must not escape"
    out = result.output
    assert url in out
    assert "orchestrator up" in out
    assert "Traceback" not in out
    assert len(out.strip().splitlines()) <= 3


def test_every_registry_command_gets_the_same_treatment(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", f"http://127.0.0.1:{_closed_port()}")
    for argv in (["template", "list"], ["template", "show", "x"], ["contract", "list"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, argv
        assert "Traceback" not in result.output, argv
        assert "orchestrator up" in result.output, argv


def test_a_timeout_names_the_knob(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ORCHESTRATOR_API_TIMEOUT_SECONDS", "7")

    def slow(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(httpx.Client, "send", slow)
    result = runner.invoke(app, ["template", "list"])

    assert result.exit_code == 1
    assert "ORCHESTRATOR_API_TIMEOUT_SECONDS" in result.output
    assert "7" in result.output
    assert "http://127.0.0.1:1" in result.output


def test_any_other_transport_error_is_one_line_too(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://127.0.0.1:1")

    def broken(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        raise httpx.RemoteProtocolError("server disconnected", request=request)

    monkeypatch.setattr(httpx.Client, "send", broken)
    result = runner.invoke(app, ["template", "list"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "server disconnected" in result.output


def test_a_rejected_key_says_so(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, rejecting_api: str
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", rejecting_api)

    result = runner.invoke(app, ["template", "list"])

    assert result.exit_code == 1
    assert "401" in result.output, "the existing status dump stays"
    assert "ORCHESTRATOR_API_KEY" in result.output


def test_a_working_api_is_unchanged(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, ok_api: str) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", ok_api)

    result = runner.invoke(app, ["template", "list"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"items": ["a"]}
