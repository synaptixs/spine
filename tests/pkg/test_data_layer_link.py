"""Cross-language data-layer linking (SQL Track A, phase A3)."""

from __future__ import annotations

from orchestrator.pkg.data_layer_link import link_data_layer
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance


def _entity(node_id: str, name: str, *, lang: str, file: str) -> Node:
    return Node(node_id, NodeKind.ENTITY, name, lang, Provenance(file, 1))


def _repo_with_sql_and_orm() -> FactBatch:
    b = FactBatch()
    # Source-SQL schema (grounded from .sql) — authoritative.
    b.add_node(_entity("sql:orders", "orders", lang="sql", file="schema.sql"))
    b.add_node(_entity("sql:customers", "customers", lang="sql", file="schema.sql"))
    b.add_edge(Edge("sql:orders", "sql:customers", EdgeKind.REFERENCES, Provenance("schema.sql", 3)))
    # ORM model (C# EF) describing the same two tables, with an inferred FK.
    b.add_node(_entity("csharp:entity:App.Order", "Order", lang="csharp", file="Order.cs"))
    b.add_node(_entity("csharp:entity:App.Customer", "Customer", lang="csharp", file="Customer.cs"))
    b.add_edge(Edge("csharp:module.Order", "csharp:entity:App.Order", EdgeKind.CONTAINS))
    b.add_edge(
        Edge(
            "csharp:entity:App.Order",
            "csharp:entity:App.Customer",
            EdgeKind.REFERENCES,
            Provenance("Order.cs", 8),
        )
    )
    return b


def test_orm_entities_collapse_onto_sql_entities() -> None:
    linked = link_data_layer(_repo_with_sql_and_orm())
    entity_ids = {n.id for n in linked.nodes if n.kind is NodeKind.ENTITY}
    # One entity per table — the ORM duplicates are gone, SQL is canonical.
    assert entity_ids == {"sql:orders", "sql:customers"}


def test_schema_foreign_key_wins_over_inferred() -> None:
    linked = link_data_layer(_repo_with_sql_and_orm())
    refs = [e for e in linked.edges if e.kind is EdgeKind.REFERENCES]
    # Exactly one orders→customers reference, and it's the .sql-grounded one.
    orders_refs = [e for e in refs if e.src == "sql:orders" and e.dst == "sql:customers"]
    assert len(orders_refs) == 1
    assert orders_refs[0].provenance is not None
    assert orders_refs[0].provenance.file.endswith(".sql")


def test_contains_to_merged_orm_entity_is_dropped() -> None:
    linked = link_data_layer(_repo_with_sql_and_orm())
    # The C# module's CONTAINS pointed at the merged ORM entity → dropped (the
    # sql module owns the canonical node), so no cross-language CONTAINS noise.
    contains = [e for e in linked.edges if e.kind is EdgeKind.CONTAINS]
    assert all(not e.dst.startswith("csharp:entity:") for e in contains)
    assert all(e.dst != "sql:orders" or e.src.startswith("sql:") for e in contains)


def test_noop_without_sql_schema() -> None:
    b = FactBatch()
    b.add_node(_entity("csharp:entity:App.Order", "Order", lang="csharp", file="Order.cs"))
    linked = link_data_layer(b)
    assert {n.id for n in linked.nodes} == {"csharp:entity:App.Order"}


def test_unmatched_orm_entity_is_left_alone() -> None:
    b = FactBatch()
    b.add_node(_entity("sql:orders", "orders", lang="sql", file="schema.sql"))
    b.add_node(_entity("csharp:entity:App.Widget", "Widget", lang="csharp", file="Widget.cs"))
    linked = link_data_layer(b)
    ids = {n.id for n in linked.nodes}
    assert ids == {"sql:orders", "csharp:entity:App.Widget"}  # no match → untouched
