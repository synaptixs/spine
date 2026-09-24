"""Read the Confluence pages a ticket links to — `--follow-links` (Track E, E2).

Opt-in, because whatever intake reads becomes text an approval is checked against and text the
spec is derived from: reading more by default would move plans nobody asked to move. With the
flag, the pages :mod:`confluence_links` finds on the *root* ticket are read and appended to the
fetched documents — after the ticket's own, so its words keep their place ahead of any page's —
where they reach both `source.txt` (§8) and the intent extractor, within its existing cap.

**Bounded, and said so (invariant 7).** Direct links only: pages those pages link to are someone
else's context. At most :data:`MAX_LINKED_PAGES`, in discovery order (remote links first); every
page past the bound, every link with no usable page id, and every page that exists but cannot be
read is named with why.

**No Confluence access is a refusal, not a warning.** Asked to follow links, a run that silently
could not would produce a plan that looks complete and is not. So the reader is resolved *before*
anything is fetched, and its absence raises :class:`~orchestrator.intake.factory.IntakeNotConfiguredError`,
which every caller already turns into an error that says what to configure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from orchestrator.intake.source import SourceDocument

#: Direct links read per ticket.
MAX_LINKED_PAGES = 5


class PageReader(Protocol):
    """All `--follow-links` needs of a Confluence source: read one page by id."""

    async def fetch_document(self, doc_id: str) -> SourceDocument: ...


@dataclass
class FollowReport:
    """What `--follow-links` read and what it could not, for the build document's header."""

    documents: list[SourceDocument] = field(default_factory=list)
    not_read: list[tuple[str, str]] = field(default_factory=list)  # (url, why)
    unsupported: str = ""  # set when the source is not one whose links can be followed

    def summary(self) -> str:
        """``followed — 2 read, 1 not read (<url>: <why>)`` — or why nothing was followed."""
        if self.unsupported:
            return f"not followed — {self.unsupported}"
        read = len(self.documents)
        if not read and not self.not_read:
            return "followed — the ticket links no Confluence pages"
        text = f"followed — {read} read"
        if self.not_read:
            reasons = "; ".join(f"{url}: {why}" for url, why in self.not_read)
            text += f", {len(self.not_read)} not read ({reasons})"
        return text


def _default_reader() -> PageReader:
    from orchestrator.intake.factory import build_confluence_source

    return build_confluence_source()


async def follow_confluence_links(
    source: Any,
    root_id: str,
    *,
    reader_factory: Callable[[], PageReader] | None = None,
) -> FollowReport:
    """The linked pages of ``root_id``, read through the configured Confluence source.

    ``source`` is the ticket's own adapter; it must offer ``linked_pages(root_id)`` — Jira's do,
    over REST and over MCP. Any other source has no links to follow, which is reported rather
    than raised: the flag asked for something the source cannot hold.
    """
    finder = getattr(source, "linked_pages", None)
    if finder is None:
        return FollowReport(unsupported="links are followed only for Jira tickets")
    # Resolved first: no Confluence access must stop the run before anything is fetched.
    reader = (reader_factory or _default_reader)()
    linked = await finder(root_id)

    report = FollowReport()
    for page in linked.pages[:MAX_LINKED_PAGES]:
        try:
            doc = await reader.fetch_document(page.page_id)
        except Exception as exc:  # noqa: BLE001 — any failure names the page; none stops the run
            report.not_read.append(
                (page.url or f"page {page.page_id}", f"could not be read ({type(exc).__name__})")
            )
            continue
        report.documents.append(
            SourceDocument(
                id=f"confluence:{page.page_id}",
                title=f"Linked page: {doc.title}",
                body=f"Linked from {root_id} ({page.via}): {page.url}\n\n{doc.body}".strip(),
                url=doc.url or page.url,
                space=doc.space,
            )
        )
    for page in linked.pages[MAX_LINKED_PAGES:]:
        report.not_read.append(
            (page.url or f"page {page.page_id}", f"bound of {MAX_LINKED_PAGES} pages reached")
        )
    for unresolved in linked.unresolved:
        report.not_read.append((unresolved.url, unresolved.reason))
    return report


__all__ = ["MAX_LINKED_PAGES", "FollowReport", "PageReader", "follow_confluence_links"]
