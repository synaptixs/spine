"""SQL front-end for the PKG extractor (the 7th language) — Track A.

Maps SQL onto the same universal ``facts`` vocabulary the code extractors use,
so the data layer joins the knowledge graph as first-class nodes. Unlike the
live-DB introspector (``pkg/schema.py``, synthetic ``db://`` locators), this
reads ``.sql`` *source*, so every node carries real ``file:line`` provenance and
is blast-radius retrievable.

Parsing is via `sqlglot <https://github.com/tobymao/sqlglot>`_ (pure-Python,
multi-dialect DDL+DML AST), an OPTIONAL dependency behind the ``sql`` extra
(``pip install 'synaptixs-spine[sql]'``). The import is lazy so the base
install stays dependency-free and importing this module never fails.

Scope:
  * **A1 (DDL):** ``CREATE TABLE`` (columns, primary keys, column- and
    table-level foreign keys) → ``Entity``/``Field``/``REFERENCES``; same-file
    ``ALTER TABLE ADD COLUMN``/``ADD CONSTRAINT``; ``CREATE VIEW`` → ``Entity``.
  * **A2 (DML + routines):** standalone ``SELECT``/``INSERT``/``UPDATE``/
    ``DELETE`` and view bodies → ``READS``/``WRITES``; ``CREATE FUNCTION``/
    ``PROCEDURE`` → ``Function`` nodes, with ``READS``/``WRITES``/``CALLS`` from
    the (opaque-to-sqlglot) routine body, re-parsed best-effort.

Cross-file migration folding (drops/renames across ordered files) is A4; this
front-end folds only *within* a file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, ForeignKey, sql_source_to_facts

if TYPE_CHECKING:
    from sqlglot import expressions as exp

# CALL / PERFORM invocations become opaque ``Command`` nodes in sqlglot, so the
# callee is recovered by a narrow regex over the routine body as a fallback.
_CALL_RE = re.compile(r"\b(?:CALL|PERFORM)\s+([A-Za-z_][\w.]*)\s*\(", re.IGNORECASE)
_LEADING_BEGIN_RE = re.compile(r"^\s*(?:DECLARE\b.*?)?\bBEGIN\b", re.IGNORECASE | re.DOTALL)
_TRAILING_END_RE = re.compile(r"\bEND\b\s*;?\s*$", re.IGNORECASE)


class SqlExtractor:
    """SQL front-end (sqlglot). Install the ``sql`` extra to use it."""

    language: str = "sql"
    suffixes: tuple[str, ...] = (".sql",)

    def __init__(self, dialect: str | None = "postgres") -> None:
        # Default to Postgres: it's the most common dialect and, unlike the
        # generic tokenizer, understands dollar-quoted (``$$…$$``) routine
        # bodies — essential for A2 procedure parsing. A repo on another dialect
        # (MySQL back-ticks, T-SQL brackets) can override; unparseable
        # statements degrade to a skip, never a crash (``error_level=IGNORE``).
        self._dialect = dialect

    def module_name(self, path: Path, root: Path) -> str:
        # A .sql file has no package declaration; the module is its repo path.
        return rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        import logging

        import sqlglot
        from sqlglot import expressions as exp
        from sqlglot.errors import ParseError, TokenError

        # We parse with error_level=IGNORE on purpose, so sqlglot's "falling back
        # to Command" warnings for unsupported (often procedural) syntax are
        # expected noise — keep them out of CLI/extraction output.
        logging.getLogger("sqlglot").setLevel(logging.ERROR)

        text = path.read_text(encoding="utf-8")
        try:
            statements = sqlglot.parse(text, dialect=self._dialect, error_level=sqlglot.ErrorLevel.IGNORE)
            start_lines = _statement_start_lines(text, self._dialect)
        except (ParseError, TokenError) as err:
            # A file sqlglot can't tokenise at all → skipped, per the
            # RepoCodeExtractor contract (it catches ValueError).
            raise ValueError(f"sqlglot could not parse {rel}: {err}") from err

        module_id = f"sql:{module}" if module else "sql:<root>"
        batch = FactBatch()
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "sql", Provenance(rel, 1)))

        # DDL folds into one table map so a same-file ALTER augments its CREATE.
        tables: dict[str, _TableBuilder] = {}
        for stmt, line in zip(_non_null(statements), _aligned_lines(statements, start_lines), strict=False):
            prov = Provenance(rel, line)
            if isinstance(stmt, exp.Create):
                self._handle_create(stmt, prov, module_id, tables, batch)
            elif isinstance(stmt, exp.Alter):
                self._handle_alter(stmt, prov, tables)
            elif isinstance(stmt, exp.Select | exp.Insert | exp.Update | exp.Delete):
                # A top-level query/DML: the file (module) is the reader/writer.
                self._emit_data_access(stmt, module_id, prov, batch)

        schema = DBSchema(database="", tables=tuple(b.build() for b in tables.values()))
        batch.merge(sql_source_to_facts(schema, module_id=module_id))
        return batch

    # ---- DDL (A1) ----------------------------------------------------------

    def _handle_create(
        self,
        stmt: exp.Create,
        prov: Provenance,
        module_id: str,
        tables: dict[str, _TableBuilder],
        batch: FactBatch,
    ) -> None:

        kind = (stmt.kind or "").upper()
        if kind == "TABLE":
            self._handle_create_table(stmt, prov, tables)
        elif kind == "VIEW":
            self._handle_create_view(stmt, prov, tables, batch)
        elif kind in ("FUNCTION", "PROCEDURE"):
            self._handle_create_routine(stmt, prov, module_id, batch)

    def _handle_create_table(
        self, stmt: exp.Create, prov: Provenance, tables: dict[str, _TableBuilder]
    ) -> None:
        from sqlglot import expressions as exp

        tbl = stmt.find(exp.Table)
        if tbl is None:
            return
        builder = tables.setdefault(tbl.name, _TableBuilder(name=tbl.name, provenance=prov))
        builder.provenance = builder.provenance or prov
        for col in stmt.find_all(exp.ColumnDef):
            builder.add_column(col.name, _type_sql(col), prov)
            for constraint in col.constraints:
                if isinstance(constraint.kind, exp.Reference):
                    ref = constraint.kind.find(exp.Table)
                    if ref is not None:
                        builder.add_fk(col.name, ref.name, prov)
        for fk in stmt.find_all(exp.ForeignKey):
            self._record_table_fk(fk, prov, builder)

    def _handle_create_view(
        self, stmt: exp.Create, prov: Provenance, tables: dict[str, _TableBuilder], batch: FactBatch
    ) -> None:
        from sqlglot import expressions as exp

        tbl = stmt.find(exp.Table)
        if tbl is None:
            return
        builder = tables.setdefault(tbl.name, _TableBuilder(name=tbl.name, provenance=prov))
        builder.provenance = builder.provenance or prov
        builder.is_view = True
        # A2: the view READS its base tables (from the defining SELECT).
        select = stmt.find(exp.Select)
        if select is not None:
            for base in _select_tables(select):
                if base != tbl.name:
                    _touch_entity(batch, base, external=True)
                    batch.add_edge(Edge(f"sql:{tbl.name}", f"sql:{base}", EdgeKind.READS, prov))

    def _handle_alter(self, stmt: exp.Alter, prov: Provenance, tables: dict[str, _TableBuilder]) -> None:
        from sqlglot import expressions as exp

        target = stmt.this.name if stmt.this else ""
        if not target:
            return
        builder = tables.setdefault(target, _TableBuilder(name=target, provenance=prov))
        for action in stmt.args.get("actions", []):
            if isinstance(action, exp.ColumnDef):
                builder.add_column(action.name, _type_sql(action), prov)
            elif isinstance(action, exp.AddConstraint):
                for fk in action.find_all(exp.ForeignKey):
                    self._record_table_fk(fk, prov, builder)

    def _record_table_fk(self, fk: exp.ForeignKey, prov: Provenance, builder: _TableBuilder) -> None:
        from sqlglot import expressions as exp

        ref = fk.args.get("reference")
        ref_table = ref.find(exp.Table) if ref else None
        if ref_table is None:
            return
        for col in [e.name for e in fk.expressions] or [""]:
            builder.add_fk(col, ref_table.name, prov)

    # ---- routines + DML (A2) ----------------------------------------------

    def _handle_create_routine(
        self, stmt: exp.Create, prov: Provenance, module_id: str, batch: FactBatch
    ) -> None:
        from sqlglot import expressions as exp

        tbl = stmt.find(exp.Table)  # sqlglot models the routine name as a Table
        if tbl is None:
            return
        func_id = f"sql:{tbl.name}"
        batch.add_node(Node(func_id, NodeKind.FUNCTION, tbl.name, "sql", prov))
        batch.add_edge(Edge(module_id, func_id, EdgeKind.CONTAINS, prov))

        body = _routine_body(stmt)
        if not body:
            return
        for sub in _reparse_body(body, self._dialect):
            if isinstance(sub, exp.Select | exp.Insert | exp.Update | exp.Delete):
                self._emit_data_access(sub, func_id, prov, batch)
            for anon in sub.find_all(exp.Anonymous):  # SELECT foo() style calls
                _touch_function(batch, anon.name)
                batch.add_edge(Edge(func_id, f"sql:{anon.name}", EdgeKind.CALLS, prov))
        for callee in _CALL_RE.findall(body):  # CALL/PERFORM proc();
            name = callee.split(".")[-1]
            _touch_function(batch, name)
            batch.add_edge(Edge(func_id, f"sql:{name}", EdgeKind.CALLS, prov))

    def _emit_data_access(
        self, stmt: exp.Expression, caller_id: str, prov: Provenance, batch: FactBatch
    ) -> None:
        writes, reads = _classify_tables(stmt)
        for table in writes:
            _touch_entity(batch, table, external=True)
            batch.add_edge(Edge(caller_id, f"sql:{table}", EdgeKind.WRITES, prov))
        for table in reads:
            _touch_entity(batch, table, external=True)
            batch.add_edge(Edge(caller_id, f"sql:{table}", EdgeKind.READS, prov))


class _TableBuilder:
    """Accumulates a table's columns/FKs across the CREATE + same-file ALTERs."""

    def __init__(self, name: str, provenance: Provenance | None = None) -> None:
        self.name = name
        self.provenance = provenance
        self.is_view = False
        self._columns: dict[str, DBColumn] = {}
        self._fks: list[ForeignKey] = []

    def add_column(self, name: str, type_sql: str, prov: Provenance) -> None:
        if name and name not in self._columns:
            self._columns[name] = DBColumn(name=name, type=type_sql, provenance=prov)

    def add_fk(self, column: str, ref_table: str, prov: Provenance) -> None:
        self._fks.append(ForeignKey(column=column, ref_table=ref_table, provenance=prov))

    def build(self) -> DBTable:
        return DBTable(
            name=self.name,
            columns=tuple(self._columns.values()),
            foreign_keys=tuple(self._fks),
            is_view=self.is_view,
            provenance=self.provenance,
        )


