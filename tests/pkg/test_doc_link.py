"""Doc ingestion (phase 1): text docs → ``Doc`` nodes + ``MENTIONS`` edges.

Reads the doc source, folds docs into a code batch, and checks the store can answer
"which docs describe X?" — with precision-first binding (ambiguous mentions skipped).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore, doc_drift, link_docs
from orchestrator.pkg.doc_link import symbolish_drift
from orchestrator.pkg.doc_source import is_doc_file, read_doc_pages, split_sections
from orchestrator.pkg.docs import DocPage
from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind, Provenance

# A minimal single-page PDF whose text stream reads "calc_tax mentioned here". pypdf
# recovers the objects even though the xref offsets are approximate.
_TEXT_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 58>>stream
BT /F1 12 Tf 20 250 Td (calc_tax mentioned here) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000052 00000 n
0000000101 00000 n
0000000209 00000 n
0000000317 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
388
%%EOF"""


def _code_batch() -> FactBatch:
    """A tiny code graph: one module, one type, one function."""
    b = FactBatch()
    b.add_node(
        Node(
            "py:billing.invoice",
            NodeKind.MODULE,
            "billing.invoice",
            "python",
            Provenance("src/billing/invoice.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:billing.invoice.Invoice",
            NodeKind.TYPE,
            "Invoice",
            "python",
            Provenance("src/billing/invoice.py", 4),
        )
    )
    b.add_node(
        Node(
            "py:billing.tax.calc_tax",
            NodeKind.FUNCTION,
            "calc_tax",
            "python",
            Provenance("src/billing/tax.py", 1),
        )
    )
    return b


def _write_repo(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "The `Invoice` type holds line items; `calc_tax` applies regional rules.\n", encoding="utf-8"
    )
    (root / "docs" / "design.rst").write_text("Legacy: `apply_discount` was removed.\n", encoding="utf-8")
    (root / "notes.txt").write_text("plain prose, no code claims\n", encoding="utf-8")


# ---- doc source -------------------------------------------------------------


def test_is_doc_file_recognises_text_docs_and_pdf() -> None:
    assert is_doc_file(Path("README.md"))
    assert is_doc_file(Path("docs/x.rst"))
    assert is_doc_file(Path("notes.txt"))
    assert is_doc_file(Path("spec.pdf"))  # PDF recognised (reading still needs the [docs] extra)
    assert not is_doc_file(Path("src/app.py"))
    assert not is_doc_file(Path("data.json"))


def test_read_doc_pages_walks_repo_and_skips_hidden(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "COMMIT_EDITMSG.md").write_text("not a doc\n", encoding="utf-8")
    titles = {p.title for p in read_doc_pages(tmp_path)}
    assert titles == {"README.md", "docs/design.rst", "notes.txt"}  # POSIX rel paths, no VCS internals


def test_read_doc_pages_extracts_pdf_text(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")  # the [docs] extra
    (tmp_path / "spec.pdf").write_bytes(_TEXT_PDF)
    pages = {p.title: p.text for p in read_doc_pages(tmp_path)}
    assert "spec.pdf" in pages
    assert "calc_tax" in pages["spec.pdf"]


def test_read_doc_pages_skips_unparseable_pdf(tmp_path: Path) -> None:
    (tmp_path / "junk.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    (tmp_path / "ok.md").write_text("hi\n", encoding="utf-8")
    # A malformed PDF (or one with no extractable text) is skipped, never fatal.
    assert {p.title for p in read_doc_pages(tmp_path)} == {"ok.md"}


# ---- link_docs --------------------------------------------------------------


def test_link_docs_adds_doc_nodes_and_mentions(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    linked = link_docs(_code_batch(), tmp_path)

    doc_ids = {n.id for n in linked.nodes if n.kind is NodeKind.DOC}
    assert doc_ids == {"doc:README.md", "doc:docs/design.rst", "doc:notes.txt"}

    store = FactStore(linked)
    # README names two live symbols → both become MENTIONS targets.
    mentioned = {n.name for n in store.mentions_of("doc:README.md")}
    assert mentioned == {"Invoice", "calc_tax"}
    # And the reverse query answers "which docs describe Invoice?".
    assert [d.id for d in store.docs_for("py:billing.invoice.Invoice")] == ["doc:README.md"]


def test_link_docs_skips_unresolved_mentions(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    store = FactStore(link_docs(_code_batch(), tmp_path))
    # `apply_discount` names no symbol in the code batch → no MENTIONS edge (it's drift, not a link).
    assert store.mentions_of("doc:docs/design.rst") == []
    # A Doc is never itself a MENTIONS target (reconciler is built from the code-only batch).
    assert all(e.dst.startswith("py:") for e in store.edges_of_kind(EdgeKind.MENTIONS))


def test_link_docs_is_noop_without_docs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    before = _code_batch()
    n_nodes, n_edges = len(before.nodes), len(before.edges)
    linked = link_docs(before, tmp_path)
    assert (len(linked.nodes), len(linked.edges)) == (n_nodes, n_edges)


# ---- doc_drift --------------------------------------------------------------


def test_doc_drift_flags_code_claims_the_code_lacks(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    drifted = {f.mention for f in doc_drift(_code_batch(), tmp_path)}
    assert "apply_discount" in drifted  # backticked claim, no such symbol → drift
    assert "Invoice" not in drifted  # grounded → not drift


def test_doc_drift_empty_without_docs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert doc_drift(_code_batch(), tmp_path) == []


# ---- section granularity (phase 3) -----------------------------------------


def test_split_sections_by_heading() -> None:
    page = DocPage(
        title="README.md",
        text="preamble\n\n# Install\nsetup steps\n\n## Usage\ncall it\n",
        source_file="README.md",
    )
    secs = {p.title: p for p in split_sections(page)}
    assert set(secs) == {"README.md", "README.md#install", "README.md#usage"}
    assert secs["README.md#install"].line == 3  # provenance at the heading line
    assert secs["README.md#usage"].line == 6
    assert "call it" in secs["README.md#usage"].text


def test_split_sections_leaves_heading_less_docs_whole() -> None:
    page = DocPage(title="notes.md", text="just prose, no headings\n", source_file="notes.md")
    assert [p.title for p in split_sections(page)] == ["notes.md"]


def test_link_docs_binds_mentions_to_their_section(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Overview\nnothing here\n\n# Billing\nThe `Invoice` type totals lines.\n", encoding="utf-8"
    )
    store = FactStore(link_docs(_code_batch(), tmp_path))
    # The MENTIONS edge points at the *section* that names Invoice, not the whole file.
    assert [d.id for d in store.docs_for("py:billing.invoice.Invoice")] == ["doc:README.md#billing"]


def test_symbolish_drift_filter() -> None:
    assert symbolish_drift("build_current_state")
    assert symbolish_drift("understand.BANK_DIRNAME")  # dotted identifier
    assert not symbolish_drift("episteme/")  # path
    assert not symbolish_drift("jira://PROJ-123")  # URL
    assert not symbolish_drift("graph.html")  # filename (asset extension)
    assert not symbolish_drift("app_test.go")  # filename (code extension, still a file)
