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

**Read is not seen (N14).** A page read is not a page the model saw: the intent extractor cuts its
input at a fixed budget, and linked pages come last. So the summary can take the extraction's
:class:`~orchestrator.intake.intents.PromptFit` and say how many pages reached the spec whole, cut,
or not at all — and, because `sdlc plan` reads the ticket fresh while the spec comes from the
cache, which pages were linked since the spec was extracted, or are in it but no longer linked.

**No Confluence access is a refusal, not a warning.** Asked to follow links, a run that silently
could not would produce a plan that looks complete and is not. So the reader is resolved *before*
anything is fetched, and its absence raises :class:`~orchestrator.intake.factory.IntakeNotConfiguredError`,
which every caller already turns into an error that says what to configure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from orchestrator.intake.source import SourceDocument

if TYPE_CHECKING:
    from orchestrator.intake.intents import DocumentFit, PromptFit

#: Direct links read per ticket.
MAX_LINKED_PAGES = 5
#: Every linked page's title starts with this, so a cached extraction can tell its pages apart.
LINKED_TITLE_PREFIX = "Linked page: "


def is_linked_page(doc: SourceDocument | DocumentFit) -> bool:
    """A page `--follow-links` appended, as opposed to the ticket's own documents."""
    return doc.id.startswith("confluence:") and doc.title.startswith(LINKED_TITLE_PREFIX)


def _name(doc: SourceDocument | DocumentFit) -> str:
    return (doc.url or doc.id) if is_linked_page(doc) else doc.id


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _cut_and_dropped(fits: list[DocumentFit], budget: int, *, what: str = "budget") -> str:
    """``A cut, B and C did not fit the 60,000-char budget`` — or ``""`` when everything fitted."""
    cut = [_name(d) for d in fits if d.state == "cut"]
    dropped = [_name(d) for d in fits if d.state == "dropped"]
    limit = f"the {budget:,}-char {what}"
    if cut and dropped:
        return f"{_and(cut)} cut, {_and(dropped)} did not fit {limit}"
    if cut:
        return f"{_and(cut)} cut at {limit}"
    if dropped:
        return f"{_and(dropped)} did not fit {limit}"
    return ""


def extraction_note(fit: PromptFit) -> str:
    """What of the ticket's *own* documents the extraction lost — linked pages are reported on the
    Linked pages line. ``""`` when the ticket fitted (D8, D9)."""
    return _cut_and_dropped(
        [d for d in fit.documents if not is_linked_page(d)], fit.budget, what="extraction budget"
    )


def extraction_warning(fit: PromptFit) -> str:
    """One line on everything the extraction lost, linked or not; ``""`` when nothing (D5)."""
    lost = _cut_and_dropped(list(fit.documents), fit.budget)
    return f"the spec was extracted from part of the source — {lost}" if lost else ""


class PageReader(Protocol):
    """All `--follow-links` needs of a Confluence source: read one page by id."""

    async def fetch_document(self, doc_id: str) -> SourceDocument: ...


@dataclass
class FollowReport:
    """What `--follow-links` read and what it could not, for the build document's header."""

    documents: list[SourceDocument] = field(default_factory=list)
    not_read: list[tuple[str, str]] = field(default_factory=list)  # (url, why)
    unsupported: str = ""  # set when the source is not one whose links can be followed

    def summary(self, extraction: PromptFit | None = None, *, spec_extracted: bool = True) -> str:
        """``followed — 2 read, 1 not read (<url>: <why>)`` — or why nothing was followed.

        With ``extraction`` — the fit of the documents the spec was derived from — it also says
        what of each page reached the model. ``spec_extracted=False`` says the spec was written by
        hand, so the pages reached `source.txt` and nothing else.
        """
        if self.unsupported:
            return f"not followed — {self.unsupported}"
        read = len(self.documents)
        if not read and not self.not_read:
            return "followed — the ticket links no Confluence pages"
        not_read = ""
        if self.not_read:
            reasons = "; ".join(f"{url}: {why}" for url, why in self.not_read)
            not_read = f"{len(self.not_read)} not read ({reasons})"
        if not spec_extracted and read:
            text = f"followed — {read} read into source.txt; the spec is hand-written, so none was extracted"
            return f"{text}; {not_read}" if not_read else text
        if extraction is None or not read:
            text = f"followed — {read} read"
            return f"{text}, {not_read}" if not_read else text
        return self._against(extraction, read, not_read)

    def _against(self, extraction: PromptFit, read: int, not_read: str) -> str:
        fetched = {d.id for d in self.documents}
        seen = [d for d in extraction.documents if d.id in fetched]
        since = [_name(d) for d in self.documents if extraction.state_of(d.id) is None]
        gone = [_name(d) for d in extraction.documents if is_linked_page(d) and d.id not in fetched]
        if not since and not gone and all(d.state == "full" for d in seen):
            text = f"followed — {read} read, " + (
                "in the spec's extraction" if read == 1 else f"all {read} in the spec's extraction"
            )
            return f"{text}, {not_read}" if not_read else text
        full = sum(1 for d in seen if d.state == "full")
        cut = [_name(d) for d in seen if d.state == "cut"]
        dropped = [_name(d) for d in seen if d.state == "dropped"]
        fit: list[str] = []
        if full:
            fit.append(f"{full} in the spec's extraction in full")
        if cut:
            fit.append(f"{len(cut)} cut ({', '.join(cut)})")
        if dropped:
            fit.append(
                f"{len(dropped)} did not fit the {extraction.budget:,}-char budget ({', '.join(dropped)})"
            )
        parts = [f"followed — {read} read"]
        if fit:
            parts.append(", ".join(fit))
        if since:
            parts.append(f"{len(since)} linked since the spec was extracted ({', '.join(since)}) — not in it")
        if gone:
            parts.append(f"{len(gone)} in the spec's extraction but no longer linked ({', '.join(gone)})")
        if not_read:
            parts.append(not_read)
        return "; ".join(parts)


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
                title=f"{LINKED_TITLE_PREFIX}{doc.title}",
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


__all__ = [
    "LINKED_TITLE_PREFIX",
    "MAX_LINKED_PAGES",
    "FollowReport",
    "PageReader",
    "extraction_note",
    "extraction_warning",
    "follow_confluence_links",
    "is_linked_page",
]
