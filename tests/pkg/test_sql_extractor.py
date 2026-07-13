"""SQL DDL → PKG facts (Track A, phase A1). Requires the ``sql`` extra."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402
from orchestrator.pkg.sql_extractor import SqlExtractor  # noqa: E402

_SCHEMA = """\
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    name TEXT
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    total NUMERIC(10, 2),
    CONSTRAINT fk_status FOREIGN KEY (status_id) REFERENCES statuses(id)
);
"""


def _extract(tmp_path: Path, sql: str, name: str = "schema.sql") -> FactBatch:
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    return SqlExtractor().extract(path=path, module=name, rel=name)


def test_create_table_emits_entities_fields_and_contains(tmp_path: Path) -> None:
    batch = _extract(tmp_path, _SCHEMA)
    by_id = {n.id: n for n in batch.nodes}

    assert by_id["sql:customers"].kind is NodeKind.ENTITY
    assert by_id["sql:customers"].language == "sql"
    assert by_id["sql:orders.total"].kind is NodeKind.FIELD

    counts = batch.counts()
    assert counts["Entity"] == 3  # customers, orders, + statuses placeholder (external)
    # 3 customer + 3 order columns (id/customer_id/total) = 6 fields
    assert counts["Field"] == 6

    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("sql:module.sql", "sql:customers") not in contains  # module id is the rel name
    assert ("sql:customers", "sql:customers.email") in contains


def test_provenance_points_at_source_lines(tmp_path: Path) -> None:
    batch = _extract(tmp_path, _SCHEMA)
    by_id = {n.id: n for n in batch.nodes}
    # customers CREATE is line 1, orders CREATE is line 7.
    customers_prov = by_id["sql:customers"].provenance
    orders_prov = by_id["sql:orders"].provenance
    assert customers_prov is not None and customers_prov.line == 1
    assert orders_prov is not None and orders_prov.line == 7
    assert by_id["sql:customers"].grounded  # real file:line → grounded


def test_column_and_table_level_foreign_keys(tmp_path: Path) -> None:
    batch = _extract(tmp_path, _SCHEMA)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("sql:orders", "sql:customers") in refs  # column-level `REFERENCES customers(id)`
    assert ("sql:orders", "sql:statuses") in refs  # table-level CONSTRAINT ... FOREIGN KEY


def test_alter_table_add_column_folds_into_table(tmp_path: Path) -> None:
    sql = "CREATE TABLE t (id INT);\nALTER TABLE t ADD COLUMN note TEXT;\n"
    batch = _extract(tmp_path, sql)
    field_ids = {n.id for n in batch.nodes if n.kind is NodeKind.FIELD}
    assert field_ids == {"sql:t.id", "sql:t.note"}


def test_alter_table_add_foreign_key(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE a (id INT);\n"
        "CREATE TABLE b (id INT);\n"
        "ALTER TABLE a ADD CONSTRAINT fk FOREIGN KEY (b_id) REFERENCES b(id);\n"
    )
    batch = _extract(tmp_path, sql)
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("sql:a", "sql:b") in refs


def test_create_view_is_an_entity(tmp_path: Path) -> None:
    sql = "CREATE TABLE t (id INT);\nCREATE VIEW v AS SELECT id FROM t;\n"
    batch = _extract(tmp_path, sql)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["sql:v"].kind is NodeKind.ENTITY


# ---- A2: DML + views + stored procedures --------------------------------


def test_view_reads_its_base_tables(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE orders (id INT, total INT);\n"
        "CREATE VIEW big AS SELECT id FROM orders WHERE total > 1;\n"
    )
    batch = _extract(tmp_path, sql)
    reads = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.READS}
    assert ("sql:big", "sql:orders") in reads


def test_standalone_dml_reads_and_writes_attributed_to_the_file(tmp_path: Path) -> None:
    sql = (
        "INSERT INTO audit (msg) SELECT id FROM orders;\n"
        "UPDATE customers SET active = 0;\n"
        "DELETE FROM sessions WHERE expired;\n"
    )
    batch = _extract(tmp_path, sql, name="migrate.sql")
    module = "sql:migrate.sql"
    writes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES}
    reads = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.READS}
    assert (module, "sql:audit") in writes  # INSERT target
    assert (module, "sql:orders") in reads  # INSERT ... SELECT source
    assert (module, "sql:customers") in writes  # UPDATE target
    assert (module, "sql:sessions") in writes  # DELETE target


def test_stored_procedure_emits_function_with_body_edges(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE orders (id INT, total INT);\n"
        "CREATE PROCEDURE recompute() LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "  UPDATE orders SET total = 0;\n"
        "  INSERT INTO log(id) VALUES (1);\n"
        "  CALL notify();\n"
        "END $$;\n"
    )
    batch = _extract(tmp_path, sql)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["sql:recompute"].kind is NodeKind.FUNCTION
    assert by_id["sql:recompute"].grounded  # defined here → real provenance

    writes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.WRITES}
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("sql:recompute", "sql:orders") in writes  # UPDATE in the body
    assert ("sql:recompute", "sql:log") in writes  # INSERT in the body
    assert ("sql:recompute", "sql:notify") in calls  # CALL in the body


def test_cross_file_fk_resolves_to_grounded_entity(tmp_path: Path) -> None:
    # customers defined in one file, referenced from another — the placeholder
    # in orders.sql must upgrade to the grounded node from customers.sql.
    (tmp_path / "customers.sql").write_text("CREATE TABLE customers (id INT);\n", encoding="utf-8")
    (tmp_path / "orders.sql").write_text(
        "CREATE TABLE orders (id INT, customer_id INT REFERENCES customers(id));\n", encoding="utf-8"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    customers = next(n for n in batch.nodes if n.id == "sql:customers")
    assert customers.grounded  # upgraded from external placeholder → real CREATE
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    assert ("sql:orders", "sql:customers") in refs


def test_sql_extractor_registered_when_sqlglot_present() -> None:
    from orchestrator.pkg.extractor import default_extractors

    assert any(isinstance(e, SqlExtractor) for e in default_extractors())


def test_unparseable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "ok.sql").write_text("CREATE TABLE t (id INT);\n", encoding="utf-8")
    # sqlglot with IGNORE tolerates most junk; a genuinely broken token stream
    # must not abort the whole repo extraction.
    (tmp_path / "broken.sql").write_text("CREATE TABLE (((( ;;;; ))))\n", encoding="utf-8")
    extractor = RepoCodeExtractor()
    batch = extractor.extract(tmp_path)
    assert any(n.id == "sql:t" for n in batch.nodes)  # the good file still extracted
