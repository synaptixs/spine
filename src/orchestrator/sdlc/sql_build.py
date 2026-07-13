"""Ephemeral-database validation for SQL codegen (SQL Track B, phase B1).

SQL has no ``pytest``/``ctest`` analogue, so "testing" generated DDL means
*applying it to a throwaway database and inspecting the result*. This module is
that primitive:

  * :func:`apply_sql` transpiles DDL (any sqlglot-supported dialect) to SQLite
    and applies it to an **in-memory** database — zero external toolchain, since
    ``sqlite3`` is in the standard library.
  * :func:`introspect_sqlite` reads the applied schema back into the same
    ``DBSchema`` the comprehension side uses, so "did we build the intended
    tables?" is checkable with existing machinery.
  * :func:`validate_schema` diffs the applied schema against an expected one
    (the intent) into a pass/fail with the specific gaps.

testcontainers-backed Postgres for dialect-specific fidelity is B4; SQLite in
memory is the always-available default.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, ForeignKey


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying DDL to the ephemeral database."""

    ok: bool
    applied: int = 0
    error: str = ""
    schema: DBSchema | None = None


def apply_sql(texts: list[str], *, dialect: str = "postgres") -> ApplyResult:
    """Transpile ``texts`` to SQLite, apply in order to an in-memory DB, introspect.

    Any single statement that fails to *apply* (a real DDL error — bad
    reference, duplicate table) fails the whole run with the DB error, which is
    exactly the feedback signal a refine loop needs. Statements sqlglot can't
    transpile are skipped (``error_level=IGNORE``), never fatal.
    """
    import sqlglot

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        applied = 0
        for text in texts:
            statements = sqlglot.transpile(
                text, read=dialect, write="sqlite", error_level=sqlglot.ErrorLevel.IGNORE
            )
            for statement in statements:
                sql = statement.strip()
                if not sql:
                    continue
                try:
                    conn.execute(sql)
                except sqlite3.Error as err:
                    return ApplyResult(ok=False, applied=applied, error=f"{err} — while applying: {sql}")
                applied += 1
        return ApplyResult(ok=True, applied=applied, schema=introspect_sqlite(conn))
    finally:
        conn.close()


def introspect_sqlite(conn: sqlite3.Connection) -> DBSchema:
    """Read a live SQLite connection's schema back into a ``DBSchema``."""
    tables: list[DBTable] = []
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for name, obj_type in rows:
        columns = tuple(
            DBColumn(name=str(r[1]), type=str(r[2] or ""), nullable=not r[3])
            for r in conn.execute(f'PRAGMA table_info("{name}")')
        )
        foreign_keys = tuple(
            ForeignKey(column=str(r[3]), ref_table=str(r[2]), ref_column=str(r[4] or ""))
            for r in conn.execute(f'PRAGMA foreign_key_list("{name}")')
        )
        tables.append(
            DBTable(name=str(name), columns=columns, foreign_keys=foreign_keys, is_view=obj_type == "view")
        )
    return DBSchema(database="", tables=tuple(tables))


@dataclass(frozen=True)
class SchemaDiff:
    """What the applied schema is missing versus the expected one."""

    ok: bool
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    missing_references: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "schema matches the expected shape"
        parts: list[str] = []
        if self.missing_tables:
            parts.append(f"missing tables: {', '.join(self.missing_tables)}")
        if self.missing_columns:
            parts.append(f"missing columns: {', '.join(self.missing_columns)}")
        if self.missing_references:
            parts.append(f"missing foreign keys: {', '.join(self.missing_references)}")
        return "; ".join(parts)


def validate_schema(applied: DBSchema, expected: DBSchema) -> SchemaDiff:
    """Check the applied schema satisfies ``expected`` (tables/columns/FKs).

    Case-insensitive and additive: the applied schema may have *more* than
    expected (extra columns are fine); it must not be *missing* anything.
    """
    by_name = {t.name.lower(): t for t in applied.tables}
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    missing_references: list[str] = []

    for want in expected.tables:
        have = by_name.get(want.name.lower())
        if have is None:
            missing_tables.append(want.name)
            continue
        have_cols = {c.name.lower() for c in have.columns}
        for col in want.columns:
            if col.name.lower() not in have_cols:
                missing_columns.append(f"{want.name}.{col.name}")
        have_refs = {fk.ref_table.lower() for fk in have.foreign_keys}
        for fk in want.foreign_keys:
            if fk.ref_table.lower() not in have_refs:
                missing_references.append(f"{want.name} → {fk.ref_table}")

    ok = not (missing_tables or missing_columns or missing_references)
    return SchemaDiff(
        ok=ok,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        missing_references=missing_references,
    )


def apply_sql_postgres(texts: list[str], dsn: str, *, dialect: str = "postgres") -> ApplyResult:
    """Apply DDL to a real Postgres (via ``dsn``) for dialect fidelity (B4).

    Unlike the SQLite path this does NOT transpile — Postgres-native DDL applies
    as written (transpiling only when the source dialect differs). Requires
    ``psycopg`` (the ``sql-postgres`` extra); the caller owns the database (e.g.
    a testcontainers instance). Schema is not introspected here (the applied
    Postgres catalog differs from SQLite's PRAGMA shape); ``ok``/``error`` is the
    apply signal the refine loop needs.
    """
    import psycopg  # lazy: only when the postgres engine is actually used
    import sqlglot

    # Split each file into individual statements and re-emit as Postgres (a
    # near-identity transpile when the source already IS postgres) — psycopg's
    # execute() runs one statement per call, so a multi-statement file must be split.
    statements: list[str] = []
    for text in texts:
        statements.extend(
            sqlglot.transpile(text, read=dialect, write="postgres", error_level=sqlglot.ErrorLevel.IGNORE)
        )

    applied = 0
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for statement in statements:
                sql = statement.strip().rstrip(";")
                if not sql:
                    continue
                try:
                    conn.execute(sql)
                except psycopg.Error as err:
                    return ApplyResult(ok=False, applied=applied, error=f"{err} — while applying: {sql}")
                applied += 1
    except psycopg.Error as err:
        return ApplyResult(ok=False, applied=applied, error=str(err))
    return ApplyResult(ok=True, applied=applied)


__all__ = [
    "ApplyResult",
    "SchemaDiff",
    "apply_sql",
    "apply_sql_postgres",
    "introspect_sqlite",
    "validate_schema",
]
