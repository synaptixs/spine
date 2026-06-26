#!/usr/bin/env python
"""Extract buildable intents from a Confluence page's children, write them to
a markdown file, and (optionally) upload them as a new child page.

Per-child extraction (one LLM call per child page) so large requirement
spaces don't get truncated into one capped prompt, and each intent keeps
its source-page attribution.

Safe by default: ``--dry-run`` (the default) writes the local .md and
prints what it WOULD upload, without touching Confluence. ``--upload``
creates the child page.

Required env (or .env): CONFLUENCE_BASE_URL/EMAIL/API_TOKEN, an LLM key
(OPENAI_API_KEY), and ORCHESTRATOR_INTAKE_MODEL (e.g. gpt-4o) if not using
the Anthropic default.

Usage:
    python scripts/intents_to_confluence.py --parent 3235774470 --out intents.md
    python scripts/intents_to_confluence.py --parent 3235774470 --out intents.md --upload
"""

from __future__ import annotations

import argparse
import asyncio
import html
import os
import sys
from pathlib import Path


def _md_for_intents(parent_title: str, grouped: list[tuple[str, list]]) -> str:
    lines = [
        f"# Buildable Intents — {parent_title}",
        "",
        "_Auto-extracted from child pages by the SDLC orchestrator (Block B). "
        "Each intent is a discrete, buildable capability; open questions need "
        "human resolution before the backlog is created._",
        "",
    ]
    total = sum(len(items) for _, items in grouped)
    lines += [f"**{total} intents across {len(grouped)} source pages.**", ""]
    for page_title, intents in grouped:
        if not intents:
            continue
        lines.append(f"## Source: {page_title}")
        lines.append("")
        for it in intents:
            lines.append(f"### {it.title}")
            if it.description:
                lines.append(it.description)
            if it.scope:
                lines.append(f"- **Scope:** {it.scope}")
            if it.dependencies:
                lines.append(f"- **Dependencies:** {', '.join(it.dependencies)}")
            if it.nfrs:
                lines.append(f"- **NFRs:** {', '.join(it.nfrs)}")
            if it.open_questions:
                lines.append("- **Open questions:**")
                lines += [f"  - {q}" for q in it.open_questions]
            lines.append("")
    return "\n".join(lines)


def _storage_for_intents(parent_title: str, grouped: list[tuple[str, list]]) -> str:
    """Render the intents as Confluence storage-format XHTML."""

    def esc(s: str) -> str:
        return html.escape(s)

    total = sum(len(items) for _, items in grouped)
    parts = [
        f"<h1>Buildable Intents — {esc(parent_title)}</h1>",
        "<p><em>Auto-extracted from child pages by the SDLC orchestrator "
        "(Block B). Each intent is a discrete, buildable capability; open "
        "questions need human resolution before the backlog is created.</em></p>",
        f"<p><strong>{total} intents across {len(grouped)} source pages.</strong></p>",
    ]
    for page_title, intents in grouped:
        if not intents:
            continue
        parts.append(f"<h2>Source: {esc(page_title)}</h2>")
        for it in intents:
            parts.append(f"<h3>{esc(it.title)}</h3>")
            if it.description:
                parts.append(f"<p>{esc(it.description)}</p>")
            bullets = []
            if it.scope:
                bullets.append(f"<li><strong>Scope:</strong> {esc(it.scope)}</li>")
            if it.dependencies:
                bullets.append(f"<li><strong>Dependencies:</strong> {esc(', '.join(it.dependencies))}</li>")
            if it.nfrs:
                bullets.append(f"<li><strong>NFRs:</strong> {esc(', '.join(it.nfrs))}</li>")
            if bullets:
                parts.append(f"<ul>{''.join(bullets)}</ul>")
            if it.open_questions:
                oq = "".join(f"<li>{esc(q)}</li>" for q in it.open_questions)
                parts.append(f"<p><strong>Open questions:</strong></p><ul>{oq}</ul>")
    return "".join(parts)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Extract intents from a Confluence page tree.")
    parser.add_argument("--parent", required=True, help="Parent Confluence page id (children are extracted).")
    parser.add_argument("--out", default="intents.md", help="Local markdown output path.")
    parser.add_argument("--title", default="", help="Title for the uploaded child page.")
    parser.add_argument("--upload", action="store_true", help="Create the child page (default: dry-run).")
    args = parser.parse_args()

    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient
    from orchestrator.intake.confluence import ConfluenceAdapter, ConfluenceConfig
    from orchestrator.intake.intents import IntentExtractor

    load_local_env()
    conf = ConfluenceConfig()
    if not conf.configured:
        print("Confluence not configured (CONFLUENCE_BASE_URL / EMAIL / API_TOKEN).", file=sys.stderr)
        return 2

    model = os.getenv("ORCHESTRATOR_INTAKE_MODEL")
    extractor = IntentExtractor(LiteLLMClient(), model=model) if model else IntentExtractor(LiteLLMClient())
    adapter = ConfluenceAdapter(conf)

    parent = await adapter.fetch_document(args.parent)
    children = await adapter.list_children(args.parent)
    print(f"Parent: {parent.title} (id={args.parent}) — {len(children)} children", file=sys.stderr)

    grouped: list[tuple[str, list]] = []
    seen_ids: set[str] = set()
    for ref in children:
        doc = await adapter.fetch_document(ref.id)
        if doc.is_empty:
            print(f"  - skip (empty): {doc.title}", file=sys.stderr)
            continue
        intents = await extractor.extract([doc])
        # global-dedup intent ids across pages
        for it in intents:
            base = it.id
            n = 1
            while it.id in seen_ids:
                it.id = f"{base}-{n}"
                n += 1
            seen_ids.add(it.id)
        print(f"  - {doc.title}: {len(intents)} intents", file=sys.stderr)
        grouped.append((doc.title, intents))

    total = sum(len(i) for _, i in grouped)
    md = _md_for_intents(parent.title, grouped)
    Path(args.out).write_text(md, encoding="utf-8")
    print(f"\nWrote {total} intents to {args.out}", file=sys.stderr)

    title = args.title or f"Buildable Intents — {parent.title}"
    if not args.upload:
        print(f"\nDry-run: would create child page {title!r} under {args.parent}.", file=sys.stderr)
        print("Re-run with --upload to create it.", file=sys.stderr)
        return 0

    storage = _storage_for_intents(parent.title, grouped)
    created = await adapter.create_page(
        space_id=parent.space, title=title, body_storage=storage, parent_id=args.parent
    )
    print(f"\nCreated child page: {created.url} (id={created.id})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
