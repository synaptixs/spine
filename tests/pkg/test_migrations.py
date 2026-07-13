"""Migration-aware schema folding (SQL Track A, phase A4)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, NodeKind  # noqa: E402
from orchestrator.pkg.migrations import (  # noqa: E402
    apply_migrations,
    find_migration_files,
    fold_migrations,
)


def _write_migrations(tmp_path: Path, files: dict[str, str]) -> Path:
    mig = tmp_path / "migrations"
    mig.mkdir()
    for name, sql in files.items():
        (mig / name).write_text(sql, encoding="utf-8")
    return tmp_path


def test_find_migration_files_is_ordered(tmp_path: Path) -> None:
    root = _write_migrations(
        tmp_path,
        {"002_second.sql": "CREATE TABLE b (id INT);", "001_first.sql": "CREATE TABLE a (id INT);"},
    )
    files = find_migration_files(root)
    assert [f.name for f in files] == ["001_first.sql", "002_second.sql"]


def test_fold_applies_add_then_drop_column(tmp_path: Path) -> None:
    root = _write_migrations(
        tmp_path,
        {
            "001_init.sql": "CREATE TABLE orders (id INT, total INT, notes TEXT);",
            "002_drop.sql": "ALTER TABLE orders DROP COLUMN notes;",
            "003_add.sql": "ALTER TABLE orders ADD COLUMN status TEXT;",
        },
    )
    schema = fold_migrations(find_migration_files(root), root=root)
    orders = next(t for t in schema.tables if t.name == "orders")
    cols = {c.name for c in orders.columns}
    assert cols == {"id", "total", "status"}  # notes dropped, status added


def test_fold_rename_column_and_table(tmp_path: Path) -> None:
    root = _write_migrations(
        tmp_path,
        {
            "001.sql": "CREATE TABLE orders (id INT, total INT);",
            "002.sql": "ALTER TABLE orders RENAME COLUMN total TO amount;",
            "003.sql": "ALTER TABLE orders RENAME TO purchase_orders;",
        },
    )
    schema = fold_migrations(find_migration_files(root), root=root)
    names = {t.name for t in schema.tables}
    assert names == {"purchase_orders"}
    cols = {c.name for t in schema.tables for c in t.columns}
    assert cols == {"id", "amount"}


def test_fold_drop_table(tmp_path: Path) -> None:
    root = _write_migrations(
        tmp_path,
        {"001.sql": "CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);", "002.sql": "DROP TABLE b;"},
    )
    schema = fold_migrations(find_migration_files(root), root=root)
    assert {t.name for t in schema.tables} == {"a"}


def test_apply_migrations_reflects_drop_in_the_graph(tmp_path: Path) -> None:
    _write_migrations(
        tmp_path,
        {
            "001_init.sql": "CREATE TABLE orders (id INT, notes TEXT);",
            "002_drop.sql": "ALTER TABLE orders DROP COLUMN notes;",
        },
    )
    raw = RepoCodeExtractor().extract(tmp_path)
    # Per-file union still shows the dropped column...
    raw_fields = {n.id for n in raw.nodes if n.kind is NodeKind.FIELD}
    assert "sql:orders.notes" in raw_fields
    # ...but the folded current schema does not.
    folded = apply_migrations(raw, tmp_path)
    folded_fields = {n.id for n in folded.nodes if n.kind is NodeKind.FIELD}
    assert "sql:orders.id" in folded_fields
    assert "sql:orders.notes" not in folded_fields


def test_apply_migrations_is_noop_without_migration_dir(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT, notes TEXT);", encoding="utf-8")
    raw = RepoCodeExtractor().extract(tmp_path)
    out = apply_migrations(raw, tmp_path)
    assert {n.id for n in out.nodes if n.kind is NodeKind.FIELD} == {"sql:t.id", "sql:t.notes"}
    # entities + REFERENCES survive untouched
    assert any(e.kind is EdgeKind.CONTAINS for e in out.edges)
