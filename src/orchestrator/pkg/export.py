"""Export PKG facts as the kind-per-table SQLite projection ontomesh ingests.

NOTE: ontomesh is a **deferred, experimental** export target, kept behind this
seam and not on any live path — codegen comprehension is served by the
in-process PKG grounder (``orchestrator.sdlc.grounding``), not by ontomesh. This
exporter is retained for a future ontology/SHACL layer; revisit when there's a
concrete semantic-query need. The contract below was verified against
``synaptixs/ontomesh@v3.8.0``.

ontomesh ingests a SQLite DB and turns *tables into OWL classes and FK columns
into object properties*, guided by its ``ontology_metadata`` annotation table.
So the projection is deliberately **domain-shaped**, one table per ``NodeKind``
and one relation per ``EdgeKind`` — never a generic ``nodes``/``edges`` dump,
which would model our storage instead of the code:

    modules(id, name, language, file, line, end_line)
    types(id, name, module_id → modules, …)
    functions(id, name, parent_type_id → types, module_id → modules, …)
    calls(caller_id → functions, callee_id → functions, file, line)
    imports(module_id → modules, target_id → modules, file, line)

The ``ontology_metadata`` rows set the OWL **class** names (``:Module``/
``:Type``/``:Function``) and ``rdfs:label`` on the FK properties. Two v3.8.0
realities to keep in mind before relying on this: (1) object-property *IRIs*
are derived by ontomesh from the *column name* (e.g. ``caller_id`` →
``:callerOf``), so our ``label`` annotations become ``rdfs:label`` but not the
property IRI; (2) ontomesh's toolkit generates the **ontology (TBox) from the
schema only** — it does not materialise one individual per table row, so the
per-symbol ``file:line`` provenance here is carried in the rows but is not
emitted as ABox individuals by the standard pipeline. Closing those gaps is
part of the (deferred) ontomesh track, not a precondition for codegen grounding.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind

_SCHEMA = """
CREATE TABLE modules (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    language  TEXT,
    external  INTEGER NOT NULL DEFAULT 0,
    file      TEXT,
    line      INTEGER,
    end_line  INTEGER
);
CREATE TABLE types (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    language  TEXT,
    external  INTEGER NOT NULL DEFAULT 0,
    module_id TEXT REFERENCES modules(id),
    file      TEXT,
    line      INTEGER,
    end_line  INTEGER
);
CREATE TABLE functions (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    language       TEXT,
    external       INTEGER NOT NULL DEFAULT 0,
    parent_type_id TEXT REFERENCES types(id),
    module_id      TEXT REFERENCES modules(id),
    file           TEXT,
    line           INTEGER,
    end_line       INTEGER
);
CREATE TABLE calls (
    caller_id TEXT NOT NULL REFERENCES functions(id),
    callee_id TEXT NOT NULL REFERENCES functions(id),
    file      TEXT,
    line      INTEGER,
    PRIMARY KEY (caller_id, callee_id, file, line)
);
CREATE TABLE imports (
    module_id TEXT NOT NULL REFERENCES modules(id),
    target_id TEXT NOT NULL REFERENCES modules(id),
    file      TEXT,
    line      INTEGER,
    PRIMARY KEY (module_id, target_id, file, line)
);
-- Mirrors ontomesh's db/schema.sql DDL exactly (its setup_db runs
-- CREATE TABLE IF NOT EXISTS and later writes to these columns, so a
-- slimmed-down table breaks ingestion).
CREATE TABLE ontology_metadata (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type       TEXT NOT NULL CHECK(target_type IN ('TABLE','COLUMN')),
    table_name        TEXT NOT NULL,
    column_name       TEXT,
    semantic_type     TEXT,
    label             TEXT,
    description       TEXT,
    sensitivity_tier  TEXT DEFAULT 'Internal'
                      CHECK(sensitivity_tier IN ('Public','Internal','Confidential','Restricted')),
    is_event_class    INTEGER DEFAULT 0,
    skos_pref_label   TEXT,
    skos_alt_labels   TEXT,
    cq_coverage       TEXT,
    is_transitive         INTEGER DEFAULT 0,
    is_symmetric          INTEGER DEFAULT 0,
    is_functional         INTEGER DEFAULT 0,
    is_inverse_functional INTEGER DEFAULT 0,
    inverse_of            TEXT,
    disjoint_group        TEXT,
    has_key_columns       TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
"""

# (target_type, table, column, semantic_type, label, description)
_ANNOTATIONS: list[tuple[str, str, str | None, str, str, str]] = [
    ("TABLE", "modules", None, "Module", "Module", "A source module/file of the product codebase."),
    ("TABLE", "types", None, "Type", "Type", "A class/struct/interface defined in the codebase."),
    ("TABLE", "functions", None, "Function", "Function", "A function or method defined in the codebase."),
    ("COLUMN", "types", "module_id", "Module", "containedIn", "The module that contains this type."),
    ("COLUMN", "functions", "parent_type_id", "Type", "memberOf", "The type this function is a method of."),
    ("COLUMN", "functions", "module_id", "Module", "containedIn", "The module that contains this function."),
    ("COLUMN", "calls", "caller_id", "Function", "calls", "Call-graph edge: the calling function."),
    ("COLUMN", "calls", "callee_id", "Function", "calledBy", "Call-graph edge: the called function."),
    ("COLUMN", "imports", "target_id", "Module", "imports", "Module-level import dependency."),
]


def export_sqlite(batch: FactBatch, path: Path | str) -> dict[str, int]:
    """Write the projection to ``path`` (overwritten). Returns row counts.

    Facts that don't fit the projection are *dropped, not mangled*: a CALLS
    edge whose endpoints aren't functions, or a CONTAINS edge between
    unknown ids, is skipped — the projection is a view of the graph for
    ontology inference, not the system of record (that stays JSON/Postgres).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    nodes = {n.id: n for n in batch.nodes}
    modules = [n for n in batch.nodes if n.kind is NodeKind.MODULE]
    types = [n for n in batch.nodes if n.kind is NodeKind.TYPE]
    functions = [n for n in batch.nodes if n.kind is NodeKind.FUNCTION]

    # CONTAINS edges → ownership FKs.
    type_module: dict[str, str] = {}
    func_parent_type: dict[str, str] = {}
    func_module: dict[str, str] = {}
    for e in batch.edges:
        if e.kind is not EdgeKind.CONTAINS:
            continue
        src, dst = nodes.get(e.src), nodes.get(e.dst)
        if src is None or dst is None:
            continue
        if src.kind is NodeKind.MODULE and dst.kind is NodeKind.TYPE:
            type_module[dst.id] = src.id
        elif src.kind is NodeKind.TYPE and dst.kind is NodeKind.FUNCTION:
            func_parent_type[dst.id] = src.id
        elif src.kind is NodeKind.MODULE and dst.kind is NodeKind.FUNCTION:
            func_module[dst.id] = src.id

    conn = sqlite3.connect(target)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO modules VALUES (?,?,?,?,?,?,?)",
            [_node_row(n) for n in modules],
        )
        conn.executemany(
            "INSERT INTO types VALUES (?,?,?,?,?,?,?,?)",
            [(*_node_row(n)[:4], type_module.get(n.id), *_node_row(n)[4:]) for n in types],
        )
        conn.executemany(
            "INSERT INTO functions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (*_node_row(n)[:4], func_parent_type.get(n.id), func_module.get(n.id), *_node_row(n)[4:])
                for n in functions
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO calls VALUES (?,?,?,?)",
            [
                (e.src, e.dst, *_edge_at(e))
                for e in batch.edges
                if e.kind is EdgeKind.CALLS and _both_kinds(nodes, e, NodeKind.FUNCTION, NodeKind.FUNCTION)
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO imports VALUES (?,?,?,?)",
            [
                (e.src, e.dst, *_edge_at(e))
                for e in batch.edges
                if e.kind is EdgeKind.IMPORTS and _both_kinds(nodes, e, NodeKind.MODULE, NodeKind.MODULE)
            ],
        )
        conn.executemany(
            "INSERT INTO ontology_metadata "
            "(target_type, table_name, column_name, semantic_type, label, description) "
            "VALUES (?,?,?,?,?,?)",
            _ANNOTATIONS,
        )
        conn.commit()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in ("modules", "types", "functions", "calls", "imports", "ontology_metadata")
        }
    finally:
        conn.close()
    return counts


def _node_row(n: Node) -> tuple[str, str, str, int, str | None, int | None, int | None]:
    prov = n.provenance
    return (
        n.id,
        n.name,
        n.language,
        int(n.external),
        prov.file if prov else None,
        prov.line if prov else None,
        prov.end_line if prov else None,
    )


def _edge_at(e: Edge) -> tuple[str | None, int | None]:
    return (e.provenance.file, e.provenance.line) if e.provenance else (None, None)


def _both_kinds(nodes: dict[str, Node], e: Edge, src_kind: NodeKind, dst_kind: NodeKind) -> bool:
    src, dst = nodes.get(e.src), nodes.get(e.dst)
    return src is not None and dst is not None and src.kind is src_kind and dst.kind is dst_kind


__all__ = ["export_sqlite"]
