"""Room (Android's SQLite ORM) → ``Entity`` nodes and the data edges around them.

P3 of docs/specs/kotlin-support-roadmap.md, §3.3. Three readings, all driven by
annotations rather than by naming convention:

* ``@Entity`` on a class → an ``Entity`` node, **named by its table** and holding
  the class's properties as ``Field``s.
* ``@ForeignKey`` / ``@Relation`` / ``@Junction`` → ``REFERENCES`` between entities.
* ``@Dao`` methods → ``READS`` / ``WRITES``. ``@Insert``/``@Upsert``/``@Delete``
  resolve through the method's *parameter type*; ``@Query`` by parsing the SQL.

**Why the node name is the table and the id is the class.** The id has to be
stable and unique, so it follows the declaration (``java:entity:pkg.TopicEntity``,
the C#/PHP parallel-id precedent). But :mod:`orchestrator.pkg.data_layer_link`
reconciles an ORM entity against a real ``.sql`` schema by comparing *names* — so
the name has to be the thing the schema also calls it. ``@Entity(tableName =
"topics")`` therefore yields ``name="topics"``, and a repo carrying both this DAO
layer and its migration SQL collapses the two onto one authoritative node with no
Kotlin-specific code in the linker.

**SQL is parsed, never pattern-matched.** ``@Query`` bodies go through ``sqlglot``
— the SQL front-end's own parser, used here for the second time outside ``.sql``.
It is an optional dependency (the ``sql`` extra): without it, ``@Query`` edges are
**skipped and said to be skipped**, never guessed from a regex. A Room query that
cannot be parsed yields nothing rather than a plausible-looking edge.

**Table targets resolve in two steps.** A ``@Query`` names a *table* while an
``@Entity`` is keyed by its *class*, and the two are almost always in different
files, so a per-file pass cannot join them. Edges are emitted at a provisional
``java:entity:<table>`` id and repointed at the declaring entity in ``finalize``
once the whole tree is known. A table nothing declares keeps the provisional id as
an **external** entity named after the table — honest, and still matchable against
a ``.sql`` schema by the linker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.kotlin_names import (
    Annotation,
    annotations_of,
    class_reference,
    collection_items,
    element_type,
    field_text,
    string_value,
    text,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "kotlin"

#: Room write annotations → whether the entity comes from a parameter.
_WRITE_ANNOTATIONS = frozenset({"Insert", "Upsert", "Update", "Delete"})

#: Room's own package, for resolving an annotation that could be something else's.
_ROOM_PACKAGE = "androidx.room"


def entity_id(type_id: str) -> str:
    """``java:pkg.TopicEntity`` → ``java:entity:pkg.TopicEntity``."""
    return f"java:entity:{type_id[len('java:') :]}"


def table_entity_id(table: str) -> str:
    """The provisional id for a table named in SQL but not yet joined to a class."""
    return f"java:entity:{table.lower()}"


def parameter_entity_id(type_id: str) -> str:
    """The provisional id for a write method's parameter type, not yet checked.

    Marked distinctly from ``entity_id``/``table_entity_id`` (#394): a write
    parameter is a *class*, never a table name, so its provisional edge must be
    dropped outright when nothing grounds it — declared elsewhere in the tree or
    not. Sharing a provisional id shape with a genuine unknown-table guess (from
    ``@Query``) is what let it fall through to that guess's external-placeholder
    fallback instead.
    """
    return f"java:entity:param:{type_id[len('java:') :]}"


def read_entity(
    node: TSNode,
    type_id: str,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
    *,
    constants: Mapping[str, str],
) -> bool:
    """Emit the ``Entity`` for an ``@Entity``-annotated class. Returns whether it did.

    ``resolve`` maps a simple type name to a node id, so ``entity =
    TopicEntity::class`` reaches the right class through the file's imports.
    """
    annotation = _find(annotations_of(node, source), "Entity")
    if annotation is None:
        return False
    class_name = field_text(node, "name", source)
    if not class_name:
        return False

    named = annotation.arg("tableName")
    if named is None:
        # Room's documented default. Not a guess: the annotation says nothing, and
        # "nothing" means the class name.
        table = class_name
    else:
        # It says *something*. Found in review: an unreadable `tableName = TOPICS`
        # was indistinguishable from an absent one, so the class name was claimed as
        # the table — and then one real table produced two Entity nodes, a grounded
        # `TopicEntity` and an external `topics` from the DAO's own `@Query`, which
        # `data_layer_link` matches by name and so never reconciles with the
        # migration that creates it. A constant declared in this file is resolved;
        # anything else is refused outright, because a table name that is wrong is
        # worse here than a table name that is missing.
        table = string_value(named, source) or constants.get(text(named, source), "")
        if not table:
            return False
    eid = entity_id(type_id)
    line = annotation.line
    batch.add_node(Node(eid, NodeKind.ENTITY, table, _LANG, Provenance(rel, line)))

    _emit_entity_fields(node, eid, source, rel, batch)
    _emit_foreign_keys(annotation, eid, resolve, source, rel, batch)
    _emit_relations(node, eid, resolve, source, rel, batch)
    return True


def read_relation_view(
    node: TSNode,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> bool:
    """``REFERENCES`` from a Room *view* class — ``@Embedded`` plus ``@Relation``.

    Room's one-to-many and many-to-many reads are declared on a plain data class
    that is not itself an ``@Entity``::

        data class PopulatedNewsResource(
            @Embedded val entity: NewsResourceEntity,
            @Relation(associateBy = Junction(value = NewsResourceTopicCrossRef::class, …))
            val topics: List<TopicEntity>,
        )

    Every part of that is a *declaration*, so the relationship resolves without
    inference: ``@Embedded``'s declared type is the parent entity, the annotated
    property's element type is the child, and ``Junction(value = X::class)`` is the
    join table. The view class itself gets no node — it is a query result shape,
    not a table — so the edges hang off the entity it embeds.

    This matters more than its one occurrence suggests: the validation app declares
    **no ``@ForeignKey`` at all**, so without this reading ``REFERENCES`` would be
    unobservable on the primary repository and provable only in a fixture.
    """
    parent = ""
    for candidate in _walk(node):
        if candidate.type not in ("class_parameter", "property_declaration"):
            continue
        if _find(annotations_of(candidate, source), "Embedded") is None:
            continue
        resolved = resolve(element_type(_declared_type(candidate, source)).rsplit(".", 1)[-1])
        if resolved:
            parent = entity_id(resolved)
        break
    if not parent:
        return False
    _emit_relations(node, parent, resolve, source, rel, batch)
    return True


def _declared_type(node: TSNode, source: bytes) -> str:
    """The written type of a ``class_parameter`` or ``property_declaration``."""
    if node.type == "class_parameter":
        return next((text(c, source) for c in node.named_children if c.type == "user_type"), "")
    decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
    if decl is None:
        return ""
    return next((text(c, source) for c in decl.named_children if c.type == "user_type"), "")


def _emit_entity_fields(node: TSNode, eid: str, source: bytes, rel: str, batch: FactBatch) -> None:
    """Primary-constructor ``val``/``var`` parameters are the entity's columns (D6).

    A Room entity is almost always a ``data class`` whose columns *are* its
    constructor properties, so this is where the schema actually lives. The
    ``Field``s under the class's own ``Type`` node are emitted separately by the
    extractor; these are their parallel under the ``Entity``, which is what makes
    an entity comparable to a ``.sql`` table rather than to a Kotlin class.
    """
    ctor = next((c for c in node.named_children if c.type == "primary_constructor"), None)
    if ctor is None:
        return
    params = next((c for c in ctor.named_children if c.type == "class_parameters"), None)
    if params is None:
        return
    for param in params.named_children:
        if param.type != "class_parameter":
            continue
        if not any(c.type in ("val", "var") for c in param.children):
            continue
        name = next((text(c, source) for c in param.named_children if c.type == "identifier"), "")
        if not name:
            continue
        # `@ColumnInfo(name = "…")` renames the column; the graph records the
        # column, so the annotation wins over the property name when it is literal.
        column = _column_name(param, source) or name
        fid = f"{eid}.{column}"
        line = param.start_point[0] + 1
        batch.add_node(Node(fid, NodeKind.FIELD, column, _LANG, Provenance(rel, line)))
        batch.add_edge(Edge(eid, fid, EdgeKind.CONTAINS, Provenance(rel, line)))


def _column_name(param: TSNode, source: bytes) -> str:
    info = _find(annotations_of(param, source), "ColumnInfo")
    if info is None:
        return ""
    return string_value(info.arg("name"), source) or ""


def _emit_foreign_keys(
    annotation: Annotation,
    eid: str,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """``@Entity(foreignKeys = [ForeignKey(entity = Topic::class, …)])`` → ``REFERENCES``."""
    for item in collection_items(annotation.arg("foreignKeys")):
        target = _referenced_entity(item, resolve, source)
        if target:
            batch.add_edge(Edge(eid, target, EdgeKind.REFERENCES, Provenance(rel, annotation.line)))


def _emit_relations(node: TSNode, eid: str, resolve: Any, source: bytes, rel: str, batch: FactBatch) -> None:
    """``@Relation`` / ``@Junction`` on a property → ``REFERENCES``.

    These sit on the properties of a *view* class (``PopulatedNewsResource``),
    not on the entity itself, so the whole body is walked rather than just the
    constructor.
    """
    for candidate in _walk(node):
        if candidate.type not in ("class_parameter", "property_declaration"):
            continue
        for annotation in annotations_of(candidate, source):
            if annotation.name != "Relation":
                continue
            for target in _relation_targets(annotation, candidate, resolve, source):
                batch.add_edge(Edge(eid, target, EdgeKind.REFERENCES, Provenance(rel, annotation.line)))


def _relation_targets(annotation: Annotation, holder: TSNode, resolve: Any, source: bytes) -> list[str]:
    """The entities one ``@Relation`` points at: the child, and any junction table.

    The child comes from ``entity = X::class`` when it is written, and otherwise
    from the annotated property's own element type — ``val topics: List<TopicEntity>``
    says ``TopicEntity`` just as precisely, and omitting ``entity`` is the common
    spelling.
    """
    out: list[str] = []
    child = _referenced_entity(annotation.arg("entity"), resolve, source)
    if not child:
        declared = element_type(_declared_type(holder, source)).rsplit(".", 1)[-1]
        resolved = resolve(declared) if declared else None
        child = entity_id(resolved) if resolved else ""
    if child:
        out.append(child)
    # `associateBy = Junction(value = X::class)` — the join table is a real entity
    # and a real dependency; a change to it breaks this read.
    junction = _referenced_entity(annotation.arg("associateBy"), resolve, source)
    if junction:
        out.append(junction)
    return out


def _referenced_entity(node: TSNode | None, resolve: Any, source: bytes) -> str:
    """A ``X::class`` argument → the referenced ``Entity`` id, or ``""``.

    Accepts either the node itself or a ``ForeignKey(...)`` invocation wrapping it.
    """
    if node is None:
        return ""
    name = class_reference(node, source)
    if not name and node.type in ("constructor_invocation", "call_expression"):
        # Both spellings occur and the difference is purely positional: the same
        # `Junction(value = X::class)` is a `constructor_invocation` directly under
        # an annotation and a `call_expression` when it is an *argument* to one, as
        # in `@Relation(associateBy = Junction(...))`. Reading only the first form
        # silently drops every junction table.
        for argument in _read_invocation_args(node, source):
            name = class_reference(argument, source)
            if name:
                break
    if not name:
        return ""
    resolved = resolve(name)
    return entity_id(resolved) if resolved else ""


def _read_invocation_args(node: TSNode, source: bytes) -> list[TSNode]:
    args = next((c for c in node.named_children if c.type == "value_arguments"), None)
    if args is None:
        return []
    out: list[TSNode] = []
    for arg in args.named_children:
        if arg.type == "value_argument" and arg.named_children:
            out.append(arg.named_children[-1])
    return out


def read_dao(
    node: TSNode,
    type_id: str,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> bool:
    """Emit ``READS``/``WRITES`` for a ``@Dao`` interface's methods."""
    if _find(annotations_of(node, source), "Dao") is None:
        return False
    for body in (c for c in node.named_children if c.type in ("class_body", "enum_class_body")):
        for member in body.named_children:
            if member.type != "function_declaration":
                continue
            name = field_text(member, "name", source)
            if not name:
                continue
            _read_dao_method(member, f"{type_id}.{name}", resolve, source, rel, batch)
    return True


def _read_dao_method(
    method: TSNode,
    func_id: str,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    for annotation in annotations_of(method, source):
        if annotation.name in _WRITE_ANNOTATIONS:
            target = _parameter_entity(method, resolve, source)
            if target:
                batch.add_edge(Edge(func_id, target, EdgeKind.WRITES, Provenance(rel, annotation.line)))
        elif annotation.name == "Query":
            sql = string_value(annotation.arg("value"), source)
            if sql:
                _emit_sql_edges(sql, func_id, rel, annotation.line, batch)


def _parameter_entity(method: TSNode, resolve: Any, source: bytes) -> str:
    """The entity a write method writes, taken from its first typed parameter.

    ``suspend fun upsertTopics(entities: List<TopicEntity>)`` → ``TopicEntity``.
    The declared type is peeled to its innermost argument, so the collection and
    coroutine wrappers Room methods are written against do not hide the entity.
    """
    params = next((c for c in method.named_children if c.type == "function_value_parameters"), None)
    if params is None:
        return ""
    for param in params.named_children:
        if param.type != "parameter":
            continue
        declared = next((text(c, source) for c in param.named_children if c.type == "user_type"), "")
        name = element_type(declared)
        if not name:
            continue
        resolved = resolve(name.rsplit(".", 1)[-1])
        if resolved:
            return parameter_entity_id(resolved)
    return ""


def _emit_sql_edges(sql: str, func_id: str, rel: str, line: int, batch: FactBatch) -> None:
    """Parse a Room ``@Query`` and emit one edge per table it touches.

    Skipped entirely when ``sqlglot`` is absent (the ``sql`` extra) — a Room query
    is real SQL and deserves a real parser; a regex over ``FROM`` would produce
    edges that look right and are wrong on any query with a subquery or a CTE.
    """
    parsed = _parse_sql(sql)
    if parsed is None:
        return
    reads, writes = parsed
    provenance = Provenance(rel, line)
    for table in sorted(writes):
        batch.add_edge(Edge(func_id, table_entity_id(table), EdgeKind.WRITES, provenance))
    for table in sorted(reads - writes):
        batch.add_edge(Edge(func_id, table_entity_id(table), EdgeKind.READS, provenance))


def _parse_sql(sql: str) -> tuple[set[str], set[str]] | None:
    """``(tables read, tables written)``, or ``None`` when SQL cannot be read here."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:  # pragma: no cover - exercised only without the extra
        return None
    try:
        # Room is SQLite. Bind parameters (`:topicId`) are valid SQLite syntax, so
        # they parse without substitution.
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception:  # noqa: BLE001 - any parse failure means "say nothing"
        return None

    reads: set[str] = set()
    writes: set[str] = set()
    for statement in statements:
        if statement is None:
            continue
        target = _write_target(statement, exp)
        if target:
            writes.add(target)
        for table in statement.find_all(exp.Table):
            name = table.name
            if name and name != target:
                reads.add(name)
    return reads, writes


def _write_target(statement: Any, exp: Any) -> str:
    """The single table an INSERT/UPDATE/DELETE modifies, else ``""``."""
    if not isinstance(statement, exp.Insert | exp.Update | exp.Delete):
        return ""
    this = statement.this
    table = this if isinstance(this, exp.Table) else (this.find(exp.Table) if this else None)
    return table.name if table is not None else ""


def repoint_table_edges(batch: FactBatch) -> FactBatch:
    """Join ``@Query`` table edges to the entity classes that declare those tables.

    A ``@Query`` names a table and an ``@Entity`` names a class; they live in
    different files, so this can only run once the whole tree is known. Every
    provisional ``java:entity:<table>`` is repointed at the declaring entity.

    A table **nothing declares** keeps its provisional id and gets an external
    ``Entity`` node named after the table. That is the honest record — the DAO
    really does read a table this tree has no class for — and it leaves the row
    matchable against a real ``.sql`` schema by ``data_layer_link``, which pairs
    entities by name.
    """
    by_table: dict[str, str] = {}
    entities = set()
    for node in batch.nodes:
        if node.kind is NodeKind.ENTITY and node.id.startswith("java:entity:"):
            entities.add(node.id)
            if node.grounded:
                by_table.setdefault(node.name.lower(), node.id)

    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        if edge.kind not in (EdgeKind.READS, EdgeKind.WRITES) or not edge.dst.startswith("java:entity:"):
            out.add_edge(edge)
            continue
        if edge.dst.startswith("java:entity:param:"):
            # #394: a write method's *parameter* type, never a table name — provenance
            # settles it outright rather than asking whether the repo happens to declare
            # the class. Only a genuine `@Entity` grounds it; declared-elsewhere-as-a-
            # plain-class and not-declared-anywhere alike drop, because neither is a
            # table this tree could honestly stand behind with an external placeholder.
            type_id = f"java:{edge.dst[len('java:entity:param:') :]}"
            grounded = entity_id(type_id)
            if grounded in entities:
                out.add_edge(Edge(edge.src, grounded, edge.kind, edge.provenance))
            continue
        table = edge.dst[len("java:entity:") :]
        target = by_table.get(table.lower())
        if target is not None:
            out.add_edge(Edge(edge.src, target, edge.kind, edge.provenance))
            continue
        if edge.dst in entities:
            out.add_edge(edge)
            continue
        # A table this tree has no class for. The honest record — the DAO really does
        # read it — and `data_layer_link` can still pair it with a real `.sql` schema.
        out.add_node(Node(edge.dst, NodeKind.ENTITY, table, _LANG, external=True))
        out.add_edge(edge)
    return out


def _find(annotations: list[Annotation], name: str) -> Annotation | None:
    return next((a for a in annotations if a.name == name), None)


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


__all__ = [
    "entity_id",
    "read_dao",
    "read_entity",
    "read_relation_view",
    "repoint_table_edges",
    "table_entity_id",
]
