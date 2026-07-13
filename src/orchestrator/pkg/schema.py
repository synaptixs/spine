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
    # Source provenance (``file:line``) when the schema was parsed from ``.sql``
    # source rather than introspected from a live DB (which has no file).
    provenance: Provenance | None = None


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key: ``column`` in this table references ``ref_table``."""

    column: str
    ref_table: str
    ref_column: str = ""
    provenance: Provenance | None = None


@dataclass(frozen=True)
class DBTable:
    name: str
    columns: tuple[DBColumn, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    # A view is still a data ``Entity``; the flag lets renderers/A2 distinguish it.
    is_view: bool = False
    provenance: Provenance | None = None


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


def sql_source_to_facts(schema: DBSchema, *, module_id: str) -> FactBatch:
    """Project a **source-parsed** ``DBSchema`` (with file provenance) into facts.

    The sibling of :func:`schema_to_facts` (live-DB introspection, synthetic
    ``db://`` locators). Here ids are ``sql:<table>`` and every node points at
    the real ``.sql`` source, so schema nodes are blast-radius-retrievable like
    any other grounded symbol. ``CONTAINS`` runs from ``module_id`` (the file).

    Foreign keys emit ``REFERENCES`` unconditionally — a target defined in
    *another* file gets an ``external`` placeholder that that file's ``CREATE``
    upgrades in place when the batches merge (so cross-file FKs resolve without
    the single-schema ``skip`` that :func:`schema_to_facts` uses).
    """
    batch = FactBatch()

    def entity_id(table: str) -> str:
        return f"sql:{table}"

    for table in schema.tables:
        eid = entity_id(table.name)
        batch.add_node(
            Node(id=eid, kind=NodeKind.ENTITY, name=table.name, language="sql", provenance=table.provenance)
        )
        batch.add_edge(Edge(src=module_id, dst=eid, kind=EdgeKind.CONTAINS))
        for column in table.columns:
            fid = f"{eid}.{column.name}"
            batch.add_node(
                Node(
                    id=fid,
                    kind=NodeKind.FIELD,
                    name=f"{table.name}.{column.name}",
                    language="sql",
                    provenance=column.provenance,
                )
            )
            batch.add_edge(Edge(src=eid, dst=fid, kind=EdgeKind.CONTAINS))

    for table in schema.tables:
        src_id = entity_id(table.name)
        for fk in table.foreign_keys:
            dst_id = entity_id(fk.ref_table)
            # Placeholder for a target this file doesn't define; a grounded
            # CREATE elsewhere upgrades it (FactBatch prefers the grounded node).
            batch.add_node(
                Node(id=dst_id, kind=NodeKind.ENTITY, name=fk.ref_table, language="sql", external=True)
            )
            batch.add_edge(Edge(src=src_id, dst=dst_id, kind=EdgeKind.REFERENCES, provenance=fk.provenance))
    return batch


__all__ = [
    "DBColumn",
    "DBSchema",
    "DBTable",
    "ForeignKey",
    "schema_to_facts",
    "sql_source_to_facts",
]
