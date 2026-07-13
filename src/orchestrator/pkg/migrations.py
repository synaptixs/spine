"""Migration-aware schema folding (SQL Track A, phase A4).

A migrations directory expresses the schema as an *ordered sequence* of DDL
files (``001_init.sql``, ``002_add_col.sql``, Flyway ``V1__init.sql``, …). The
per-file SQL extractor unions what each file adds — correct for ``CREATE`` and
``ADD COLUMN``, but it can't express ``DROP COLUMN`` / ``RENAME`` / ``DROP
TABLE`` (the fact graph is additive). So the "current schema" of a
migration-driven repo is only right if the files are **folded in order**.

:func:`fold_migrations` replays the ordered files into one authoritative
``DBSchema``; :func:`apply_migrations` swaps a repo's per-file SQL facts for the
folded ones. It is a no-op when no migrations directory is present.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, ForeignKey, sql_source_to_facts

_MIGRATION_DIRS = {"migration", "migrations", "migrate"}


def find_migration_files(root: Path) -> list[Path]:
    """Ordered ``.sql`` files that live under a migrations directory."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        parts = {p.lower() for p in Path(dirpath).parts}
        if parts & _MIGRATION_DIRS:
            found.extend(Path(dirpath) / n for n in filenames if n.endswith(".sql"))
    # Filename order = apply order (zero-padded serials and Flyway Vn__ both sort).
    return sorted(found, key=lambda p: (str(p.parent), p.name))


class _MutableTable:
    def __init__(self, name: str, provenance: Provenance) -> None:
        self.name = name
        self.provenance = provenance
        self.is_view = False
        self.columns: dict[str, DBColumn] = {}
        self.fks: list[ForeignKey] = []

    def to_table(self) -> DBTable:
        return DBTable(
            name=self.name,
            columns=tuple(self.columns.values()),
            foreign_keys=tuple(self.fks),
            is_view=self.is_view,
            provenance=self.provenance,
        )


