"""Database schema → PKG data-layer facts (Entity / Field nodes).

The code extractor models the *code*; this models the **data** — tables become
``Entity`` nodes and columns ``Field`` nodes, so the grounder can surface the
real schema for a data-shaped ticket ("add a column to orders", "query the
invoices table") instead of the agent guessing. Nodes carry a synthetic
``db://`` provenance so they count as grounded (and are therefore retrievable).

This is the universal target; the source is pluggable — an MCP DB server
(``orchestrator.mcp.db``) or a direct introspector both produce a ``DBSchema``.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance


@dataclass(frozen=True)
class DBColumn:
    name: str
    type: str = ""
    nullable: bool = True


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key: ``column`` in this table references ``ref_table``."""

    column: str
    ref_table: str
    ref_column: str = ""


@dataclass(frozen=True)
class DBTable:
    name: str
    columns: tuple[DBColumn, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()


@dataclass(frozen=True)
class DBSchema:
    database: str
    tables: tuple[DBTable, ...] = ()


def _provenance(database: str, *parts: str) -> Provenance:
    # Synthetic db:// locator (not a real file) so schema nodes are grounded.
    return Provenance(file="db://" + "/".join((database, *parts)), line=1)


def schema_to_facts(schema: DBSchema) -> FactBatch:
    """Project a ``DBSchema`` into ``Entity``/``Field`` nodes + CONTAINS edges."""
    batch = FactBatch()
    db = schema.database
    for table in schema.tables:
        entity_id = f"db:{db}.{table.name}"
        batch.add_node(
            Node(
                id=entity_id,
                kind=NodeKind.ENTITY,
                name=table.name,
                language="sql",
                provenance=_provenance(db, table.name),
            )
        )
        for column in table.columns:
            field_id = f"{entity_id}.{column.name}"
            batch.add_node(
                Node(
                    id=field_id,
                    kind=NodeKind.FIELD,
                    name=f"{table.name}.{column.name}",
                    language="sql",
                    provenance=_provenance(db, table.name, column.name),
                )
            )
            batch.add_edge(Edge(src=entity_id, dst=field_id, kind=EdgeKind.CONTAINS))

    # Foreign keys → Entity→Entity REFERENCES edges (second pass so both ends
    # exist). An FK to a table not in this schema is skipped (no dangling edge).
    table_names = {t.name for t in schema.tables}
    for table in schema.tables:
        src_id = f"db:{db}.{table.name}"
        for fk in table.foreign_keys:
            if fk.ref_table not in table_names:
                continue
            batch.add_edge(
                Edge(
                    src=src_id,
                    dst=f"db:{db}.{fk.ref_table}",
                    kind=EdgeKind.REFERENCES,
                    provenance=_provenance(db, table.name, fk.column),
                )
            )
    return batch


__all__ = ["DBColumn", "DBSchema", "DBTable", "ForeignKey", "schema_to_facts"]
