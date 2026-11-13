"""Symbol text (hybrid KG search, D3): the words that stand for a symbol, read from its own source span.

The graph's nodes carry no text, so the content signal BM25 and an embedding model need is read at index
time from ``provenance.file`` lines ``line..end_line``. Two recipes: **A** (name, qualified name, path,
signature) and **B** (A plus the leading comment and the first body lines). A file that cannot be read, or a
path that leaves the root, gives the name and path only — never an exception, never a byte from outside the
repository.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.pkg import FactBatch, FactStore, Node, NodeKind, Provenance
from orchestrator.pkg.extractor import mark_tests
from orchestrator.pkg.symbol_text import (
    BODY_LINES,
    RECIPE_VERSION,
    Recipe,
    SourceLines,
    symbol_text,
    symbol_texts,
)

_BILLING = '''\
import json


# Writes the invoice as a CSV row for the ledger export.
def export_invoice(
    invoice,
    path,
):
    """Persist one invoice to the accounting ledger."""
    row = json.dumps(invoice)
    with open(path, "a") as fh:
        fh.write(row)
    return row


def unrelated():
    return "ledger"
'''


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "billing.py").write_text(_BILLING)
    return tmp_path


def _fn(name: str, line: int, end: int | None, file: str = "billing.py", lang: str = "python") -> Node:
    stem = file.rsplit(".", 1)[0].replace("/", ".")
    return Node(f"py:{stem}.{name}", NodeKind.FUNCTION, name, lang, Provenance(file, line, end))


def test_recipe_a_carries_the_name_the_qualified_name_the_path_and_the_whole_signature(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    text = symbol_text(_fn("export_invoice", 5, 13), SourceLines(root), Recipe.NAME_SIGNATURE)
    assert "export_invoice" in text
    assert "billing.export_invoice" in text
    assert "billing.py" in text
    # A signature split over four lines is read until its parentheses close.
    assert "invoice," in text and "path," in text and "):" in text
    # Recipe A stops at the signature: no body, no comment.
    assert "accounting ledger" not in text
    assert "CSV row" not in text


def test_recipe_b_adds_the_leading_comment_the_docstring_and_the_body(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = symbol_text(_fn("export_invoice", 5, 13), SourceLines(root), Recipe.WITH_BODY)
    assert "CSV row for the ledger export" in text  # the comment right above the declaration
    assert "accounting ledger" in text  # the docstring
    assert "fh.write(row)" in text  # the body


def test_recipe_b_never_reads_past_the_symbols_own_end(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = symbol_text(_fn("export_invoice", 5, 13), SourceLines(root), Recipe.WITH_BODY)
    assert "def unrelated" not in text
    assert '"ledger"' not in text


def test_without_an_end_line_the_body_is_bounded_by_the_body_line_cap(tmp_path: Path) -> None:
    body = "\n".join(f"    step_{i} = {i}" for i in range(BODY_LINES + 30))
    (tmp_path / "long.py").write_text(f"def long_one():\n{body}\n")
    text = symbol_text(_fn("long_one", 1, None, file="long.py"), SourceLines(tmp_path), Recipe.WITH_BODY)
    assert f"step_{BODY_LINES - 2} " in text
    assert f"step_{BODY_LINES + 5} " not in text


def test_a_file_that_is_gone_gives_the_name_and_path_and_no_error(tmp_path: Path) -> None:
    text = symbol_text(
        _fn("export_invoice", 5, 13, file="deleted.py"), SourceLines(tmp_path), Recipe.WITH_BODY
    )
    assert "export_invoice" in text
    assert "deleted.py" in text


@pytest.mark.parametrize("escape", ["../secret.py", "/etc/secret.py", "sub/../../secret.py"])
def test_a_path_that_leaves_the_root_is_never_read(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.py").write_text("def leak():\n    return 'TOPSECRET'\n")
    text = symbol_text(_fn("leak", 1, 2, file=escape), SourceLines(root), Recipe.WITH_BODY)
    assert "TOPSECRET" not in text
    assert "leak" in text


def test_a_symlink_out_of_the_root_is_never_read(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def leak():\n    return 'TOPSECRET'\n")
    os.symlink(outside, root / "link.py")
    text = symbol_text(_fn("leak", 1, 2, file="link.py"), SourceLines(root), Recipe.WITH_BODY)
    assert "TOPSECRET" not in text


def test_other_languages_get_their_signature_and_comment_too(tmp_path: Path) -> None:
    (tmp_path / "server.go").write_text(
        "package server\n\n// ServeInvoices streams every invoice to the client.\n"
        "func ServeInvoices(w http.ResponseWriter, r *http.Request) {\n\tw.Write(nil)\n}\n"
    )
    (tmp_path / "ledger.c").write_text(
        "/* Append one entry to the ledger file. */\nint ledger_append(FILE *fh,\n"
        "                  const char *row) {\n    return fputs(row, fh);\n}\n"
    )
    go = Node(
        "go:server.ServeInvoices", NodeKind.FUNCTION, "ServeInvoices", "go", Provenance("server.go", 4, 6)
    )
    c = Node("c:ledger_append", NodeKind.FUNCTION, "ledger_append", "c", Provenance("ledger.c", 2, 5))
    lines = SourceLines(tmp_path)
    go_a = symbol_text(go, lines, Recipe.NAME_SIGNATURE)
    assert "ResponseWriter" in go_a
    assert "streams every invoice" in symbol_text(go, lines, Recipe.WITH_BODY)
    c_a = symbol_text(c, lines, Recipe.NAME_SIGNATURE)
    assert "const char *row" in c_a  # the signature's second line, read until the parenthesis closes
    assert "Append one entry" in symbol_text(c, lines, Recipe.WITH_BODY)


def _store(*nodes: Node) -> FactStore:
    batch = FactBatch()
    for n in nodes:
        batch.add_node(n)
    return FactStore(mark_tests(batch))


def test_the_index_holds_product_code_only_unless_tests_are_asked_for(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_billing.py").write_text("def test_export():\n    assert True\n")
    store = _store(
        _fn("export_invoice", 5, 13),
        Node(
            "py:tests.test_billing.test_export",
            NodeKind.FUNCTION,
            "test_export",
            "python",
            Provenance("tests/test_billing.py", 1, 2),
        ),
        Node("doc:README.md#usage", NodeKind.DOC, "Usage", "", Provenance("README.md", 1, 3)),
        Node("intent:SSPN-1", NodeKind.INTENT, "Export invoices", ""),
        Node("py:json", NodeKind.MODULE, "json", "python", external=True),
    )
    product = symbol_texts(store, root, Recipe.NAME_SIGNATURE)
    assert set(product) == {"py:billing.export_invoice"}
    with_tests = symbol_texts(store, root, Recipe.NAME_SIGNATURE, include_tests=True)
    assert set(with_tests) == {"py:billing.export_invoice", "py:tests.test_billing.test_export"}


def test_the_index_is_the_same_bytes_every_time(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    store = _store(_fn("unrelated", 16, 17), _fn("export_invoice", 5, 13))  # added out of id order on purpose
    first = symbol_texts(store, root, Recipe.WITH_BODY)
    assert first == symbol_texts(store, root, Recipe.WITH_BODY)
    assert list(first) == sorted(
        first
    )  # keyed in node-id order, so iteration order never depends on the graph's
    assert isinstance(RECIPE_VERSION, int) and RECIPE_VERSION >= 1
