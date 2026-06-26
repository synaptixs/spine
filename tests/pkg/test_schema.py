"""DB schema → PKG data-layer facts, and the grounder surfacing them."""

from __future__ import annotations

from orchestrator.pkg import FactStore, GroundedRetriever
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, schema_to_facts

_SCHEMA = DBSchema(
    database="app",
    tables=(
        DBTable(name="orders", columns=(DBColumn("id", "integer", False), DBColumn("total", "numeric"))),
        DBTable(name="customers", columns=(DBColumn("email", "text", False),)),
    ),
)


def test_schema_to_facts_emits_entities_fields_and_contains() -> None:
    batch = schema_to_facts(_SCHEMA)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["db:app.orders"].kind is NodeKind.ENTITY
    assert by_id["db:app.orders.total"].kind is NodeKind.FIELD
    assert by_id["db:app.orders"].grounded  # synthetic db:// provenance → grounded
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("db:app.orders", "db:app.orders.id") in contains
    counts = batch.counts()
    assert counts["Entity"] == 2 and counts["Field"] == 3


def test_grounder_surfaces_db_entity_for_a_data_spec() -> None:
    retriever = GroundedRetriever(FactStore(schema_to_facts(_SCHEMA)))
    names = {n.name for n in retriever.relevant_symbols("add a status column to the orders table")}
    assert "orders" in names  # the data layer is now retrievable for grounding


def test_foreign_keys_emit_references_edges() -> None:
    from orchestrator.pkg.schema import ForeignKey

    schema = DBSchema(
        database="app",
        tables=(
            DBTable(
                name="orders",
                columns=(DBColumn("id", "integer", False), DBColumn("customer_id", "integer")),
                foreign_keys=(ForeignKey(column="customer_id", ref_table="customers", ref_column="id"),),
            ),
            DBTable(name="customers", columns=(DBColumn("id", "integer", False),)),
            # FK to a table outside this schema → no dangling edge.
            DBTable(
                name="audit",
                columns=(DBColumn("order_id", "integer"),),
                foreign_keys=(
                    ForeignKey(column="order_id", ref_table="orders"),
                    ForeignKey("x", "missing_table"),
                ),
            ),
        ),
    )
    store = FactStore(schema_to_facts(schema))
    # orders → customers; audit → orders; the missing_table FK is skipped.
    refs = {n.name for n in store.references_of("db:app.orders")}
    assert refs == {"customers"}
    assert {n.name for n in store.references_of("db:app.audit")} == {"orders"}
    # reverse: who depends on orders?
    assert {n.name for n in store.dependents_of("db:app.orders")} == {"audit"}
    assert {n.name for n in store.dependents_of("db:app.customers")} == {"orders"}
