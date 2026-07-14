"""Tests for orchestrator.launch — the ``orchestrator up`` one-command launcher.

Exercises the pure command/env/readiness helpers (no real subprocesses or
Docker) plus the ``up`` CLI wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator import launch
from orchestrator.cli import app


# --------------------------------------------------------------------------- #
# LaunchConfig
# --------------------------------------------------------------------------- #
class TestLaunchConfig:
    def test_app_url_shows_localhost_for_loopback(self) -> None:
        assert launch.LaunchConfig(host="127.0.0.1", port=8000).app_url == "http://localhost:8000/app"

    def test_app_url_keeps_real_host(self) -> None:
        assert launch.LaunchConfig(host="example.com", port=80).app_url == "http://example.com:80/app"

    def test_health_urls_bind_to_host(self) -> None:
        cfg = launch.LaunchConfig(host="127.0.0.1", port=8001)
        assert cfg.healthz_url == "http://127.0.0.1:8001/healthz"
        assert cfg.readyz_url == "http://127.0.0.1:8001/readyz"


# --------------------------------------------------------------------------- #
# find_project_file
# --------------------------------------------------------------------------- #
class TestFindProjectFile:
    def test_finds_marker_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        found = launch.find_project_file("marker.txt", start=sub)
        assert found is not None
        assert found.samefile(tmp_path / "marker.txt")

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert launch.find_project_file("nope.txt", start=tmp_path) is None


# --------------------------------------------------------------------------- #
# build_child_env
# --------------------------------------------------------------------------- #
class TestBuildChildEnv:
    def test_fills_defaults_when_unset(self) -> None:
        cfg = launch.LaunchConfig(env={}, api_key="k1", session_secret="s1")
        env = launch.build_child_env(cfg)
        assert env["ORCHESTRATOR_API_KEY"] == "k1"
        assert env["ORCHESTRATOR_SESSION_SECRET"] == "s1"
        assert env["ORCHESTRATOR_ARTIFACT_STORE"] == "memory"
        assert env["SDLC_CODEGEN"] == "llm"

    def test_existing_values_win(self) -> None:
        cfg = launch.LaunchConfig(
            env={"ORCHESTRATOR_API_KEY": "real", "ORCHESTRATOR_ARTIFACT_STORE": "minio"},
            api_key="k1",
        )
        env = launch.build_child_env(cfg)
        assert env["ORCHESTRATOR_API_KEY"] == "real"
        assert env["ORCHESTRATOR_ARTIFACT_STORE"] == "minio"


# --------------------------------------------------------------------------- #
# Command builders
# --------------------------------------------------------------------------- #
class TestCommandBuilders:
    def test_api_command(self) -> None:
        cmd = launch.api_command(launch.LaunchConfig(host="0.0.0.0", port=9001))
        assert "uvicorn" in cmd
        assert "orchestrator.registry.api.app:create_app" in cmd
        assert "--factory" in cmd
        assert "9001" in cmd
        assert "0.0.0.0" in cmd

    def test_worker_command_targets_sdlc_worker(self) -> None:
        # Must be the SDLC worker (hosts SDLCWorkflow on sdlc-tasks) — the queue
        # the inbox's delegate action starts on — not orchestrator.temporal.worker.
        assert launch.worker_command()[-1] == "orchestrator.sdlc.worker"

    def test_alembic_command(self, tmp_path: Path) -> None:
        ini = tmp_path / "alembic.ini"
        ini.write_text("[alembic]\n", encoding="utf-8")
        cmd = launch.alembic_command(ini)
        assert cmd[-2:] == ["upgrade", "head"]
        assert str(ini) in cmd


# --------------------------------------------------------------------------- #
# resolve_compose_base
# --------------------------------------------------------------------------- #
class TestResolveComposeBase:
    def test_prefers_compose_v2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.launch.shutil.which", lambda name: "/x/docker" if name == "docker" else None
        )
        monkeypatch.setattr("orchestrator.launch.subprocess.run", lambda *a, **k: None)
        assert launch.resolve_compose_base() == ["docker", "compose"]

    def test_falls_back_to_legacy_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "orchestrator.launch.shutil.which",
            lambda name: "/x/docker-compose" if name == "docker-compose" else None,
        )
        assert launch.resolve_compose_base() == ["docker-compose"]

    def test_none_when_docker_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("orchestrator.launch.shutil.which", lambda name: None)
        assert launch.resolve_compose_base() is None


# --------------------------------------------------------------------------- #
# wait_for_http
# --------------------------------------------------------------------------- #
class _FakeResp:
    status = 200

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestWaitForHttp:
    def test_returns_true_on_2xx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("orchestrator.launch.urllib.request.urlopen", lambda *a, **k: _FakeResp())
        assert launch.wait_for_http("http://localhost/healthz", timeout=1) is True

    def test_returns_false_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a: object, **_k: object) -> _FakeResp:
            raise OSError("refused")

        monkeypatch.setattr("orchestrator.launch.urllib.request.urlopen", boom)
        assert launch.wait_for_http("http://localhost/healthz", timeout=0.2, interval=0.05) is False


# --------------------------------------------------------------------------- #
# _resolve_compose_file
# --------------------------------------------------------------------------- #
class TestResolveComposeFile:
    def test_explicit_file_wins(self, tmp_path: Path) -> None:
        f = tmp_path / "my.yml"
        f.write_text("services: {}\n", encoding="utf-8")
        cfg = launch.LaunchConfig(compose_file=f)
        assert launch._resolve_compose_file(cfg, echo=lambda _m: None) == f

    def test_writes_embedded_when_none_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(launch, "find_project_file", lambda _name: None)
        out = launch._resolve_compose_file(launch.LaunchConfig(env={}), echo=lambda _m: None)
        assert out.exists()
        body = out.read_text(encoding="utf-8")
        assert "temporal:" in body
        assert "postgres:" in body


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_up_help_lists_flags() -> None:
    result = CliRunner().invoke(app, ["up", "--help"])
    assert result.exit_code == 0
    assert "--no-docker" in result.stdout
    assert "--no-worker" in result.stdout
    assert "--port" in result.stdout
