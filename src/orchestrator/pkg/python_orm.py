"""ORM models in Python source → ``Entity`` / ``Field`` nodes and ``REFERENCES`` edges.

The data layer is where a change is most expensive and least visible from the call graph:
"what touches the ``audit_log`` table?" is unanswerable when the graph holds classes but no
tables. C# has emitted entities for releases; Python held 7 ``__tablename__`` declarations
and zero ``Entity`` nodes, so ``episteme/domain-model.md`` said this repo has no database.

Two ORMs, both statically readable from the ``ast`` the front-end already parses:

============  =====================================  ===============================
ORM           Table name                             Foreign keys
============  =====================================  ===============================
SQLAlchemy    literal ``__tablename__``              ``ForeignKey("other.col")``
Django        ``Meta.db_table``, else ``app_model``  ``ForeignKey(Other)``,
                                                     ``OneToOneField(Other)``
============  =====================================  ===============================

Ids are ``py:entity:<table>`` — deliberately **not** ``sql:``-prefixed, so
:func:`~orchestrator.pkg.data_layer_link.link_data_layer` collapses them onto the real
schema when a repo also has ``.sql`` source, and the schema stays authoritative. The class's
``Type`` node stays exactly as it was; the ``Entity`` is added alongside it, matching C#.

**The precision rule here is the highest-risk one in the whole track.** At field level a
Pydantic model, a dataclass, a TypedDict and an attrs class are indistinguishable from an ORM
model — annotated names with defaults. Treating them as tables would flood the data layer
with fiction, and fiction in the data layer is worse than silence: it invents rows, columns
and relationships nobody can find in a database. So the gate is a **real ORM marker** —
a literal ``__tablename__``, or a ``models.Model`` base — never the shape of the class.

Foreign keys resolve in ``finalize`` because a Django ``ForeignKey(Order)`` names a *class*
that usually lives in another module, and the target's table name is only known once every
model has been read.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# Django field constructors that point at another model. ``ManyToManyField`` is included:
# it is a relationship through a join table, and omitting it would under-report the graph
# in the direction that matters (a missing edge reads as "nothing depends on this").
_DJANGO_RELATIONS = frozenset({"ForeignKey", "OneToOneField", "ManyToManyField"})

# Data-access calls whose first argument names the table being touched. Narrow on purpose:
# these four are the dominant SQLAlchemy 2.0 idiom, and the *bare name* argument is what makes
# them precise. Raw ``text()`` SQL, a table chosen at runtime and lazy-loading traversals are
# all skipped — a wrong data-access edge is worse than a missing one, because it sends a
# schema-change impact query to the wrong callers.
_ACCESS_KINDS = {
    "select": EdgeKind.READS,
    "insert": EdgeKind.WRITES,
    "update": EdgeKind.WRITES,
    "delete": EdgeKind.WRITES,
}

# Class-body names that describe the table rather than a column.
_NOT_COLUMNS = frozenset({"__tablename__", "__table_args__", "__abstract__", "objects", "Meta"})


@dataclass(frozen=True)
class PendingColumn:
    name: str
    provenance: Provenance


@dataclass(frozen=True)
class PendingReference:
    """A foreign key whose target may be a table name or a not-yet-seen model class."""

    table: str  # literal target table ("orders"), or "" when the target is a class
    class_name: str  # target model class name, or "" when the target is literal
    provenance: Provenance


@dataclass
class PendingEntity:
    table: str
    class_name: str
    module_id: str
    provenance: Provenance
    columns: list[PendingColumn] = field(default_factory=list)
    references: list[PendingReference] = field(default_factory=list)


@dataclass(frozen=True)
class PendingAccess:
    """A function touching a table, before the class → table map is complete."""

    function_id: str
    class_name: str
    kind: EdgeKind
    provenance: Provenance


@dataclass
class OrmState:
    """Entities found so far, across the whole walk (foreign keys span files)."""

    entities: list[PendingEntity] = field(default_factory=list)
    accesses: list[PendingAccess] = field(default_factory=list)

    def clear(self) -> None:
        self.entities.clear()
        self.accesses.clear()


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.expr) -> str:
    """``models.ForeignKey`` → ``ForeignKey``; ``Column`` → ``Column``; else ``""``."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return ""


