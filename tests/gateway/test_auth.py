from __future__ import annotations

import pytest

from orchestrator.gateway.auth import CredentialError, _env_key_for, resolve_credentials


def test_env_key_canonicalisation() -> None:
    assert _env_key_for("tool.web_search", "0.2.1") == "TOOL_TOOL__WEB_SEARCH_0_2_1_API_KEY"


def test_resolve_none_returns_empty() -> None:
    creds = resolve_credentials(contract_id="tool.x", version="0.1.0", auth_spec={"type": "none"})
    assert creds.headers == {}
    assert creds.secrets == {}


def test_resolve_api_key_from_default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env_key = _env_key_for("tool.x", "0.1.0")
    monkeypatch.setenv(env_key, "sek-123")
    creds = resolve_credentials(contract_id="tool.x", version="0.1.0", auth_spec={"type": "api_key"})
    assert creds.headers["Authorization"] == "Bearer sek-123"
    assert creds.secrets == {env_key: "sek-123"}


def test_resolve_api_key_via_secret_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_KEY", "abc")
    creds = resolve_credentials(
        contract_id="tool.web_search",
        version="0.1.0",
        auth_spec={"type": "api_key", "secret_ref": "TAVILY_KEY"},
    )
    assert creds.headers["Authorization"] == "Bearer abc"


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_env_key_for("tool.x", "0.1.0"), raising=False)
    with pytest.raises(CredentialError, match="missing API key"):
        resolve_credentials(contract_id="tool.x", version="0.1.0", auth_spec={"type": "api_key"})


def test_unsupported_auth_type_raises() -> None:
    with pytest.raises(CredentialError, match="not supported yet"):
        resolve_credentials(contract_id="tool.x", version="0.1.0", auth_spec={"type": "oauth2"})
