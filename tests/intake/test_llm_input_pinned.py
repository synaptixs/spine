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


def _over_budget_pages() -> list[SourceDocument]:
    return [
        SourceDocument(
            id=f"confluence:{i}",
            title=f"Linked page: Spec part {i}",
            body=f"Linked from FIN-42 (description): https://acme.atlassian.net/wiki/spaces/FIN/pages/{i}\n\n"
            + (f"- rule {i}: every amount keeps its currency\n" * 500),
            url=f"https://acme.atlassian.net/wiki/spaces/FIN/pages/{i}",
        )
        for i in range(1, 5)
    ]


async def test_an_over_budget_message_is_cut_exactly_as_it_was_on_3_48() -> None:
    """N14 review: the ticket above fits, so nothing here pinned the cut. This golden was rendered
    by the loop `_build_user_message` ran on 3.48.0 (`c6b91dfc`, from a worktree), before
    `fit_documents` replaced it: one page cut with `…[truncated]`, the rest left out."""
    _check("over-budget-llm-message.txt", _llm_message([await _rest_document(), *_over_budget_pages()]))


# ---- FIN-43: the attachments 3.44 never downloaded (N16c) ------------------------------------
#
# 3.44 downloaded as it went and stopped at the first bound; HEAD downloads every attachment for
# `full_body` and derives the bounded view afterwards (`_bound_attachments`). The two can only
# diverge where HEAD downloads something 3.44 never did — a sixth readable file, a failure past
# the five-file bound, a failure after the 20,000-char budget ran out — and FIN-42 reaches none of
# them. These goldens were rendered by v3.44.0's own adapter, prompt builder and cache serialiser
# (a throwaway worktree of the tag, the fixtures below imported into it), so they pin what 3.44
# said, not what this branch says.


def _att(key: str, filename: str, size: int) -> dict[str, Any]:
    return {
        "id": key,
        "filename": filename,
        "size": size,
        "content": f"https://{_HOST}/rest/api/3/attachment/content/{key}",
    }


def _note(i: int) -> bytes:
    return (f"# Note {i}\n\n" + f"- rule {i}\n" * 20).encode()


_FIN43_CASES: dict[str, tuple[list[dict[str, Any]], dict[str, bytes]]] = {
    # Seven readable files: 3.44 read five and named the last two "bound of 5 reached".
    "seven-readable": (
        [_att(f"r{i}", f"note{i}.md", 300) for i in range(1, 8)],
        {f"r{i}": _note(i) for i in range(1, 8)},
    ),
    # Past the bound: a 404, a file over the 1 MB cap and a readable one — 3.44 fetched none of
    # them and named all three for the bound; HEAD fetches all three.
    "failure-past-the-bound": (
        [_att(f"r{i}", f"note{i}.md", 300) for i in range(1, 6)]
        + [_att("f6", "gone.md", 300), _att("f7", "big.txt", 300), _att("r8", "late.md", 300)],
        {**{f"r{i}": _note(i) for i in range(1, 6)}, "f7": b"y" * 1_200_000, "r8": b"late\n"},
    ),
    # A 404 after the 20,000-char budget ran out: 3.44 named it for the budget, never fetched it.
    "failure-past-the-budget": (
        [
            _att("b1", "a.txt", 9_000),
            _att("b2", "b.txt", 9_000),
            _att("b3", "c.txt", 9_000),
            _att("f4", "gone.txt", 10),
        ],
        {"b1": b"a" * 9_000, "b2": b"b" * 9_000, "b3": b"c" * 9_000},
    ),
}


def _fin43_issue(attachments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": "Orders export drops the currency column",
        "issuetype": {"name": "Bug"},
        "status": {"name": "To Do"},
        "priority": {"name": "High"},
        "labels": ["export"],
        "description": _adf(_para(_text("The CSV export loses the currency column."))),
        "comment": {"total": 0, "comments": []},
        "attachment": attachments,
    }