def _django_model_base(cls: ast.ClassDef) -> bool:
    """``class Order(models.Model)`` — the marker, not the shape."""
    for base in cls.bases:
        if isinstance(base, ast.Attribute) and base.attr == "Model":
            return True
        if isinstance(base, ast.Name) and base.id == "Model":
            return True
    return False


def _meta_db_table(cls: ast.ClassDef) -> str | None:
    for stmt in cls.body:
        if not isinstance(stmt, ast.ClassDef) or stmt.name != "Meta":
            continue
        for inner in stmt.body:
            if isinstance(inner, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "db_table" for t in inner.targets
            ):
                return _literal_str(inner.value)
    return None


def _tablename(cls: ast.ClassDef) -> str | None:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets
        ):
            # A computed ``__tablename__`` (a helper call, a Meta lookup) is skipped
            # entirely: a table named by a guess is worse than a table that is missing.
            return _literal_str(stmt.value)
    return None


def _django_table(cls: ast.ClassDef, module_id: str) -> str:
    """Django's own default when ``Meta.db_table`` is absent: ``<app>_<model>``.

    The app label is the package directly containing the model, which is what Django uses
    and what the module id already carries.
    """
    explicit = _meta_db_table(cls)
    if explicit:
        return explicit
    parts = module_id.removeprefix("py:").split(".")
    app = parts[-2] if len(parts) > 1 else (parts[0] if parts else "app")
    return f"{app}_{cls.name.lower()}"


def _references_in(node: ast.expr, *, django: bool, rel: str, line: int) -> list[PendingReference]:
    """Foreign keys named anywhere inside a column/field definition.

    ``ForeignKey`` means two different things depending on the ORM, and the string looks
    identical: SQLAlchemy's ``ForeignKey("customers.id")`` is *table.column*, Django's
    ``ForeignKey("shop.Customer")`` is *app.Model*. Reading one as the other silently
    invents a table named after an app. The owning class decides which ORM we are in.
    """
    out: list[PendingReference] = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        name = _call_name(inner)
        if name not in _DJANGO_RELATIONS or not inner.args:
            continue
        first = inner.args[0]
        if isinstance(first, ast.Name):  # ForeignKey(Order) — a class, either ORM
            out.append(PendingReference("", first.id, Provenance(rel, line)))
            continue
        literal = _literal_str(first)
        if literal is None:
            continue
        if django:
            out.append(PendingReference("", literal.split(".")[-1], Provenance(rel, line)))
        else:
            out.append(PendingReference(literal.split(".")[0], "", Provenance(rel, line)))
    return out


def _columns_and_refs(
    cls: ast.ClassDef, *, django: bool, rel: str
) -> tuple[list[PendingColumn], list[PendingReference]]:
    columns: list[PendingColumn] = []
    references: list[PendingReference] = []
    for stmt in cls.body:
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(stmt, ast.AnnAssign):  # SQLAlchemy 2.0: ``x: Mapped[str] = mapped_column()``
            target, value = stmt.target, stmt.value
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id in _NOT_COLUMNS:
            continue
        name = _call_name(value) if value is not None else ""
        is_column = name in {"Column", "mapped_column", "relationship"} or (
            django and (name.endswith("Field") or name in _DJANGO_RELATIONS)
        )
        annotated = isinstance(stmt, ast.AnnAssign) and "Mapped" in ast.dump(stmt.annotation or ast.Pass())
        if not (is_column or annotated):
            continue
        line = getattr(stmt, "lineno", cls.lineno)
        if name != "relationship":  # a relationship is an edge, not a stored column
            columns.append(PendingColumn(target.id, Provenance(rel, line)))
        if value is not None:
            references.extend(_references_in(value, django=django, rel=rel, line=line))
    return columns, references


