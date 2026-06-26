"""Block B.1: the source-system adapter layer.

An adopter's requirements live in Confluence, Notion, Linear, a Google
Doc, or plain markdown in a repo. The intent extractor (B.2) shouldn't
care which — it consumes ``SourceDocument``s. ``SourceAdapter`` is the
seam: one protocol, many implementations. Confluence is the first
(``orchestrator.intake.confluence``); the plan's adapter-layer lever
(adoption: bring-your-own stack) lives here.

``fetch_tree`` is the common entry point: given a root (a Confluence
space or page id), walk children breadth-first up to ``max_depth`` /
``max_docs`` and return the flattened document list the extractor reads.
The caps keep one ingest from pulling an entire wiki.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_DOCS = 100


@dataclass(frozen=True)
class SourceRef:
    """A pointer to a document in the source system (not yet fetched)."""

    id: str
    title: str
    kind: str = "page"  # page | space | folder | …


@dataclass(frozen=True)
class SourceDocument:
    """A fetched requirements document, normalised to plain text.

    ``body`` is source-format-agnostic text (the adapter strips Confluence
    storage XHTML, Notion blocks, etc. down to readable text). Downstream
    consumers reason over ``body`` without knowing the origin format.
    """

    id: str
    title: str
    body: str
    url: str = ""
    space: str = ""
    labels: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.body.strip()


@dataclass
class FetchTreeResult:
    """Outcome of a tree walk: the documents + whether a cap was hit."""

    documents: list[SourceDocument] = field(default_factory=list)
    truncated: bool = False  # True when max_depth / max_docs cut the walk short


class SourceAdapter(Protocol):
    """Minimal contract every requirements-source adapter satisfies."""

    source_kind: str  # "confluence" | "notion" | …

    async def fetch_document(self, doc_id: str) -> SourceDocument: ...

    async def list_children(self, doc_id: str) -> list[SourceRef]: ...

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult: ...
