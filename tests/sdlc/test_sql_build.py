"""Ephemeral-DB SQL validation (SQL Track B, phase B1)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, ForeignKey  # noqa: E402
from orchestrator.sdlc.sql_build import apply_sql, validate_schema  # noqa: E402
from orchestrator.sdlc.testenv import make_test_environment, make_test_runner  # noqa: E402
from orchestrator.sdlc.testrunner import SqlTestRunner  # noqa: E402

_POSTGRES_DDL = """
CREATE TABLE customers (id SERIAL PRIMARY KEY, email VARCHAR(255) NOT NULL);
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  total NUMERIC(10, 2)
);
"""


def test_apply_transpiles_postgres_ddl_to_sqlite_and_introspects() -> None:
    result = apply_sql([_POSTGRES_DDL], dialect="postgres")
    assert result.ok, result.error
    assert result.schema is not None
    by_name = {t.name: t for t in result.schema.tables}
    assert set(by_name) == {"customers", "orders"}
    assert {c.name for c in by_name["orders"].columns} == {"id", "customer_id", "total"}
    # The foreign key survives the round-trip through SQLite introspection.
    assert any(fk.ref_table == "customers" for fk in by_name["orders"].foreign_keys)


def test_apply_reports_a_real_ddl_error() -> None:
    # orders references a table that is never created → SQLite rejects the FK DDL
    # only if it exists; a genuine error is a duplicate table.
    result = apply_sql(["CREATE TABLE t (id INT);", "CREATE TABLE t (id INT);"], dialect="postgres")
    assert not result.ok
    assert "t" in result.error.lower()


def test_validate_schema_flags_missing_pieces() -> None:
    applied = apply_sql([_POSTGRES_DDL], dialect="postgres").schema
    assert applied is not None
    expected = DBSchema(
        database="",
        tables=(
            DBTable(
                name="orders",
                columns=(DBColumn("id"), DBColumn("shipped_at")),  # shipped_at not generated
                foreign_keys=(ForeignKey("customer_id", "customers"),),
            ),
            DBTable(name="invoices", columns=(DBColumn("id"),)),  # table not generated
        ),
    )
    diff = validate_schema(applied, expected)
    assert not diff.ok
    assert "orders.shipped_at" in diff.missing_columns
    assert "invoices" in diff.missing_tables


def test_validate_schema_passes_when_expected_is_satisfied() -> None:
    applied = apply_sql([_POSTGRES_DDL], dialect="postgres").schema
    assert applied is not None
    expected = DBSchema(
        database="",
        tables=(
            DBTable(
                name="orders",
                columns=(DBColumn("id"), DBColumn("customer_id")),
                foreign_keys=(ForeignKey("customer_id", "customers"),),
            ),
        ),
    )
    assert validate_schema(applied, expected).ok


def test_sql_toolchain_is_always_available() -> None:
    from orchestrator.sdlc.testenv import sql_toolchain_available

    assert sql_toolchain_available() is True


def test_factories_wire_sql_language() -> None:
    env = make_test_environment("sql")
    assert env.describe().startswith("sql toolchain")
    runner = make_test_runner("sql", env)
    assert isinstance(runner, SqlTestRunner)


@pytest.mark.asyncio
async def test_sql_runner_passes_on_valid_migrations(tmp_path: Path) -> None:
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_init.sql").write_text("CREATE TABLE t (id SERIAL PRIMARY KEY);", encoding="utf-8")
    (mig / "002_add.sql").write_text("ALTER TABLE t ADD COLUMN note TEXT;", encoding="utf-8")
    result = await SqlTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output


@pytest.mark.asyncio
async def test_sql_runner_fails_on_broken_ddl(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE t (id INT);\nCREATE TABLE t (id INT);", encoding="utf-8"
    )
    result = await SqlTestRunner().run(path=str(tmp_path))
    assert not result.passed
    assert result.returncode == 1


# ---- B4: Postgres engine (opt-in, Docker-gated) -------------------------


def test_engine_env_selects_postgres_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import PostgresSqlTestRunner, SqlTestRunner

    env = make_test_environment("sql")
    monkeypatch.setenv("SDLC_SQL_ENGINE", "postgres")
    assert isinstance(make_test_runner("sql", env), PostgresSqlTestRunner)
    monkeypatch.setenv("SDLC_SQL_ENGINE", "sqlite")
    assert isinstance(make_test_runner("sql", env), SqlTestRunner)


@pytest.mark.asyncio
async def test_postgres_runner_is_graceful_without_toolchain(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    if _has_testcontainers():
        pytest.skip("testcontainers present — this asserts the missing-toolchain path")
    from orchestrator.sdlc.testrunner import PostgresSqlTestRunner

    (tmp_path / "001.sql").write_text("CREATE TABLE t (id INT);", encoding="utf-8")
    result = await PostgresSqlTestRunner().run(path=str(tmp_path))
    assert not result.passed
    assert "sql-postgres" in result.output  # actionable, not a crash


def _has_testcontainers() -> bool:
    import importlib.util

    return importlib.util.find_spec("testcontainers") is not None