def _collect_accesses(
    body: list[ast.stmt], *, parent_id: str, enclosing: str, rel: str, state: OrmState
) -> None:
    """Attribute ``select(Model)`` and friends to the function they appear in.

    The innermost enclosing function owns the access, so a query inside a nested helper is
    not blamed on the outer one. A call outside any function (module level) is skipped: the
    graph has no symbol to hang the edge on that a reader could act upon.
    """
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            _collect_accesses(
                stmt.body, parent_id=f"{parent_id}.{stmt.name}", enclosing="", rel=rel, state=state
            )
            continue
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            func_id = f"{parent_id}.{stmt.name}"
            _collect_accesses(stmt.body, parent_id=func_id, enclosing=func_id, rel=rel, state=state)
            continue
        if not enclosing:
            continue
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            kind = _ACCESS_KINDS.get(_call_name(node))
            if kind is None:
                continue
            target = node.args[0]
            # A bare name, and nothing else. ``select(text("..."))`` or a computed table
            # resolves to no class and is dropped rather than guessed at.
            if isinstance(target, ast.Name):
                state.accesses.append(PendingAccess(enclosing, target.id, kind, Provenance(rel, node.lineno)))


def scan_module(tree: ast.Module, *, module_id: str, rel: str, state: OrmState) -> None:
    """Collect this file's ORM entities and data access into ``state``."""
    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.ClassDef):
            continue
        django = _django_model_base(stmt)
        table = _tablename(stmt)
        if table is None and django:
            table = _django_table(stmt, module_id)
        if not table:
            continue  # not an ORM model, or its table name is computed
        columns, references = _columns_and_refs(stmt, django=django, rel=rel)
        state.entities.append(
            PendingEntity(
                table=table,
                class_name=stmt.name,
                module_id=module_id,
                provenance=Provenance(rel, stmt.lineno),
                columns=columns,
                references=references,
            )
        )
    _collect_accesses(tree.body, parent_id=module_id, enclosing="", rel=rel, state=state)


def emit(state: OrmState, batch: FactBatch) -> None:
    """Add every entity, its columns, and the foreign keys between them."""
    by_class = {entity.class_name: entity.table for entity in state.entities}

    for entity in state.entities:
        eid = f"py:entity:{entity.table}"
        batch.add_node(Node(eid, NodeKind.ENTITY, entity.table, "python", entity.provenance))
        batch.add_edge(Edge(entity.module_id, eid, EdgeKind.CONTAINS, entity.provenance))
        for column in entity.columns:
            fid = f"{eid}.{column.name}"
            batch.add_node(
                Node(
                    fid,
                    NodeKind.FIELD,
                    f"{entity.table}.{column.name}",
                    "python",
                    column.provenance,
                )
            )
            batch.add_edge(Edge(eid, fid, EdgeKind.CONTAINS, column.provenance))

    for entity in state.entities:
        src = f"py:entity:{entity.table}"
        for reference in entity.references:
            table = reference.table or by_class.get(reference.class_name, "")
            if not table or table == entity.table:
                continue  # unresolvable target, or a self-reference — say nothing
            dst = f"py:entity:{table}"
            # A target this repo doesn't define still gets a placeholder, exactly as the SQL
            # front-end does: the edge is real even when the table lives elsewhere.
            batch.add_node(Node(dst, NodeKind.ENTITY, table, "python", external=True))
            batch.add_edge(Edge(src, dst, EdgeKind.REFERENCES, reference.provenance))

    for access in state.accesses:
        accessed = by_class.get(access.class_name)
        if accessed is None:
            continue  # not an ORM class this repo declares — say nothing
        batch.add_edge(Edge(access.function_id, f"py:entity:{accessed}", access.kind, access.provenance))


__all__ = ["OrmState", "PendingAccess", "PendingEntity", "emit", "scan_module"]