# ---- module-level helpers --------------------------------------------------


def _type_sql(col: exp.ColumnDef) -> str:
    kind = col.args.get("kind")
    return kind.sql() if kind else ""


def _touch_entity(batch: FactBatch, table: str, *, external: bool) -> None:
    """Ensure a data-access target has an ``Entity`` node (upgraded if defined)."""
    batch.add_node(Node(f"sql:{table}", NodeKind.ENTITY, table, "sql", external=external))


def _touch_function(batch: FactBatch, name: str) -> None:
    batch.add_node(Node(f"sql:{name}", NodeKind.FUNCTION, name, "sql", external=True))


def _select_tables(select: exp.Select) -> set[str]:
    from sqlglot import expressions as exp

    return {t.name for t in select.find_all(exp.Table)}


def _classify_tables(stmt: exp.Expression) -> tuple[set[str], set[str]]:
    """``(writes, reads)`` table names — the target of a write vs. sources read."""
    from sqlglot import expressions as exp

    all_tables = {t.name for t in stmt.find_all(exp.Table)}
    if isinstance(stmt, exp.Insert | exp.Update | exp.Delete):
        target = stmt.this.find(exp.Table) if stmt.this else None
        writes = {target.name} if target is not None else set()
        return writes, all_tables - writes
    return set(), all_tables


