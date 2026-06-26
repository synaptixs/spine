"""Authenticate the SDLC clone/push against a (private) GitHub repo.

``WorkspaceManager`` clones ``SDLC_REPO_URL`` with plain ``git clone``; a
private repo needs credentials. ``authenticate_repo_url`` rewrites an https
GitHub URL to embed a token —
``https://x-access-token:<token>@github.com/<owner>/<repo>`` — so both the
clone *and* the push that reuses ``origin`` authenticate, with no ambient
credential helper required (e.g. headless CI where ``gh`` isn't logged in).

Token source, in precedence order:

  1. ``GITHUB_TOKEN`` / ``GH_TOKEN`` — a PAT or pre-minted token. The simplest
     path: drop one in ``.env``.
  2. The configured GitHub App (``GITHUB_APP_ID`` + key, the same App the PR
     reviewer uses): discover the repo's installation and mint a ~1-hour
     installation token via ``codereview.auth.GitHubAppAuth``.

Anything that isn't a plain https github.com URL (ssh remotes, other hosts,
URLs that already carry credentials) and the no-token case are returned
unchanged — the caller falls back to whatever ambient auth exists.

Note: the embedded token lands in the clone's ``.git/config`` (origin). That's
acceptable here because SDLC worktrees are ephemeral and torn down per run, and
installation tokens self-expire in ~1 hour.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from orchestrator.codereview.auth import GitHubAppAuth, GitHubAuthError, build_app_jwt
from orchestrator.codereview.config import GitHubAppConfig

logger = logging.getLogger("orchestrator.sdlc.gitauth")

_GITHUB_HOSTS = {"github.com", "www.github.com"}


def _parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` for a plain https github.com URL, else ``None``.

    Non-https schemes (ssh ``git@…``), non-github hosts, and malformed paths
    return ``None`` so the caller leaves them untouched.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if (parts.hostname or "") not in _GITHUB_HOSTS:
        return None
    path = parts.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    segments = path.split("/")
    if len(segments) < 2 or not segments[0] or not segments[1]:
        return None
    return segments[0], segments[1]


def _inject_token(url: str, token: str) -> str:
    """Embed ``x-access-token:<token>`` as the userinfo of an https URL."""
    parts = urlsplit(url)
    netloc = f"x-access-token:{quote(token, safe='')}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _app_installation_token(
    owner: str, repo: str, config: GitHubAppConfig, *, http_client: httpx.AsyncClient | None = None
) -> str:
    """Discover the repo's App installation and mint a ~1h installation token."""
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        headers = {
            "Authorization": f"Bearer {build_app_jwt(config.app_id, config.private_key)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        resp = await client.get(f"{config.api_base_url}/repos/{owner}/{repo}/installation", headers=headers)
        if resp.status_code != httpx.codes.OK:
            raise GitHubAuthError(
                f"no GitHub App installation for {owner}/{repo} "
                f"(is the App installed on it?): HTTP {resp.status_code} {resp.text[:200]}"
            )
        installation_id = int(resp.json()["id"])
        auth = GitHubAppAuth(config, http_client=client)
        return await auth.installation_token(installation_id)
    finally:
        if http_client is None:
            await client.aclose()


async def resolve_repo_token(
    owner: str, repo: str, *, http_client: httpx.AsyncClient | None = None
) -> str | None:
    """Resolve a git-usable token for ``owner/repo``: env PAT, else the App."""
    env_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if env_token:
        return env_token
    config = GitHubAppConfig()
    if config.api_configured:
        return await _app_installation_token(owner, repo, config, http_client=http_client)
    return None


async def authenticate_repo_url(
    url: str | None, *, http_client: httpx.AsyncClient | None = None
) -> str | None:
    """Return ``url`` with a token embedded, or unchanged if none is needed.

    Best-effort: a token-resolution failure is logged and the original URL is
    returned, so callers degrade to ambient credentials instead of hard-failing.
    """
    if not url:
        return url
    parsed = _parse_github_owner_repo(url)
    if parsed is None:
        return url  # not a plain https github URL — leave it alone
    if "@" in (urlsplit(url).netloc or ""):
        return url  # already carries credentials
    owner, repo = parsed
    try:
        token = await resolve_repo_token(owner, repo, http_client=http_client)
    except (GitHubAuthError, httpx.HTTPError) as exc:
        logger.warning("sdlc.gitauth.token_failed", extra={"error": str(exc)[:200]})
        return url
    if not token:
        return url
    return _inject_token(url, token)


__all__ = ["authenticate_repo_url", "resolve_repo_token"]
