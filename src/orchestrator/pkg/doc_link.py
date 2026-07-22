"""Fold a repo's documentation into the PKG (doc-ingestion, phase 1).

A post-extraction pass (like :func:`data_layer_link.link_data_layer`): after the code graph
exists, read the repo's text docs and add a ``Doc`` node per file plus a ``MENTIONS`` edge to
each code symbol the doc *unambiguously* names — reusing the deterministic doc→symbol binder in
:mod:`pkg.docs`. So the graph can answer "which docs describe ``X``?" and ground code in prose.

Precision-first: a mention that resolves to several symbols is **skipped** (an ambiguous edge
poisons grounding), and file-only mentions aren't linked in this phase (symbols only). A repo
with no docs comes back unchanged, so wiring it into comprehension is safe everywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.docs import DocDriftFinding, DocPage, DocReconciler, extract_mentions
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# Doc drift is about *symbols* the docs claim but the graph lacks. The raw drift set also catches
# path/URL/filename mentions in backticks (`episteme/`, `graph.html`, `jira://X`, `foo_test.go`) —
# real "unbound" mentions, but not symbol drift; they'd drown the signal. Keep only identifier /
# dotted-identifier claims whose final segment isn't a file extension.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# fmt: off
_DRIFT_EXT = frozenset({
    "html", "md", "markdown", "rst", "txt", "json", "yaml", "yml", "toml", "ini", "cfg",
    "lock", "xml", "csv", "tsv", "png", "svg", "jpg", "jpeg", "gif", "pdf",
    "go", "py", "ts", "tsx", "js", "jsx", "css", "scss", "sh", "bat",
})
# fmt: on


def symbolish_drift(mention: str) -> bool:
    """True when a drift claim looks like a code symbol (not a path/URL/filename)."""
    if not _IDENT_RE.match(mention):
        return False
    return mention.rsplit(".", 1)[-1].lower() not in _DRIFT_EXT


def _doc_id(page: DocPage) -> str:
    return f"doc:{page.title}"


def link_docs(batch: FactBatch, repo_root: Path | str) -> FactBatch:
    """Return ``batch`` with a ``Doc`` node per doc *section* + ``MENTIONS`` edges to the symbols it
    names. No-op when the repo has no docs. The reconciler is built from the (code-only) batch, so
    ``Doc`` nodes are never themselves mention targets. Section-granular by default: a markdown doc
    becomes one ``Doc`` node per heading (``doc:README.md#usage``), each with provenance at its
    heading line, so ``MENTIONS`` point at the section that names a symbol (see
    :func:`doc_source.split_sections`)."""
    pages = read_doc_pages(repo_root)
    if not pages:
        return batch
    reconciler = DocReconciler(batch, repo_root=repo_root)
    for page in pages:
        doc_id = _doc_id(page)
        prov = Provenance(page.source_file or page.title, page.line)
        batch.add_node(Node(doc_id, NodeKind.DOC, page.title, "doc", prov))
        for mention in extract_mentions(page):
            anchors = reconciler.bind(mention, base_dir=page.base_dir).anchor_ids
            if len(anchors) == 1:  # unambiguous symbol mention only
                batch.add_edge(Edge(doc_id, anchors[0], EdgeKind.MENTIONS, prov))
    return batch


def doc_drift(batch: FactBatch, repo_root: Path | str) -> list[DocDriftFinding]:
    """Doc-drift findings for a repo — code-intent claims in the docs the code doesn't support
    ("the docs lie about the code"). Deterministic; surfaced by ``state`` in a later phase."""
    pages = read_doc_pages(repo_root)
    if not pages:
        return []
    _bindings, drift = DocReconciler(batch, repo_root=repo_root).reconcile(pages)
    return drift


__all__ = ["doc_drift", "link_docs", "symbolish_drift"]