def _routine_body(stmt: exp.Create) -> str:
    """The routine's body text (``$$…$$`` heredoc or a quoted string)."""
    from sqlglot import expressions as exp

    expr = stmt.args.get("expression")
    if expr is None:
        return ""
    if isinstance(expr, exp.Literal):
        return str(expr.this)
    raw = str(expr.sql(dialect="postgres")).strip()
    if raw.startswith("$$") and raw.endswith("$$"):
        return raw[2:-2]
    if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw


def _reparse_body(body: str, dialect: str | None) -> list[Any]:
    """Re-parse a routine body's embedded statements (best-effort).

    Procedural wrappers (``BEGIN … END``) are stripped so the first real
    statement isn't swallowed by the transaction keyword; anything that doesn't
    parse is silently dropped (procedural constructs sqlglot can't model).
    """
    import sqlglot

    inner = _TRAILING_END_RE.sub("", _LEADING_BEGIN_RE.sub("", body))
    try:
        return [s for s in sqlglot.parse(inner, dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE) if s]
    except Exception:  # noqa: BLE001 — a broken body must never fail the file
        return []


def _statement_start_lines(sql: str, dialect: str | None) -> list[int]:
    """1-based start line of each statement, split on the tokenizer's semicolons."""
    import sqlglot
    from sqlglot.tokens import TokenType  # type: ignore[attr-defined]

    lines: list[int] = []
    current: int | None = None
    for token in sqlglot.tokenize(sql, dialect=dialect):
        if current is None:
            current = token.line
        if token.token_type == TokenType.SEMICOLON:
            lines.append(current)
            current = None
    if current is not None:
        lines.append(current)
    return lines


def _non_null(statements: list[Any]) -> list[Any]:
    return [s for s in statements if s is not None]


def _aligned_lines(statements: list[Any], start_lines: list[int]) -> list[int]:
    """Line per non-null statement; pad with 1 if the token split disagrees."""
    kept = _non_null(statements)
    if len(start_lines) >= len(kept):
        return start_lines[: len(kept)]
    return start_lines + [1] * (len(kept) - len(start_lines))


__all__ = ["SqlExtractor"]
