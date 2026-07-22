"""The doc-reader seam: formats register, they don't branch.

`test_doc_link.py` covers what ingestion *produces* (Doc nodes, MENTIONS, drift). This file
covers the reader layer itself — the registry, and each format's text extraction.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator.pkg.doc_source import (
    _READERS,
    DocReader,
    _docx_heading_level,
    is_doc_file,
    read_doc_pages,
    register_reader,
)


@pytest.fixture
def restore_readers() -> Iterator[None]:
    """Snapshot/restore the global registry so a test registration can't leak."""
    saved = dict(_READERS)
    yield
    _READERS.clear()
    _READERS.update(saved)


# ---- the seam ---------------------------------------------------------------


def test_builtin_readers_are_registered() -> None:
    for suffix in (".md", ".markdown", ".rst", ".txt", ".html", ".htm", ".pdf", ".docx", ".xlsx"):
        assert suffix in _READERS, suffix
    # Formats that carry headings are section-split; flat ones are not.
    assert _READERS[".html"].sections is True
    assert _READERS[".md"].sections is True
    assert _READERS[".docx"].sections is True
    assert _READERS[".rst"].sections is False
    assert _READERS[".pdf"].sections is False
    assert _READERS[".rst"].sections is False
    assert _READERS[".pdf"].sections is False


def test_register_reader_adds_a_format_without_touching_dispatch(
    tmp_path: Path, restore_readers: None
) -> None:
    register_reader(DocReader("shouty", frozenset({".loud"}), lambda p: p.read_text().upper()))
    (tmp_path / "note.loud").write_text("calls `validate`\n", encoding="utf-8")

    assert is_doc_file(Path("x.loud"))
    pages = {p.title: p.text for p in read_doc_pages(tmp_path)}
    assert pages["note.loud"].strip() == "CALLS `VALIDATE`"


def test_reader_returning_none_skips_the_file(tmp_path: Path, restore_readers: None) -> None:
    register_reader(DocReader("never", frozenset({".nope"}), lambda _p: None))
    (tmp_path / "a.nope").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("kept\n", encoding="utf-8")
    # A reader that declines is non-fatal — the walk continues (the pypdf precedent).
    assert {p.title for p in read_doc_pages(tmp_path)} == {"b.md"}


def test_unregistered_suffix_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert read_doc_pages(tmp_path) == []
    assert not is_doc_file(Path("app.py"))


# ---- HTML (G2 phase 1) ------------------------------------------------------

_HTML_DOC = """<html><head>
<style>.a{color:red}</style>
<script>var apply_discount = 1;</script>
</head><body>
<h1>Overview</h1>
<p>Nothing to see.</p>
<h2>Billing</h2>
<p>The <code>Invoice</code> type totals lines; see <code>calc_tax</code>.</p>
<pre><code>Invoice().total()</code></pre>
</body></html>
"""


def test_html_headings_become_sections(tmp_path: Path) -> None:
    (tmp_path / "guide.html").write_text(_HTML_DOC, encoding="utf-8")
    pages = {p.title: p for p in read_doc_pages(tmp_path)}
    # <h1>/<h2> map to ATX headings, so split_sections treats HTML exactly like markdown.
    assert set(pages) == {"guide.html#overview", "guide.html#billing"}
    assert pages["guide.html#billing"].source_file == "guide.html"


def test_html_inline_code_becomes_backticks(tmp_path: Path) -> None:
    (tmp_path / "guide.html").write_text(_HTML_DOC, encoding="utf-8")
    billing = next(p for p in read_doc_pages(tmp_path) if p.title.endswith("#billing"))
    # <code>X</code> is HTML's backtick — preserved so the binder sees a high-confidence claim.
    assert "`Invoice`" in billing.text
    assert "`calc_tax`" in billing.text


def test_html_drops_script_and_style(tmp_path: Path) -> None:
    (tmp_path / "guide.html").write_text(_HTML_DOC, encoding="utf-8")
    text = " ".join(p.text for p in read_doc_pages(tmp_path))
    assert "apply_discount" not in text  # <script> body is code, not prose
    assert "color:red" not in text


def test_html_pre_block_is_not_backticked(tmp_path: Path) -> None:
    (tmp_path / "guide.html").write_text(_HTML_DOC, encoding="utf-8")
    text = " ".join(p.text for p in read_doc_pages(tmp_path))
    # The sample survives as plain text, but isn't wrapped into one giant backtick span.
    assert "Invoice().total()" in text
    assert "`Invoice().total()`" not in text


def test_html_without_headings_stays_whole(tmp_path: Path) -> None:
    (tmp_path / "flat.html").write_text("<p>just <code>prose</code> here</p>", encoding="utf-8")
    assert [p.title for p in read_doc_pages(tmp_path)] == ["flat.html"]


