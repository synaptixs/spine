"""Registry service configuration."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

#: The fields that are credentials. These, and only these, are asked of the secrets provider —
#: by their **environment-variable name** (``ORCHESTRATOR_API_KEY``), so a vault path mirrors the
#: variable an operator already knows. Everything else stays plain pydantic-settings.
SECRET_FIELDS: frozenset[str] = frozenset({"database_url", "api_key", "principals", "session_secret"})


class SecretProviderSource(PydanticBaseSettingsSource):
    """A settings source that reads ``SECRET_FIELDS`` through ``core.secrets``.

    Ordered **after** init kwargs and **before** the environment: a test that builds
    ``Settings(api_key=...)`` is untouched, and under the default ``env`` provider this source
    reads the very variable the env source would have — so the result is byte-identical, which is
    the whole promise of the seam's phase 2. A vault provider changes only who answers.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        if field_name not in SECRET_FIELDS:
            return None, field_name, False
        from orchestrator.core.secrets import get_secret

        prefix = self.config.get("env_prefix") or ""
        return get_secret(f"{prefix}{field_name}".upper()), field_name, False

    def __call__(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, field in self.settings_cls.model_fields.items():
            value, key, _complex = self.get_field_value(field, name)
            if value is not None:
                out[key] = value
        return out


class Settings(BaseSettings):
    """Process-wide settings, populated from environment variables.

    Credentials (``SECRET_FIELDS``) are read through the secrets seam in ``core.secrets``, which
    defaults to the environment — so this docstring stays true until an operator selects a vault.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            SecretProviderSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

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
    # Root under which capability jobs (understand / state / pkg) may analyse
    # repos. A web request names a repo by a path *relative to this root* (or an
    # absolute path that resolves inside it); anything escaping the root is
    # rejected — so a web caller can never point the analyser at an arbitrary
    # server-side directory. Unset (the default) → the server's working
    # directory, which is the repo a local ``orchestrator up`` was started in.
    workspace_root: str | None = Field(default=None)
    # Remote repos may also be analysed by git URL (cloned on demand). Only these
    # hosts are allowed — a comma-separated list; ``*`` allows any host. Default:
    # the public providers. Add an enterprise/custom host (e.g. ``git.acme.com``)
    # here. ``file://``, plaintext ``http://``, ``localhost``, and private/
    # loopback/metadata IPs are always rejected (SSRF guard).
    repo_allowed_hosts: str = Field(default="github.com,bitbucket.org,gitlab.com")
    # When False (default), a local repo path must resolve under ``workspace_root``.
    # Set True to allow any absolute local path (a single-user, trusted-local
    # deployment) — do NOT enable this if the UI is exposed to untrusted callers.
    repo_allow_any_local: bool = Field(default=False)
    # When False (default), the MCP connections page is read-only (list + test).
    # Set True to allow adding/editing/removing MCP servers from the UI, which
    # writes mcp.json — a stdio server's ``command`` is executed by the MCP
    # process, so enabling this on an exposed UI is a code-execution surface.
    mcp_config_writable: bool = Field(default=False)

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