def fold_migrations(paths: list[Path], *, root: Path, dialect: str | None = "postgres") -> DBSchema:
    """Replay ordered migration files into the resulting current ``DBSchema``."""
    import sqlglot
    from sqlglot import expressions as exp

    tables: dict[str, _MutableTable] = {}

    def rel_of(path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name

    for path in paths:
        rel = rel_of(path)
        try:
            statements = sqlglot.parse(
                path.read_text(encoding="utf-8"), dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE
            )
        except Exception:  # noqa: BLE001 — a broken migration must not abort the fold
            continue
        for stmt in statements:
            if stmt is None:
                continue
            prov = Provenance(rel, 1)
            if isinstance(stmt, exp.Create):
                _apply_create(stmt, prov, tables)
            elif isinstance(stmt, exp.Alter):
                _apply_alter(stmt, prov, tables)
            elif isinstance(stmt, exp.Drop) and (stmt.args.get("kind") or "").upper() == "TABLE":
                tbl = stmt.find(exp.Table)
                if tbl is not None:
                    tables.pop(tbl.name, None)

    return DBSchema(database="", tables=tuple(t.to_table() for t in tables.values()))


def _apply_create(stmt: object, prov: Provenance, tables: dict[str, _MutableTable]) -> None:
    from sqlglot import expressions as exp

    assert isinstance(stmt, exp.Create)
    kind = (stmt.kind or "").upper()
    if kind not in ("TABLE", "VIEW"):
        return
    tbl = stmt.find(exp.Table)
    if tbl is None:
        return
    mut = tables.setdefault(tbl.name, _MutableTable(tbl.name, prov))
    if kind == "VIEW":
        mut.is_view = True
        return
    for col in stmt.find_all(exp.ColumnDef):
        kind_node = col.args.get("kind")
        mut.columns[col.name] = DBColumn(
            name=col.name, type=kind_node.sql() if kind_node else "", provenance=prov
        )
        for constraint in col.constraints:
            if isinstance(constraint.kind, exp.Reference):
                ref = constraint.kind.find(exp.Table)
                if ref is not None:
                    mut.fks.append(ForeignKey(col.name, ref.name, provenance=prov))
    for fk in stmt.find_all(exp.ForeignKey):
        ref = fk.args.get("reference")
        ref_table = ref.find(exp.Table) if ref else None
        if ref_table is not None:
            for local in [e.name for e in fk.expressions] or [""]:
                mut.fks.append(ForeignKey(local, ref_table.name, provenance=prov))


def _apply_alter(stmt: object, prov: Provenance, tables: dict[str, _MutableTable]) -> None:
    from sqlglot import expressions as exp

    assert isinstance(stmt, exp.Alter)
    target = stmt.this.name if stmt.this else ""
    mut = tables.get(target)
    if mut is None:
        return
    for action in stmt.args.get("actions", []):
        if isinstance(action, exp.ColumnDef):  # ADD COLUMN
            kind_node = action.args.get("kind")
            mut.columns[action.name] = DBColumn(
                name=action.name, type=kind_node.sql() if kind_node else "", provenance=prov
            )
        elif isinstance(action, exp.Drop):  # DROP COLUMN
            col = action.find(exp.Column) or action.this
            name = getattr(col, "name", "")
            mut.columns.pop(name, None)
            mut.fks = [fk for fk in mut.fks if fk.column != name]
        elif isinstance(action, exp.RenameColumn):
            old = action.this.name if action.this else ""
            new = action.args["to"].name if action.args.get("to") else ""
            if old in mut.columns and new:
                existing = mut.columns.pop(old)
                mut.columns[new] = DBColumn(name=new, type=existing.type, provenance=prov)
                mut.fks = [
                    ForeignKey(new, fk.ref_table, fk.ref_column, fk.provenance) if fk.column == old else fk
                    for fk in mut.fks
                ]
        elif isinstance(action, exp.AlterRename):  # RENAME TABLE TO
            new_name = action.this.name if action.this else ""
            if new_name and target in tables:
                moved = tables.pop(target)
                moved.name = new_name
                tables[new_name] = moved
        elif isinstance(action, exp.AddConstraint):
            for fk in action.find_all(exp.ForeignKey):
                ref = fk.args.get("reference")
                ref_table = ref.find(exp.Table) if ref else None
                if ref_table is not None:
                    for local in [e.name for e in fk.expressions] or [""]:
                        mut.fks.append(ForeignKey(local, ref_table.name, provenance=prov))


def apply_migrations(batch: FactBatch, root: Path | str) -> FactBatch:
    """Replace a repo's per-file SQL facts with the ordered-fold current schema.

    No-op when there is no migrations directory. Non-SQL facts and SQL facts
    from files *outside* the migrations directory pass through untouched.
    """
    root_path = Path(root)
    files = find_migration_files(root_path)
    if not files:
        return batch

    migration_rels = {
        (f.resolve().relative_to(root_path.resolve()).as_posix() if _under(f, root_path) else f.name)
        for f in files
    }
    schema = fold_migrations(files, root=root_path)

    out = FactBatch()
    # Keep everything not sourced from a migration file (other languages, other
    # .sql files, module nodes). Drop the superseded per-file SQL entity/field
    # nodes + their edges so DROP/RENAME are reflected.
    for node in batch.nodes:
        if _sourced_from(node.provenance, migration_rels) and node.kind in (
            NodeKind.ENTITY,
            NodeKind.FIELD,
        ):
            continue
        out.add_node(node)
    for edge in batch.edges:
        if _sourced_from(edge.provenance, migration_rels):
            continue
        out.add_edge(edge)

    # Inject the authoritative folded schema under a synthetic migrations module.
    module_id = "sql:<migrations>"
    out.add_node(Node(module_id, NodeKind.MODULE, "migrations", "sql", Provenance("migrations", 1)))
    out.merge(sql_source_to_facts(schema, module_id=module_id))
    return out


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _sourced_from(prov: Provenance | None, rels: set[str]) -> bool:
    return prov is not None and prov.file in rels


__all__ = ["apply_migrations", "find_migration_files", "fold_migrations"]
