"""Perl DBIx::Class entity extraction (P4, §3.4).

Runs in ``finalize()`` (not per-file, unlike routes) because a relation target's
local-vs-external classification needs whole-repo knowledge of which packages are
themselves DBIC Result classes — the same reason P2's CALLS pass is a ``finalize()`` pass,
not a per-file one.

Marker: ``__PACKAGE__->table('orders')`` — the one call every DBIx::Class Result class must
make, so it identifies the class definitively rather than guessing from its base class or
shape. Columns (``add_columns(...)``, bareword list or ``name => {...}`` hash form — only
the keys are read, never the type-info values) become ``Field`` nodes on the entity, with a
``CONTAINS`` edge from entity to field, matching how ``Type``/``Field`` works elsewhere.
Relations (``belongs_to``/``has_many``/``might_have``) become ``REFERENCES`` edges,
``src`` = the declaring entity, ``dst`` = the named target — direction doesn't vary by
relation kind, matching every other ORM reader in this codebase (see ``php_orm.py``).

A relation target that isn't itself a declared DBIC entity in this repo still gets a
``REFERENCES`` edge — to an external placeholder ``Entity`` node — never silently dropped
(the ``dbic`` corpus case's "one relation outside the tree" is built to catch a regression
here). ``data_layer_link.link_data_layer`` (a separate, already-shared, cross-language pass)
reconciles these ORM-inferred entities against a real ``.sql`` schema when one exists in the
repo; it needs no Perl-specific wiring and is a no-op otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# DBIx::Class relationship declarators (§3.4). Rose::DB::Object/Class::DBI ("same shape,
# second reader" per the roadmap) aren't in P4's corpus and aren't read here — a documented
# gap, not an oversight; add a reader for them the same way if real demand shows up.
# `many_to_many` deliberately excluded: its signature isn't `($rel_name, $related_class,
# ...)` like the other four (it names a linking relation + a foreign relation, not a
# target class directly), so the same positional read would be wrong for it — a
# documented gap, not an oversight.
DBIC_RELATIONS = frozenset({"belongs_to", "has_many", "might_have", "has_one"})


class _TypeRecLike(Protocol):
    """Just the ``_TypeRec`` fields this module reads — a ``Protocol`` rather than an
    import, so this module has no dependency on ``perl_extractor.py`` (which imports this
    module, not the other way around)."""

    is_dbic_entity: bool
    dbic_table_line: int | None
    dbic_rel: str
    dbic_columns: list[tuple[str, int]]
    dbic_relations: list[tuple[str, int]]


def entity_id(type_id: str) -> str:
    """``perl:App.Schema.Result.Order`` -> ``perl:entity:App.Schema.Result.Order``."""
    prefix, _, body = type_id.partition(":")
    return f"{prefix}:entity:{body}"


def emit_entities(types: Mapping[str, _TypeRecLike], batch: FactBatch) -> None:
    """``types`` is ``PerlExtractor._types`` — every ``_TypeRec`` seen across the whole repo,
    by the time ``finalize()`` runs. Two passes, like ``php_orm.py``: which packages qualify
    as entities, built first, so a relation target declared in a file walked earlier or later
    resolves the same way regardless of order.
    """
    entity_ids: dict[str, str] = {tid: entity_id(tid) for tid, rec in types.items() if rec.is_dbic_entity}
    for tid, eid in entity_ids.items():
        rec = types[tid]
        line = rec.dbic_table_line if rec.dbic_table_line is not None else 1
        name = tid.partition(":")[2].rsplit(".", 1)[-1]
        batch.add_node(Node(eid, NodeKind.ENTITY, name, "perl", Provenance(rec.dbic_rel, line)))
        for column, col_line in rec.dbic_columns:
            fid = f"{eid}.{column}"
            batch.add_node(Node(fid, NodeKind.FIELD, column, "perl", Provenance(rec.dbic_rel, col_line)))
            batch.add_edge(Edge(eid, fid, EdgeKind.CONTAINS, Provenance(rec.dbic_rel, col_line)))
        for target_name, rel_line in rec.dbic_relations:
            if not target_name:
                continue
            target_tid = f"perl:{target_name.replace('::', '.')}"
            target_eid = entity_ids.get(target_tid)
            prov = Provenance(rec.dbic_rel, rel_line)
            if target_eid is None:
                target_eid = entity_id(target_tid)
                target_display = target_name.replace("::", ".").rsplit(".", 1)[-1]
                batch.add_node(Node(target_eid, NodeKind.ENTITY, target_display, "perl", external=True))
            batch.add_edge(Edge(eid, target_eid, EdgeKind.REFERENCES, prov))


__all__ = ["DBIC_RELATIONS", "emit_entities", "entity_id"]
