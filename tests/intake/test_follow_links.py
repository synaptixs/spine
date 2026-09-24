"""`--follow-links`: reading the Confluence pages a ticket links to (Track E, E2). No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestrator.intake.cache import (
    FOLLOW_LINKS,
    analyze_cached,
    cache_path,
    complete_by_pr,
    load_cached_plan,
    load_progress,
    save_plan,
    set_progress,
)
from orchestrator.intake.confluence_links import LinkedPages, PageRef, Unresolved, encode_tiny
from orchestrator.intake.factory import IntakeNotConfiguredError
from orchestrator.intake.follow_links import MAX_LINKED_PAGES, FollowReport, follow_confluence_links
from orchestrator.intake.intents import Intent
from orchestrator.intake.jira import JiraConfig
from orchestrator.intake.jira_source import JiraSourceAdapter
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import FetchTreeResult, SourceDocument
from orchestrator.intake.specs import FeatureSpec

_SITE = "acme.atlassian.net"


class _Ticket:
    """The ticket's own adapter: offers `linked_pages`, as Jira's do."""

    def __init__(self, linked: LinkedPages) -> None:
        self.linked, self.asked = linked, 0

    async def linked_pages(self, doc_id: str) -> LinkedPages:
        self.asked += 1
        return self.linked


class _Wiki:
    """The Confluence reader. Page ids in ``broken`` fail the way a 403 would."""

    source_kind = "confluence"

    def __init__(self, broken: set[str] | None = None) -> None:
        self.broken = broken or set()
        self.read: list[str] = []

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        self.read.append(doc_id)
        if doc_id in self.broken:
            raise RuntimeError("HTTP 403")
        return SourceDocument(id=doc_id, title=f"Page {doc_id}", body=f"requirement from page {doc_id}")


def _page(i: int) -> PageRef:
    return PageRef(page_id=str(i), url=f"https://{_SITE}/wiki/spaces/X/pages/{i}", via="description")


async def test_direct_links_are_read_up_to_the_bound_and_every_other_one_is_named() -> None:
    linked = LinkedPages(
        pages=[_page(i) for i in range(1, MAX_LINKED_PAGES + 3)],
        unresolved=[Unresolved(f"https://{_SITE}/wiki/display/X/Old", "title URL, no page id")],
    )
    wiki = _Wiki(broken={"2"})
    report = await follow_confluence_links(_Ticket(linked), "FIN-42", reader_factory=lambda: wiki)

    assert wiki.read == [str(i) for i in range(1, MAX_LINKED_PAGES + 1)]  # never past the bound
    assert [d.id for d in report.documents] == [
        "confluence:1",
        "confluence:3",
        "confluence:4",
        "confluence:5",
    ]
    assert report.documents[0].title == "Linked page: Page 1"
    assert "Linked from FIN-42 (description)" in report.documents[0].body
    reasons = [why for _, why in report.not_read]
    assert reasons == [
        "could not be read (RuntimeError)",
        f"bound of {MAX_LINKED_PAGES} pages reached",
        f"bound of {MAX_LINKED_PAGES} pages reached",
        "title URL, no page id",
    ]
    assert report.summary().startswith("followed — 4 read, 4 not read (")


async def test_no_confluence_access_refuses_before_anything_is_fetched() -> None:
    """D12: an explicit opt-in that silently could not happen yields a plan that looks complete."""
    ticket = _Ticket(LinkedPages(pages=[_page(1)]))

    def _unconfigured() -> Any:
        raise IntakeNotConfiguredError("Confluence not configured")

    with pytest.raises(IntakeNotConfiguredError):
        await follow_confluence_links(ticket, "FIN-42", reader_factory=_unconfigured)
    assert ticket.asked == 0


async def test_a_source_with_no_links_to_follow_says_so() -> None:
    class _File:
        source_kind = "file"

    report = await follow_confluence_links(_File(), "./bug.md", reader_factory=_Wiki)
    assert (
        report.documents == []
        and report.summary() == "not followed — links are followed only for Jira tickets"
    )


def test_a_ticket_with_no_links_says_that_too() -> None:
    assert FollowReport().summary() == "followed — the ticket links no Confluence pages"


# ---- the Jira adapter finds them -----------------------------------------------------------


async def test_the_jira_adapter_finds_links_in_remote_links_and_the_raw_description() -> None:
    description = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "spec",
                        "marks": [
                            {"type": "link", "attrs": {"href": f"https://{_SITE}/wiki/x/{encode_tiny(77)}"}}
                        ],
                    }
                ],
            }
        ],
    }
    comment = {"comments": [{"body": f"Old page: https://{_SITE}/wiki/display/FIN/Old"}]}
    remote = [
        {
            "globalId": "appId=a&pageId=123",
            "application": {"type": "com.atlassian.confluence"},
            "object": {"url": f"https://{_SITE}/wiki/pages/viewpage.action?pageId=123"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issue/FIN-42/remotelink"):
            return httpx.Response(200, json=remote)
        if request.url.path.endswith("/issue/FIN-42"):
            assert request.url.params.get("fields") == "description,comment"
            return httpx.Response(
                200, json={"key": "FIN-42", "fields": {"description": description, "comment": comment}}
            )
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=f"https://{_SITE}")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url=f"https://{_SITE}", email="e", api_token="t"), http_client=http
    )
    async with http:
        linked = await adapter.linked_pages("FIN-42")

    assert [(p.page_id, p.via) for p in linked.pages] == [("123", "remote link"), ("77", "description")]
    assert [u.reason for u in linked.unresolved] == ["title URL, no page id"]


async def test_remote_links_jira_will_not_return_leave_the_text_scan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/remotelink"):
            return httpx.Response(403, json={"errorMessages": ["no"]})
        text = f"see https://{_SITE}/wiki/spaces/X/pages/9/Spec"
        return httpx.Response(200, json={"key": "K-1", "fields": {"description": text}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=f"https://{_SITE}")
    adapter = JiraSourceAdapter(
        JiraConfig(base_url=f"https://{_SITE}", email="e", api_token="t"), http_client=http
    )
    async with http:
        linked = await adapter.linked_pages("K-1")
    assert [p.page_id for p in linked.pages] == ["9"]


# ---- the cache keeps the two analyses apart --------------------------------------------------

_SOURCE = "jira://FIN-42"


def _plan(title: str) -> BacklogPlan:
    return BacklogPlan(
        documents=[SourceDocument(id="FIN-42", title=title, body=title)],
        intents=[Intent(id="intent-x", title=title, description="d", acceptance_criteria=["c"])],
        specs=[FeatureSpec(intent_id="intent-x", title=title, acceptance_criteria=["c"])],
    )


class _Analyser:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def analyze(self, root_id: str, *, follow_links: bool = False) -> BacklogPlan:
        self.calls.append(follow_links)
        return _plan("with linked pages" if follow_links else "ticket only")


async def test_following_links_is_its_own_cache_entry_and_never_the_flag_off_one(tmp_path: Path) -> None:
    svc = _Analyser()
    plain = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path)  # type: ignore[arg-type]
    followed = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path, follow_links=True)  # type: ignore[arg-type]
    again_plain = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path)  # type: ignore[arg-type]
    again_followed = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path, follow_links=True)  # type: ignore[arg-type]

    assert svc.calls == [False, True]  # each extracted once, then served from its own entry
    assert plain.specs[0].title == again_plain.specs[0].title == "ticket only"
    assert followed.specs[0].title == again_followed.specs[0].title == "with linked pages"
    assert list(tmp_path.glob("*.json")) == [cache_path(_SOURCE, tmp_path)]  # one ticket, one file


def test_progress_is_one_record_per_ticket_whichever_entry_it_was_planned_from(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan("with linked pages"), tmp_path, variant=FOLLOW_LINKS)  # flag-only ticket
    set_progress(_SOURCE, "intent-x", status="in_progress", pr_url="https://x/pr/1", cache_dir=tmp_path)
    assert load_progress(_SOURCE, tmp_path) == {
        "intent-x": {"status": "in_progress", "pr_url": "https://x/pr/1"}
    }

    # A later flag-off analysis keeps both the progress and the variant.
    save_plan(_SOURCE, _plan("ticket only"), tmp_path)
    assert load_progress(_SOURCE, tmp_path)["intent-x"]["status"] == "in_progress"
    variant = load_cached_plan(_SOURCE, tmp_path, variant=FOLLOW_LINKS)
    assert variant is not None and variant.specs[0].title == "with linked pages"


def test_completing_a_pr_finds_a_ticket_only_ever_planned_with_links(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan("with linked pages"), tmp_path, variant=FOLLOW_LINKS)
    set_progress(_SOURCE, "intent-x", status="in_progress", pr_url="https://x/pr/7", cache_dir=tmp_path)
    matched = complete_by_pr("https://x/pr/7", cache_dir=tmp_path)
    assert matched is not None
    source, plan = matched
    assert source == _SOURCE and plan.specs[0].title == "with linked pages"
    assert load_progress(_SOURCE, tmp_path)["intent-x"]["status"] == "done"


def test_a_flag_off_read_never_sees_the_variant(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan("with linked pages"), tmp_path, variant=FOLLOW_LINKS)
    assert load_cached_plan(_SOURCE, tmp_path) is None


# ---- the service appends them --------------------------------------------------------------


async def test_the_service_appends_linked_pages_after_the_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.intake.service import BacklogService

    class _Source(_Ticket):
        source_kind = "jira"

        async def fetch_tree(self, root_id: str, **_k: Any) -> FetchTreeResult:
            return FetchTreeResult(documents=[SourceDocument(id=root_id, title="T", body="ticket")])

    monkeypatch.setattr("orchestrator.intake.follow_links._default_reader", _Wiki)
    service = BacklogService.__new__(BacklogService)
    service._source = _Source(LinkedPages(pages=[_page(5)]))  # type: ignore[assignment]

    plain = await service.fetch_source_documents("FIN-42")
    assert [d.id for d in plain.documents] == ["FIN-42"] and plain.linked_pages == ""

    followed = await service.fetch_source_documents("FIN-42", follow_links=True)
    assert [d.id for d in followed.documents] == ["FIN-42", "confluence:5"]
    assert followed.linked_pages == "followed — 1 read"


def test_a_flag_off_plan_never_prunes_progress_a_variant_still_holds(tmp_path: Path) -> None:
    """Review finding 1: intent ids come from the LLM's titles, so the plan with linked pages can
    name its intent differently from the plan without. A flag-off save after a flag-on run kept
    only its own ids — the PR recorded for the variant's intent vanished, and `complete_by_pr`
    could no longer find it."""
    followed = _plan("with linked pages")
    followed.intents[0] = followed.intents[0].model_copy(update={"id": "intent-with-links"})
    save_plan(_SOURCE, followed, tmp_path, variant=FOLLOW_LINKS)
    set_progress(
        _SOURCE, "intent-with-links", status="in_progress", pr_url="https://x/pr/2", cache_dir=tmp_path
    )

    save_plan(_SOURCE, _plan("ticket only"), tmp_path)  # a later plain `sdlc plan`, id "intent-x"

    assert load_progress(_SOURCE, tmp_path)["intent-with-links"]["pr_url"] == "https://x/pr/2"
    assert complete_by_pr("https://x/pr/2", cache_dir=tmp_path) is not None
