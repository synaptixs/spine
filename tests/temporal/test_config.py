"""Unit tests for TemporalConfig: env-var auto-detection of cloud vs local."""

from __future__ import annotations

import pytest

from orchestrator.temporal.config import TemporalConfig


def test_defaults_match_docker_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars set → talk to local docker-compose."""
    for key in ("TEMPORAL_HOST", "TEMPORAL_NAMESPACE", "TEMPORAL_TASK_QUEUE", "TEMPORAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    cfg = TemporalConfig.from_env()
    assert cfg.host == "localhost:7233"
    assert cfg.namespace == "default"
    assert cfg.task_queue == "orchestrator-tasks"
    assert cfg.api_key is None
    assert cfg.use_tls is False
    assert cfg.is_cloud is False


def test_api_key_implies_cloud_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEMPORAL_API_KEY set → cloud mode with TLS."""
    monkeypatch.setenv("TEMPORAL_API_KEY", "tk-abc")
    monkeypatch.setenv("TEMPORAL_HOST", "my-ns.tmprl.cloud:7233")
    monkeypatch.setenv("TEMPORAL_NAMESPACE", "prod.acct123")
    cfg = TemporalConfig.from_env()
    assert cfg.api_key == "tk-abc"
    assert cfg.use_tls is True
    assert cfg.is_cloud is True
    assert cfg.host == "my-ns.tmprl.cloud:7233"
    assert cfg.namespace == "prod.acct123"


def test_empty_api_key_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty TEMPORAL_API_KEY (common with poorly-defaulted .env files)
    must not flip us into cloud mode and demand TLS."""
    monkeypatch.setenv("TEMPORAL_API_KEY", "")
    cfg = TemporalConfig.from_env()
    assert cfg.api_key is None
    assert cfg.use_tls is False