def test_malformed_html_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "bad.html").write_text("<<<>>> \x00 not really markup", encoding="utf-8")
    (tmp_path / "ok.md").write_text("fine\n", encoding="utf-8")
    assert "ok.md" in {p.title for p in read_doc_pages(tmp_path)}


# ---- markdown front matter (G2 phase 1) -------------------------------------


def test_front_matter_keeps_values_drops_keys(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text(
        "---\ntitle: Billing spec\nmodule: 'billing.tax.calc_tax'\ntags:\n  - finance\n---\n\nbody prose\n",
        encoding="utf-8",
    )
    text = read_doc_pages(tmp_path)[0].text
    assert "billing.tax.calc_tax" in text  # the value is a real code claim
    assert "finance" in text and "Billing spec" in text
    assert "module:" not in text and "---" not in text  # keys and fences are noise
    assert "body prose" in text


def test_markdown_without_front_matter_is_untouched(tmp_path: Path) -> None:
    (tmp_path / "plain.md").write_text("# Title\n\nsome `calc_tax` prose\n", encoding="utf-8")
    assert read_doc_pages(tmp_path, sections=False)[0].text == "# Title\n\nsome `calc_tax` prose\n"


def test_standalone_yaml_is_not_ingested(tmp_path: Path) -> None:
    """Config is not documentation — see the note by the reader registrations.

    Ingesting CI/compose/manifest YAML would inflate `state`'s doc-coverage and flood
    doc_drift with identifier-shaped config values."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: app\n", encoding="utf-8")
    (tmp_path / "ci.yaml").write_text("jobs:\n  test:\n    run: orchestrator.cli\n", encoding="utf-8")
    assert read_doc_pages(tmp_path) == []


# ---- Office (G2 phase 2) ----------------------------------------------------


def _docx(path: Path) -> None:
    """A realistic Word spec: heading styles, a monospace code run, and a table."""
    docx = pytest.importorskip("docx")  # the [office] extra
    doc = docx.Document()
    doc.add_heading("Billing Architecture", level=1)
    doc.add_paragraph("Overview prose.")
    doc.add_heading("Tax", level=2)
    para = doc.add_paragraph("Applied by ")
    run = para.add_run("calc_tax")
    run.font.name = "Consolas"  # Word's inline code
    para.add_run(" before totalling.")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Symbol"
    table.rows[0].cells[1].text = "apply_discount"
    doc.save(str(path))


def test_docx_heading_styles_become_sections(tmp_path: Path) -> None:
    _docx(tmp_path / "spec.docx")
    titles = {p.title for p in read_doc_pages(tmp_path)}
    # Word "Heading 1"/"Heading 2" map to ATX levels, so .docx sections like markdown does.
    assert titles == {"spec.docx#billing-architecture", "spec.docx#tax"}


def test_docx_monospace_run_becomes_backticks(tmp_path: Path) -> None:
    _docx(tmp_path / "spec.docx")
    tax = next(p for p in read_doc_pages(tmp_path) if p.title.endswith("#tax"))
    # A Consolas run is Word's inline code — preserved as a high-confidence claim.
    assert "`calc_tax`" in tax.text
    # Runs concatenate without inserted separators, so identifiers aren't fractured.
    assert "cal c_tax" not in tax.text


def test_docx_table_text_is_kept(tmp_path: Path) -> None:
    _docx(tmp_path / "spec.docx")
    text = " ".join(p.text for p in read_doc_pages(tmp_path))
    assert "apply_discount" in text  # spec tables carry real content


def test_docx_heading_level_mapping() -> None:
    assert _docx_heading_level("Heading 1") == 1
    assert _docx_heading_level("heading 6") == 6
    assert _docx_heading_level("Title") == 1
    assert _docx_heading_level("Normal") == 0
    assert _docx_heading_level("Heading 9") == 0  # outside ATX range


def test_xlsx_sheet_becomes_a_section_with_string_cells(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")  # the [office] extra
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "API Surface"
    sheet.append(["Endpoint", "Handler"])
    sheet.append(["/invoices", "calc_tax"])
    sheet.append([1, 2.5])  # numeric row: data, not prose
    book.save(str(tmp_path / "matrix.xlsx"))

    pages = {p.title: p.text for p in read_doc_pages(tmp_path)}
    assert "matrix.xlsx#api-surface" in pages
    assert "calc_tax" in pages["matrix.xlsx#api-surface"]
    assert "2.5" not in pages["matrix.xlsx#api-surface"]


def test_corrupt_office_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "broken.docx").write_bytes(b"not a zip at all")
    (tmp_path / "broken.xlsx").write_bytes(b"also not a zip")
    (tmp_path / "ok.md").write_text("fine\n", encoding="utf-8")
    assert {p.title for p in read_doc_pages(tmp_path)} == {"ok.md"}
