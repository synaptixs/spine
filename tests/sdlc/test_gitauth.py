"""Tests for SDLC clone/push authentication (token-injected clone URLs)."""

from __future__ import annotations

import httpx
import pytest

from orchestrator.codereview.auth import GitHubAppAuth
from orchestrator.codereview.config import GitHubAppConfig
from orchestrator.sdlc import gitauth
from orchestrator.sdlc.gitauth import _inject_token, _parse_github_owner_repo, authenticate_repo_url


class TestParseOwnerRepo:
    def test_https_url(self) -> None:
        assert _parse_github_owner_repo("https://github.com/acme/widget") == ("acme", "widget")

    def test_strips_dot_git_suffix(self) -> None:
        assert _parse_github_owner_repo("https://github.com/acme/widget.git") == ("acme", "widget")

    def test_preserves_trailing_dot_in_name(self) -> None:
        # Real repo names can end in '.' (e.g. Example-Service.).
        assert _parse_github_owner_repo("https://github.com/acme/widget.") == ("acme", "widget.")

    def test_ssh_url_is_not_github_https(self) -> None:
        assert _parse_github_owner_repo("git@github.com:acme/widget.git") is None

    def test_non_github_host(self) -> None:
        assert _parse_github_owner_repo("https://gitlab.com/acme/widget") is None

    def test_malformed_path(self) -> None:
        assert _parse_github_owner_repo("https://github.com/acme") is None


def test_inject_token_embeds_userinfo() -> None:
    out = _inject_token("https://github.com/acme/widget.", "ghs_abc123")
    assert out == "https://x-access-token:ghs_abc123@github.com/acme/widget."


def test_inject_token_url_encodes_special_chars() -> None:
    out = _inject_token("https://github.com/acme/widget", "a/b+c@d")
    assert "x-access-token:a%2Fb%2Bc%40d@github.com" in out


class TestAuthenticateRepoUrl:
    async def test_env_pat_is_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "pat-from-env")
        out = await authenticate_repo_url("https://github.com/acme/widget")
        assert out == "https://x-access-token:pat-from-env@github.com/acme/widget"

    async def test_gh_token_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "gh-token")
        out = await authenticate_repo_url("https://github.com/acme/widget")
        assert "x-access-token:gh-token@" in str(out)

    async def test_non_github_url_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "pat")
        url = "https://gitlab.com/acme/widget"
        assert await authenticate_repo_url(url) == url

    async def test_already_credentialed_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "pat")
        url = "https://x-access-token:existing@github.com/acme/widget"
        assert await authenticate_repo_url(url) == url

    async def test_none_url_passthrough(self) -> None:
        assert await authenticate_repo_url(None) is None

    async def test_no_token_no_app_returns_bare_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_APP_ID"):
            monkeypatch.delenv(key, raising=False)
        # Force an unconfigured App regardless of the developer's real .env.
        monkeypatch.setattr(gitauth, "GitHubAppConfig", lambda: GitHubAppConfig(app_id="", private_key=""))
        url = "https://github.com/acme/widget"
        assert await authenticate_repo_url(url) == url

    async def test_app_installation_token_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no env token but a configured App, discover the installation and
        mint a token — both calls go through one mocked transport."""
        for key in ("GITHUB_TOKEN", "GH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(
            gitauth, "GitHubAppConfig", lambda: GitHubAppConfig(app_id="42", private_key="pem")
        )
        # Avoid signing a real RS256 JWT (no real key in the test).
        monkeypatch.setattr(gitauth, "build_app_jwt", lambda *a, **k: "fake.jwt")
        monkeypatch.setattr(GitHubAppAuth, "app_jwt", lambda self, **k: "fake.jwt")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/repos/acme/widget/installation":
                return httpx.Response(200, json={"id": 999})
            if request.url.path == "/app/installations/999/access_tokens":
                return httpx.Response(201, json={"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"})
            return httpx.Response(404)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            out = await authenticate_repo_url("https://github.com/acme/widget", http_client=client)
        finally:
            await client.aclose()
        assert out == "https://x-access-token:ghs_minted@github.com/acme/widget"
