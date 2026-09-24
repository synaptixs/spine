"""The LLM's input and the intake cache, pinned byte for byte.

Every cached spec was extracted from exactly the text `_build_user_message` produced for its
ticket. If that text moves — a reordered section, a changed cap, one extra newline — the next
extraction can produce a different spec, the plan built from it gets a different digest, and
every in-flight `autorun --source` parks (exit 6). So intake changes that are *meant* to leave
the LLM alone have to prove it, and this file is the proof: a ticket that exercises every
part of a Jira body (description, comments, links, attachments read, cut and only named), fetched
through the real adapters, rendered through the real prompt builder and cache serialiser, and
compared with golden files captured on 3.44.0.

Regenerate only for a change that is *meant* to move the LLM's input, and say so in the commit:

    SPINE_REGEN_GOLDEN=1 uv run --frozen pytest tests/intake/test_llm_input_pinned.py

The golden directory starts with a dot on purpose: `.txt` files are ingested as `Doc` nodes, and
both walkers skip dot-directories (CLAUDE.md, "fixture source inside this repo").
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestrator.intake.cache import _plan_to_dict
from orchestrator.intake.intents import IntentExtractor
from orchestrator.intake.jira import JiraConfig
from orchestrator.intake.jira_source import JiraSourceAdapter
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument

GOLDEN = Path(__file__).parent / ".golden"
_REGEN = os.environ.get("SPINE_REGEN_GOLDEN") == "1"

_HOST = "acme.atlassian.net"


def _adf(*paragraphs: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(paragraphs)}


def _para(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(nodes)}


def _text(text: str, *, href: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if href:
        node["marks"] = [{"type": "link", "attrs": {"href": href}}]
    return node


# A ticket that reaches every branch of `_issue_to_document`: a description with a Confluence link
# (as a link mark and as a smart card), three comments, a parent and a sideways link, and seven
# attachments — two long enough to be cut, one past the 20,000-character budget, an image, a type
# with no reader, one over the byte cap and one the server refuses.
_ISSUE: dict[str, Any] = {
    "summary": "Orders export drops the currency column",
    "issuetype": {"name": "Bug"},
    "status": {"name": "To Do"},
    "priority": {"name": "High"},
    "labels": ["export", "finance"],
    "description": _adf(
        _para(_text("The CSV export loses the currency column since 4.2.")),
        _para(
            _text("Mapping rules are in the "),
            _text("export spec", href=f"https://{_HOST}/wiki/spaces/FIN/pages/123456/Export+spec"),
            _text("."),
        ),
        {
            "type": "paragraph",
            "content": [{"type": "inlineCard", "attrs": {"url": f"https://{_HOST}/wiki/x/QAAB"}}],
        },
    ),
    "parent": {"key": "FIN-1", "fields": {"summary": "Finance exports"}},
    "issuelinks": [
        {
            "type": {"outward": "blocks", "inward": "is blocked by"},
            "outwardIssue": {"key": "FIN-9", "fields": {"summary": "Quarter close"}},
        }
    ],
    "comment": {
        "total": 3,
        "comments": [
            {
                "author": {"displayName": "Ana"},
                "created": "2026-09-01T10:00:00.000+0000",
                "body": _adf(_para(_text("Seen on EUR and GBP."))),
            },
            {
                "author": {"displayName": "Raj"},
                "created": "2026-09-02T10:00:00.000+0000",
                "body": _adf(_para(_text("x" * 1_500))),
            },
            {
                "author": {"displayName": "Ana"},
                "created": "2026-09-03T10:00:00.000+0000",
                "body": _adf(_para(_text("Must keep ISO 4217 codes."))),
            },
        ],
    },
    "attachment": [
        {
            "id": "a1",
            "filename": "mapping.md",
            "size": 200,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a1",
        },
        {
            "id": "a2",
            "filename": "export-log.txt",
            "size": 12_000,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a2",
        },
        {
            "id": "a3",
            "filename": "rules.md",
            "size": 30_000,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a3",
        },
        {
            "id": "a4",
            "filename": "screen.png",
            "size": 4_000,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a4",
        },
        {
            "id": "a5",
            "filename": "dump.bin",
            "size": 100,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a5",
        },
        {
            "id": "a6",
            "filename": "huge.txt",
            "size": 2_000_000,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a6",
        },
        {
            "id": "a7",
            "filename": "gone.txt",
            "size": 10,
            "content": f"https://{_HOST}/rest/api/3/attachment/content/a7",
        },
    ],
}
_CONTENT = {
    "a1": b"# Mapping\n\n- currency: ISO 4217 code, column 7\n",
    "a2": ("export row ok\n" * 900).encode(),
    "a3": ("- rule: amounts keep their currency\n" * 850).encode(),
}


def _jira_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/issue/FIN-42"):
        return httpx.Response(200, json={"id": "10042", "key": "FIN-42", "fields": _ISSUE})
    if "/attachment/content/" in path:
        body = _CONTENT.get(path.rsplit("/", 1)[1])
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404, content=b"")
    return httpx.Response(404, json={})


async def _rest_document() -> SourceDocument:
    http = httpx.AsyncClient(transport=httpx.MockTransport(_jira_handler), base_url=f"https://{_HOST}")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url=f"https://{_HOST}", email="e", api_token="t"), http_client=http
    )
    async with http:
        return await adapter.fetch_document("FIN-42")


def _llm_message(documents: list[SourceDocument]) -> str:
    # The real prompt builder; it reads no instance state, so no LLM client is needed.
    return IntentExtractor._build_user_message(IntentExtractor.__new__(IntentExtractor), documents)


def _cached(documents: list[SourceDocument]) -> str:
    return json.dumps(_plan_to_dict(BacklogPlan(documents=documents)), indent=2, sort_keys=True)


def _check(name: str, rendered: str) -> None:
    # Exactly one trailing newline on disk, so the end-of-file hook never rewrites a golden file;
    # the comparison stays exact because the newline is added here, not stripped from either side.
    actual = rendered + "\n"
    path = GOLDEN / name
    if _REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        pytest.skip(f"regenerated {path.name}")
    assert path.is_file(), f"missing golden {path} — regenerate with SPINE_REGEN_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{name} moved: the LLM's input or the intake cache changed"


async def test_the_llm_reads_a_rest_ticket_exactly_as_it_did_on_3_44() -> None:
    _check("rest-llm-message.txt", _llm_message([await _rest_document()]))


async def test_the_intake_cache_stores_a_rest_ticket_exactly_as_it_did_on_3_44() -> None:
    _check("rest-cache.json", _cached([await _rest_document()]))
