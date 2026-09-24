"""Finding the Confluence pages a Jira ticket links to (Track E, E2). Pure — no network."""

from __future__ import annotations

import struct
from base64 import b64encode

import pytest

from orchestrator.intake.confluence_links import (
    CONFLUENCE_APPLICATION,
    Unresolved,
    decode_tiny,
    encode_tiny,
    find_linked_pages,
    resolve_page_url,
    urls_in_adf,
    urls_in_text,
)

_SITE = "acme.atlassian.net"


@pytest.mark.parametrize("page_id", [1, 255, 65_536, 123_456, 2_147_483_647, 98_765_432_101])
def test_a_tiny_link_round_trips(page_id: int) -> None:
    assert decode_tiny(encode_tiny(page_id)) == page_id


def test_the_tiny_encoding_is_atlassians() -> None:
    """Independent of `encode_tiny`: little-endian bytes, base64, `/`→`-`, `+`→`_`, the padding
    and trailing `A` dropped (Atlassian KB, "programmatically generate the tiny link")."""
    page_id = 123_456
    expected = b64encode(struct.pack("<Q", page_id)).decode().replace("/", "-").replace("+", "_")
    assert encode_tiny(page_id) == expected.rstrip("=").rstrip("A")


@pytest.mark.parametrize("code", ["", "!!", "A" * 12, "QOIBA", "AAAA"])
def test_a_code_that_does_not_round_trip_is_never_a_page(code: str) -> None:
    """A trailing `A` the encoder would have stripped, an empty or over-long code, or one that
    decodes to page 0: each would give *some* id, and a wrong page is worse than none."""
    assert decode_tiny(code) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://{_SITE}/wiki/spaces/FIN/pages/123456/Export+spec", "123456"),
        (f"https://{_SITE}/wiki/spaces/FIN/pages/123456", "123456"),
        (f"https://{_SITE}/wiki/pages/viewpage.action?pageId=777", "777"),
        (f"https://{_SITE}/wiki/x/{encode_tiny(4242)}", "4242"),
        ("https://confluence.acme.io/pages/viewpage.action?pageId=9&src=jira", "9"),
    ],
)
def test_a_url_that_carries_a_page_id_resolves_to_it(url: str, expected: str) -> None:
    assert resolve_page_url(url) == expected


def test_a_title_url_is_named_not_resolved() -> None:
    """Resolving `/display/SPACE/Title` by title can land on another page after a rename."""
    got = resolve_page_url("https://confluence.acme.io/display/FIN/Export+spec")
    assert got == Unresolved("https://confluence.acme.io/display/FIN/Export+spec", "title URL, no page id")


def test_a_corrupted_tiny_link_is_named_not_guessed() -> None:
    got = resolve_page_url(f"https://{_SITE}/wiki/x/QOIBA")
    assert isinstance(got, Unresolved) and "does not decode" in got.reason


def test_a_url_that_is_not_a_confluence_page_is_ignored() -> None:
    assert resolve_page_url(f"https://{_SITE}/browse/FIN-9") is None


def test_urls_come_from_link_marks_and_every_kind_of_card() -> None:
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "spec",
                        "marks": [{"type": "link", "attrs": {"href": "https://a/1"}}],
                    }
                ],
            },
            {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": "https://a/2"}}]},
            {"type": "blockCard", "attrs": {"url": "https://a/3"}},
            {"type": "embedCard", "attrs": {"url": "https://a/4", "layout": "wide"}},
        ],
    }
    assert urls_in_adf(adf) == ["https://a/1", "https://a/2", "https://a/3", "https://a/4"]


def test_urls_come_from_wiki_markup_and_bare_text() -> None:
    text = "See [the spec|https://a/1] and [https://a/2], or https://a/3."
    assert urls_in_text(text) == ["https://a/1", "https://a/2", "https://a/3"]


def test_remote_links_come_first_and_one_page_found_twice_is_one_page() -> None:
    remote = [
        {
            "globalId": "appId=abc-123&pageId=123456",
            "application": {"type": CONFLUENCE_APPLICATION, "name": "System Confluence"},
            "object": {
                "url": f"https://{_SITE}/wiki/pages/viewpage.action?pageId=123456",
                "title": "Export spec",
            },
        }
    ]
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
                            {
                                "type": "link",
                                "attrs": {
                                    "href": f"https://{_SITE}/wiki/spaces/FIN/pages/123456/Export+spec"
                                },
                            }
                        ],
                    }
                ],
            },
            {
                "type": "paragraph",
                "content": [
                    {"type": "inlineCard", "attrs": {"url": f"https://{_SITE}/wiki/x/{encode_tiny(999)}"}}
                ],
            },
        ],
    }
    found = find_linked_pages(
        remote_links=remote,
        texts=[("description", description), ("comment", f"Also https://{_SITE}/wiki/display/FIN/Old+title")],
        site_hosts=[_SITE],
    )
    assert [(p.page_id, p.via) for p in found.pages] == [("123456", "remote link"), ("999", "description")]
    assert [u.reason for u in found.unresolved] == ["title URL, no page id"]


def test_a_wiki_path_on_another_site_is_not_a_confluence_link() -> None:
    found = find_linked_pages(
        texts=[("description", "Background: https://en.wikipedia.org/wiki/Currency")], site_hosts=[_SITE]
    )
    assert found.pages == [] and found.unresolved == []


def test_a_confluence_page_on_another_site_is_named_not_read_from_ours() -> None:
    """Review finding 5: a page id means something only on the site that issued it — page 55 on a
    partner's Confluence, read from ours, is a different page."""
    remote = [
        {
            "globalId": "appId=x&pageId=55",
            "application": {"type": CONFLUENCE_APPLICATION},
            "object": {"url": "https://wiki.partner.example/pages/viewpage.action?pageId=55"},
        },
        {
            "globalId": "appId=y&pageId=56",
            "application": {"type": CONFLUENCE_APPLICATION},
            "object": {"url": f"https://{_SITE}/wiki/pages/viewpage.action?pageId=56"},
        },
        {"application": {"type": "com.github"}, "object": {"url": "https://github.com/acme/app/pull/1"}},
    ]
    found = find_linked_pages(remote_links=remote, site_hosts=[_SITE])
    assert [p.page_id for p in found.pages] == ["56"]
    assert [u.reason for u in found.unresolved] == ["on another Confluence site (wiki.partner.example)"]
