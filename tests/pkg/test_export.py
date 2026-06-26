"""PKG export: the kind-per-table SQLite projection ontomesh ingests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    Node,
    NodeKind,
    Provenance,
    export_sqlite,
)


def _batch() -> FactBatch:
    b = FactBatch()
    b.add_node(Node("py:billing", NodeKind.MODULE, "billing", "python", Provenance("billing.py", 1)))
    b.add_node(Node("py:tax", NodeKind.MODULE, "tax", "python", external=True))
    b.add_node(Node("py:billing.Invoice", NodeKind.TYPE, "Invoice", "python", Provenance("billing.py", 4, 9)))
    b.add_node(
        Node("py:billing.Invoice.total", NodeKind.FUNCTION, "total", "python", Provenance("billing.py", 5, 7))
    )
    b.add_node(Node("py:tax.calc_tax", NodeKind.FUNCTION, "calc_tax", "python", external=True))
    b.add_edge(Edge("py:billing", "py:billing.Invoice", EdgeKind.CONTAINS, Provenance("billing.py", 4)))
    b.add_edge(
        Edge("py:billing.Invoice", "py:billing.Invoice.total", EdgeKind.CONTAINS, Provenance("billing.py", 5))
    )
    b.add_edge(Edge("py:billing", "py:tax", EdgeKind.IMPORTS, Provenance("billing.py", 2)))
    b.add_edge(
        Edge("py:billing.Invoice.total", "py:tax.calc_tax", EdgeKind.CALLS, Provenance("billing.py", 6))
    )
    return b


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "facts.db"
    counts = export_sqlite(_batch(), db)
    assert counts["ontology_metadata"] > 0
    return sqlite3.connect(db)


def test_kind_per_table_rows(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        assert {r[0] for r in conn.execute("SELECT id FROM modules")} == {"py:billing", "py:tax"}
        assert conn.execute("SELECT external FROM modules WHERE id='py:tax'").fetchone()[0] == 1
        (type_row,) = conn.execute("SELECT id, module_id, file, line, end_line FROM types").fetchall()
        assert type_row == ("py:billing.Invoice", "py:billing", "billing.py", 4, 9)
    finally:
        conn.close()


def test_contains_edges_become_fks(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        row = conn.execute(
            "SELECT parent_type_id, module_id FROM functions WHERE id='py:billing.Invoice.total'"
        ).fetchone()
        assert row == ("py:billing.Invoice", None)  # owned by the type, not directly the module
    finally:
        conn.close()


def test_calls_and_imports_relations(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        (call,) = conn.execute("SELECT caller_id, callee_id, file, line FROM calls").fetchall()
        assert call == ("py:billing.Invoice.total", "py:tax.calc_tax", "billing.py", 6)
        (imp,) = conn.execute("SELECT module_id, target_id FROM imports").fetchall()
        assert imp == ("py:billing", "py:tax")
    finally:
        conn.close()


def test_annotations_present_for_ontomesh(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        semantic = dict(
            conn.execute(
                "SELECT table_name, semantic_type FROM ontology_metadata WHERE target_type='TABLE'"
            ).fetchall()
        )
        assert semantic == {"modules": "Module", "types": "Type", "functions": "Function"}
        fk_props = {
            r[0]
            for r in conn.execute("SELECT label FROM ontology_metadata WHERE target_type='COLUMN'").fetchall()
        }
        assert {"calls", "imports", "memberOf", "containedIn"} <= fk_props
    finally:
        conn.close()


def test_export_overwrites_existing_file(tmp_path: Path) -> None:
    db = tmp_path / "facts.db"
    export_sqlite(_batch(), db)
    counts = export_sqlite(_batch(), db)  # second run must not fail or duplicate
    assert counts["modules"] == 2 and counts["calls"] == 1
