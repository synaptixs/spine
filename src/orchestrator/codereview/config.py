"""Block A: GitHub App connection settings.

Env-driven, prefix ``GITHUB_APP_``. A GitHub App authenticates in two
steps (Block A.2 wires the second):

  1. App-level JWT signed with the app's private key (proves "I am this App").
  2. Installation access token minted per-installation from that JWT
     (scopes the App's permissions to one org/repo install).

Webhook signature verification (Block A.1, this commit) needs only the
``webhook_secret``. ``app_id`` + ``private_key`` are consumed by the auth
module. ``enabled`` gates whether ``create_app`` mounts the webhook router
at all — keeps the platform's existing surface unchanged when the PR
reviewer isn't configured.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GitHubAppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GITHUB_APP_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(
        default=False,
        description="Mount the PR-reviewer webhook router. Off by default so the "
        "platform's existing API surface is unchanged when unconfigured.",
    )
    # Explicit alias: the field is ``app_id`` but the env var is GITHUB_APP_ID
    # (not GITHUB_APP_APP_ID, which the prefix + field name would otherwise
    # produce). populate_by_name keeps ``GitHubAppConfig(app_id=...)`` working.
    app_id: str = Field(
        default="",
        validation_alias="GITHUB_APP_ID",
        description="GitHub App ID (numeric, as a string).",
    )
    webhook_secret: str = Field(
        default="",
        description="Shared secret GitHub signs webhook payloads with (X-Hub-Signature-256).",
    )
    private_key: str = Field(
        default="",
        description="PEM contents of the App's private key. Takes precedence over private_key_path.",
    )
    private_key_path: str = Field(
        default="",
        description="Filesystem path to the App's PEM private key. Used when private_key is empty.",
    )
    api_base_url: str = Field(
        default="https://api.github.com",
        description="GitHub REST API base. Override for GitHub Enterprise "
        "(e.g. https://github.example.com/api/v3).",
    )

    @model_validator(mode="after")
    def _load_key_from_path(self) -> GitHubAppConfig:
        """Fall back to reading the PEM off disk when only the path is set.

        Keeps secrets out of env vars in deployments that mount the key as
        a file (the common Kubernetes / Cloud Run secret-volume pattern).
        """
        if not self.private_key and self.private_key_path:
            path = Path(self.private_key_path)
            if path.is_file():
                object.__setattr__(self, "private_key", path.read_text(encoding="utf-8"))
        return self

    @property
    def webhook_configured(self) -> bool:
        """True when we can verify inbound webhook signatures."""
        return bool(self.webhook_secret)

    @property
    def api_configured(self) -> bool:
        """True when we can mint installation tokens to call the GitHub API."""
        return bool(self.app_id and self.private_key)
