"""The Confluence pages a Jira ticket links to — found, never fetched.

Pure functions over what the ticket already carries: its remote links (Jira's
``/issue/{key}/remotelink``), its description (ADF on Cloud v3, wiki markup elsewhere) and its
comments. No I/O; `sdlc plan --follow-links` decides what to fetch from the result.

**Two ways in, because authors link both ways.** A page added through Jira's "Confluence content"
link arrives as a remote link whose ``globalId`` carries the page id outright — reliable, but only
for links made that way. A URL pasted into the description or a comment arrives only as text.
Remote links are read first, then the text; the same page found twice is one page.

**Only the site's own URLs.** A scanned URL counts only when its host is one of the Atlassian
site's (Jira's or Confluence's base URL). A bare ``/wiki/`` path would otherwise make
``en.wikipedia.org/wiki/Currency`` a "Confluence link". The same holds for a remote link Jira types
``com.atlassian.confluence``: a page id is only meaningful on the site that issued it, and page
``123`` on a partner's Confluence read from this one is a *different page*. So one whose URL is on
another host is named, not read.

**What resolves, and what is only named.**
- ``…/pages/<id>/…`` and ``…viewpage.action?pageId=<id>`` carry the id.
- ``/x/<code>`` tiny links are decoded offline — the page id, little-endian, base64 with ``/`` →
  ``-`` and ``+`` → ``_``, padding and trailing ``A`` stripped. Atlassian calls that algorithm
  "not officially supported", so a decoded id is kept only if re-encoding it reproduces the code
  exactly; anything else is :class:`Unresolved`, never a wrong page.
- ``/display/SPACE/Title`` carries no id. Resolving it by title can land on the wrong page after
  a rename, so it is named, not read.
"""

from __future__ import annotations

import base64
import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

CONFLUENCE_APPLICATION = "com.atlassian.confluence"

_PAGE_PATH = re.compile(r"/pages/(\d+)(?:/|$)")
_TINY_PATH = re.compile(r"(?:^|/)x/([A-Za-z0-9_-]+)/?$")
_DISPLAY_PATH = re.compile(r"(?:^|/)display/[^/]+/[^/]+")
_GLOBAL_PAGE_ID = re.compile(r"(?:^|&)pageId=(\d+)(?:&|$)")
# A URL in running text or wiki markup — stops at whitespace and at the markup's own delimiters.
_URL = re.compile(r"https?://[^\s\]\[|\"'<>)]+")


@dataclass(frozen=True)
class PageRef:
    """A linked page whose id is known. ``via`` says where it was found."""

    page_id: str
    url: str
    via: str


@dataclass(frozen=True)
class Unresolved:
    """A link that is to Confluence but names no page id Spine can trust — named, never read."""

    url: str
    reason: str


@dataclass
class LinkedPages:
    pages: list[PageRef] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)


def encode_tiny(page_id: int) -> str:
    """The ``/x/<code>`` of a page id — the inverse :func:`decode_tiny` is checked against."""
    raw = base64.b64encode(struct.pack("<Q", page_id)).decode("ascii")
    return raw.replace("/", "-").replace("+", "_").rstrip("=").rstrip("A")


def decode_tiny(code: str) -> int | None:
    """The page id behind a tiny-link code, or ``None`` unless re-encoding reproduces ``code``."""
    if not code or len(code) > 11 or not re.fullmatch(r"[A-Za-z0-9_-]+", code):
        return None
    padded = code.replace("-", "/").replace("_", "+").ljust(11, "A") + "="
    try:
        page_id = struct.unpack("<Q", base64.b64decode(padded, validate=True))[0]
    except (ValueError, struct.error):
        return None
    return page_id if page_id > 0 and encode_tiny(page_id) == code else None


