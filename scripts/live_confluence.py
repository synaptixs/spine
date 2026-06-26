#!/usr/bin/env python
"""Live read-only probe for Block B.1 (Confluence adapter) — no LLM needed.

Validates the half the Atlassian credentials unlock: real auth, our v2 API
paths, and storage-XHTML → text extraction on real page content.

Required env (or .env): CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL,
CONFLUENCE_API_TOKEN.

Usage:
    # Discover spaces + recent pages (so you can pick an id):
    uv run python scripts/live_confluence.py --discover
    # Fetch one page + its children:
    uv run python scripts/live_confluence.py --page 123456
    # Walk a small tree from a root page:
    uv run python scripts/live_confluence.py --page 123456 --tree --max-docs 5
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx


async def _discover(config: object) -> int:
    """List a few spaces + recent pages directly off the v2 API."""
    from orchestrator.intake.confluence import ConfluenceConfig

    assert isinstance(config, ConfluenceConfig)
    auth = (config.email, config.api_token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        spaces = await client.get(
            f"{config.api_base}/spaces",
            params={"limit": "10"},
            auth=auth,
            headers={"Accept": "application/json"},
        )
        if spaces.status_code != 200:
            print(f"spaces fetch failed: HTTP {spaces.status_code} {spaces.text[:200]}", file=sys.stderr)
            return 1
        print("Spaces:")
        for s in spaces.json().get("results", []):
            print(f"  - {s.get('key', '?')}  id={s.get('id')}  {s.get('name', '')}")

        pages = await client.get(
            f"{config.api_base}/pages",
            params={"limit": "10"},
            auth=auth,
            headers={"Accept": "application/json"},
        )
        if pages.status_code == 200:
            print("\nRecent pages (use an id with --page):")
            for p in pages.json().get("results", []):
                print(f"  - id={p.get('id')}  {p.get('title', '')}")
    return 0


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Live read-only Confluence probe (Block B.1).")
    parser.add_argument("--discover", action="store_true", help="List spaces + recent pages.")
    parser.add_argument("--page", help="Confluence page id to fetch.")
    parser.add_argument("--tree", action="store_true", help="Walk children from --page.")
    parser.add_argument("--max-docs", type=int, default=5)
    parser.add_argument("--max-depth", type=int, default=2)
    args = parser.parse_args()

    from orchestrator.intake.confluence import ConfluenceAdapter, ConfluenceConfig

    config = ConfluenceConfig()
    if not config.configured:
        print(
            "Confluence not configured: set CONFLUENCE_BASE_URL / EMAIL / API_TOKEN.",
            file=sys.stderr,
        )
        return 2

    if args.discover:
        return await _discover(config)

    if not args.page:
        print("Pass --page <id> (or --discover to find one).", file=sys.stderr)
        return 2

    adapter = ConfluenceAdapter(config)
    if args.tree:
        result = await adapter.fetch_tree(args.page, max_depth=args.max_depth, max_docs=args.max_docs)
        print(f"Fetched {len(result.documents)} document(s); truncated={result.truncated}\n")
        for d in result.documents:
            preview = d.body[:300].replace("\n", " ")
            print(f"## {d.title}  (id={d.id})")
            print(f"   url: {d.url}")
            print(f"   chars: {len(d.body)}  preview: {preview!r}\n")
    else:
        doc = await adapter.fetch_document(args.page)
        print(f"Title: {doc.title}")
        print(f"Id:    {doc.id}")
        print(f"URL:   {doc.url}")
        print(f"Space: {doc.space}")
        print(f"Body chars: {len(doc.body)}")
        print("\n--- extracted text (first 1500 chars) ---")
        print(doc.body[:1500])
        children = await adapter.list_children(args.page)
        print(f"\nChildren: {len(children)}")
        for c in children[:10]:
            print(f"  - id={c.id}  {c.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
