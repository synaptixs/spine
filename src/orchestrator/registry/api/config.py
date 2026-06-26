"""Registry service configuration."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings, populated from environment variables."""

    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
        description="SQLAlchemy async URL for the registry database.",
    )
    api_key: str = Field(
        default="dev-key",
        description="Single API key clients must present. Replace via env var in any non-dev environment.",
    )
    # Bet 2c-ii — optional RBAC + multi-tenancy. A JSON object mapping API key →
    # principal, e.g. ``{"key-a": {"id": "alice", "tenant_id": "acme",
    # "roles": ["approver"]}}``. When empty (the default), the single ``api_key``
    # resolves to a wildcard principal in tenant ``"default"`` — i.e. exactly
    # today's single-tenant, everyone-can-approve behavior. Set
    # ``ORCHESTRATOR_PRINCIPALS`` to turn on per-key tenant + role enforcement.
    principals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Secret that signs web-session cookies (P0b login). Replace via
    # ``ORCHESTRATOR_SESSION_SECRET`` in any non-dev environment — a known secret
    # would let anyone forge a session.
    session_secret: str = Field(default="dev-session-secret")
    db_echo: bool = False

    @field_validator("principals", mode="before")
    @classmethod
    def _parse_principals(cls, v: Any) -> Any:
        # Env vars arrive as strings; accept a JSON object (or a dict directly).
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v else {}
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