async def _fin43_document(case: str) -> SourceDocument:
    attachments, content = _FIN43_CASES[case]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issue/FIN-43"):
            return httpx.Response(200, json={"id": "1", "key": "FIN-43", "fields": _fin43_issue(attachments)})
        if "/attachment/content/" in path:
            body = content.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, content=body) if body is not None else httpx.Response(404, content=b"")
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=f"https://{_HOST}")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url=f"https://{_HOST}", email="e", api_token="t"), http_client=http
    )
    async with http:
        return await adapter.fetch_document("FIN-43")


@pytest.mark.parametrize("case", sorted(_FIN43_CASES))
async def test_the_llm_reads_attachments_3_44_never_downloaded_exactly_as_it_did(case: str) -> None:
    _check(f"fin43-{case}-llm-message.txt", _llm_message([await _fin43_document(case)]))


@pytest.mark.parametrize("case", sorted(_FIN43_CASES))
async def test_the_intake_cache_stores_attachments_3_44_never_downloaded_exactly_as_it_did(case: str) -> None:
    _check(f"fin43-{case}-cache.json", _cached([await _fin43_document(case)]))


# ---- past the full-read bound (N15, SSPN-59) ------------------------------------------------------
# Two tickets with more than twenty readable attachments. The full read stops at twenty files; the
# extractor's bounded view is derived from it and must still read what 3.44.0 read, which had no
# such bound. Route A is the ticket's: four files leave 40 chars of the 20,000-char budget, sixteen
# more are each too long to fit even their cut marker, and the 21st is ten characters. Route C is
# the likelier one: a budget cut that lands on spaces is shortened by `rstrip`, leaving a few
# characters of budget, so every later readable file is named with 3.44.0's reason.
_PAST_THE_BOUND_ROUTES: dict[str, list[str]] = {
    "FIN-51": ["a" * 8_000, "b" * 8_000, "c" * 2_000, "d" * 1_960] + ["e" * 500] * 16 + ["tiny note!"],
    "FIN-52": ["a" * 9_000, "b" * 9_000, "w" * 3_900 + " " * 60 + "tail" * 2_000] + ["x" * 300] * 19,
}


async def _past_the_bound_document(key: str) -> SourceDocument:
    texts = _PAST_THE_BOUND_ROUTES[key]
    fields = {
        "summary": f"{len(texts)} attachments",
        "issuetype": {"name": "Story"},
        "status": {"name": "To Do"},
        "attachment": [
            {
                "id": str(i),
                "filename": f"f{i:02d}.txt",
                "size": len(text),
                "content": f"https://{_HOST}/rest/api/3/attachment/content/{i}",
            }
            for i, text in enumerate(texts)
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/issue/{key}"):
            return httpx.Response(200, json={"id": key, "key": key, "fields": fields})
        if "/attachment/content/" in path:
            return httpx.Response(200, content=texts[int(path.rsplit("/", 1)[1])].encode())
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=f"https://{_HOST}")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url=f"https://{_HOST}", email="e", api_token="t"), http_client=http
    )
    async with http:
        return await adapter.fetch_document(key)


async def _past_the_bound_documents() -> list[SourceDocument]:
    return [await _past_the_bound_document(key) for key in _PAST_THE_BOUND_ROUTES]


async def test_past_the_full_read_bound_the_llm_reads_exactly_what_3_44_did() -> None:
    """N15 (SSPN-59). This golden was rendered by 3.44.0 itself — the code in a `git worktree` of
    `v3.44.0` running this module's `_past_the_bound_documents` and `_llm_message` — never by the
    code under test. Never regenerate it with ``SPINE_REGEN_GOLDEN=1``: a golden the fixed code
    wrote proves only that the code agrees with itself."""
    _check("past-the-full-read-bound-llm-message.txt", _llm_message(await _past_the_bound_documents()))
