#!/usr/bin/env python
"""Verify GitHub App setup — read-only, no writes.

Confirms the App ID + private key sign a valid JWT and that GitHub accepts
it, then lists the App's installations (with their ids + the repos each can
access). Run this after creating + installing the App, before pointing
``live_review.py`` at a PR.

Required env (or .env): GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY (or _PATH).

Usage:
    uv run python scripts/live_github_auth.py
"""

from __future__ import annotations

import asyncio
import sys


async def _main() -> int:
    from orchestrator.codereview.auth import build_app_jwt
    from orchestrator.codereview.config import GitHubAppConfig
    from orchestrator.core.env import load_local_env

    load_local_env()
    config = GitHubAppConfig()
    if not config.api_configured:
        print(
            "GitHub App not configured: set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY (or _PATH).",
            file=sys.stderr,
        )
        return 2

    import httpx

    jwt = build_app_jwt(config.app_id, config.private_key)
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        app = await client.get(f"{config.api_base_url}/app", headers=headers)
        if app.status_code != 200:
            print(
                f"App auth failed: HTTP {app.status_code} {app.text[:200]}\n"
                "→ check GITHUB_APP_ID and that the private key matches this App.",
                file=sys.stderr,
            )
            return 1
        print(f"Authenticated as App: {app.json().get('slug')!r} (id={config.app_id})\n")

        insts = await client.get(
            f"{config.api_base_url}/app/installations", headers=headers, params={"per_page": "100"}
        )
        if insts.status_code != 200:
            print(
                f"Listing installations failed: HTTP {insts.status_code} {insts.text[:200]}",
                file=sys.stderr,
            )
            return 1
        installations = insts.json()
        if not installations:
            print("No installations yet — install the App on a repo (App page → Install App).")
            return 0
        print("Installations (use --installation <id> with live_review.py):")
        for inst in installations:
            account = (inst.get("account") or {}).get("login", "?")
            print(
                f"  - installation id={inst.get('id')}  account={account}  "
                f"repos={inst.get('repository_selection')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