def resolve_page_url(url: str) -> str | Unresolved | None:
    """A Confluence URL's page id; :class:`Unresolved` when it is Confluence but has no usable id;
    ``None`` when it is not a Confluence page URL at all."""
    parsed = urlparse(url)
    path = parsed.path
    found = _PAGE_PATH.search(path)
    if found:
        return found.group(1)
    if path.endswith("viewpage.action"):
        ids = parse_qs(parsed.query).get("pageId") or []
        if ids and ids[0].isdigit():
            return ids[0]
        return Unresolved(url, "viewpage link without a page id")
    tiny = _TINY_PATH.search(path)
    if tiny:
        page_id = decode_tiny(tiny.group(1))
        if page_id is None:
            return Unresolved(url, "tiny link that does not decode to a page id")
        return str(page_id)
    if _DISPLAY_PATH.search(path):
        return Unresolved(url, "title URL, no page id")
    return None


def urls_in_adf(node: Any) -> list[str]:
    """Every link target in an Atlassian Document Format tree: ``link`` marks and smart cards."""
    found: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for child in n:
                walk(child)
            return
        if not isinstance(n, dict):
            return
        if n.get("type") in {"inlineCard", "blockCard", "embedCard"}:
            url = (n.get("attrs") or {}).get("url")
            if isinstance(url, str):
                found.append(url)
        for mark in n.get("marks") or []:
            if isinstance(mark, dict) and mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href")
                if isinstance(href, str):
                    found.append(href)
        walk(n.get("content"))

    walk(node)
    return found


def urls_in_text(text: str) -> list[str]:
    """URLs in plain text or Jira wiki markup (``[label|url]``, ``[url]``, bare)."""
    return [m.group(0).rstrip(".,;:") for m in _URL.finditer(text or "")]


def urls_in_field(value: Any) -> list[str]:
    """A description or comment body, whichever format it arrived in."""
    if isinstance(value, dict | list):
        return urls_in_adf(value)
    if isinstance(value, str):
        return urls_in_text(value)
    return []


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def find_linked_pages(
    *,
    remote_links: Sequence[Any] = (),
    texts: Iterable[tuple[str, Any]] = (),
    site_hosts: Iterable[str] = (),
) -> LinkedPages:
    """Every Confluence page a ticket links to, remote links first, de-duplicated by page id.

    ``texts`` is ``(where, field)`` pairs — ``("description", …)``, ``("comment", …)`` — each field
    ADF, wiki markup or plain text. ``site_hosts`` are the Atlassian site's hosts; a scanned URL on
    any other host is not a Confluence link and is ignored.
    """
    hosts = {h.lower() for h in site_hosts if h}
    out = LinkedPages()
    seen_ids: set[str] = set()
    seen_unresolved: set[str] = set()

    def add(resolved: str | Unresolved | None, url: str, via: str) -> None:
        if resolved is None:
            return
        if isinstance(resolved, Unresolved):
            if resolved.url not in seen_unresolved:
                seen_unresolved.add(resolved.url)
                out.unresolved.append(resolved)
            return
        if resolved not in seen_ids:
            seen_ids.add(resolved)
            out.pages.append(PageRef(page_id=resolved, url=url, via=via))

    for link in remote_links:
        if not isinstance(link, dict):
            continue
        obj = link.get("object") or {}
        url = str(obj.get("url") or "")
        application = str((link.get("application") or {}).get("type") or "")
        if application == CONFLUENCE_APPLICATION:
            if url and _host(url) not in hosts:
                add(Unresolved(url, f"on another Confluence site ({_host(url)})"), url, "remote link")
                continue
            found = _GLOBAL_PAGE_ID.search(str(link.get("globalId") or ""))
            if found:
                add(found.group(1), url, "remote link")
            else:
                add(
                    resolve_page_url(url) or Unresolved(url, "Confluence link without a page id"),
                    url,
                    "remote link",
                )
        elif url and _host(url) in hosts:
            add(resolve_page_url(url), url, "remote link")

    for where, value in texts:
        for url in urls_in_field(value):
            if _host(url) in hosts:
                add(resolve_page_url(url), url, where)
    return out


__all__ = [
    "CONFLUENCE_APPLICATION",
    "LinkedPages",
    "PageRef",
    "Unresolved",
    "decode_tiny",
    "encode_tiny",
    "find_linked_pages",
    "resolve_page_url",
    "urls_in_adf",
    "urls_in_field",
    "urls_in_text",
]
