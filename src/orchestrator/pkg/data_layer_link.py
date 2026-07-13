"""Cross-language data-layer linking (SQL Track A, phase A3).

The code extractors infer a data model from ORM classes (e.g. C# EF Core →
``csharp:entity:…`` nodes with *inferred* ``REFERENCES``). The SQL front-end
extracts the **real** schema from ``.sql`` source (``sql:<table>`` nodes with
source-grounded foreign keys). When both are present in one repo they describe
the *same* tables twice.

:func:`link_data_layer` reconciles them: where an ORM entity and a source-SQL
entity denote the same table (matched by a normalized name), it collapses the
ORM entity onto the SQL node — the schema is **authoritative** — and re-points
the ORM entity's edges at it, preferring the schema's grounded foreign keys over
the ORM's inferred ones. It is a no-op when a repo has no ``.sql`` schema, so
wiring it into the comprehension path is safe for every other project.
"""

from __future__ import annotations

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind

_SQL_PREFIX = "sql:"


def _normalize(name: str) -> str:
    """Fold ``Order`` / ``orders`` / ``order_items`` to a comparable key."""
    key = name.lower().replace("_", "").replace(" ", "")
    if len(key) > 3 and key.endswith("s"):  # naive singularize; enough to pair table↔model
        key = key[:-1]
    return key


def _is_sql_entity(node: Node) -> bool:
    return node.kind is NodeKind.ENTITY and node.id.startswith(_SQL_PREFIX)


def _is_orm_entity(node: Node) -> bool:
    return node.kind is NodeKind.ENTITY and not node.id.startswith(_SQL_PREFIX)


def link_data_layer(batch: FactBatch) -> FactBatch:
    """Return a batch with ORM entities merged onto matching source-SQL entities.

    Unmatched repos (no SQL entities, or no name matches) come back unchanged.
    """
    sql_by_norm: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in batch.nodes:
        if _is_sql_entity(node) and node.grounded:
            norm = _normalize(node.name)
            if norm in sql_by_norm and sql_by_norm[norm] != node.id:
                ambiguous.add(norm)  # two schema tables fold to one key — don't guess
            sql_by_norm.setdefault(norm, node.id)
    if not sql_by_norm:
        return batch

    # ORM entity id → canonical SQL entity id.
    remap: dict[str, str] = {}
    for node in batch.nodes:
        if _is_orm_entity(node):
            norm = _normalize(node.name)
            if norm in sql_by_norm and norm not in ambiguous:
                remap[node.id] = sql_by_norm[norm]
    if not remap:
        return batch

    linked = FactBatch()
    for node in batch.nodes:
        if node.id in remap:
            continue  # merged into the canonical SQL entity
        linked.add_node(node)

    # Remap edges; drop CONTAINS pointing at a merged ORM entity (the SQL module
    # already CONTAINS the canonical node). Prefer schema-grounded REFERENCES.
    references: dict[tuple[str, str], Edge] = {}
    for edge in batch.edges:
        src = remap.get(edge.src, edge.src)
        dst = remap.get(edge.dst, edge.dst)
        if src == dst:
            continue  # a self-reference created by the merge is noise
        if edge.kind is EdgeKind.CONTAINS and edge.dst in remap:
            continue
        moved = Edge(src=src, dst=dst, kind=edge.kind, provenance=edge.provenance)
        if edge.kind is EdgeKind.REFERENCES:
            key = (src, dst)
            existing = references.get(key)
            if existing is None or _prefer(moved, existing):
                references[key] = moved
            continue
        linked.add_edge(moved)
    for edge in references.values():
        linked.add_edge(edge)
    return linked


def _prefer(candidate: Edge, existing: Edge) -> bool:
    """Prefer a foreign key grounded in ``.sql`` source over an inferred one."""
    cand_sql = bool(candidate.provenance and candidate.provenance.file.endswith(".sql"))
    exist_sql = bool(existing.provenance and existing.provenance.file.endswith(".sql"))
    return cand_sql and not exist_sql


__all__ = ["link_data_layer"]
