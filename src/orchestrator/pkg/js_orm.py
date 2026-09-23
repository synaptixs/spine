"""Sequelize models in JavaScript source → ``Entity`` + ``Field`` nodes and ``REFERENCES`` edges.

The data layer of javascript-support-roadmap P4 (D10). The shape is Python's (``python_orm.py``):
an ``Entity`` a module ``CONTAINS``, its columns as ``Field`` nodes it ``CONTAINS``, and
entity→entity ``REFERENCES``. Ids are ``ts:entity:<model>`` — the ``ts:`` namespace the
JavaScript front-end shares with TypeScript, in the form ``ts:endpoint:`` already uses — and
deliberately not ``sql:``-prefixed, so :func:`~orchestrator.pkg.data_layer_link.link_data_layer`
reads them as ORM entities and collapses each onto a matching table in a ``.sql`` schema. It
matches by the entity's *name*, which is the declared ``tableName`` when there is one and the
model name otherwise, and it pluralizes naively — a trailing ``s`` — so ``user``/``users`` pair
and ``person``/``people`` do not, unless ``tableName`` says so.

**No ORM marker, no Entity** — the rule every ORM reader in this package keeps, because a model
is not recognisable by its shape. The two markers read here, both statically:

- ``<x>.define('<name>', {…})`` whose attribute map **uses a Sequelize type** — a value rooted at
  a binding from the ``sequelize`` package (``DataTypes.STRING``, ``Sequelize.DataTypes.X``, or
  ``{type: …}`` of one). ``define`` alone marks nothing: ``customElements.define``,
  ``ajv.define`` and factory-girl's ``factory.define`` all take a string and an object. The
  official example app defines every model *inside a function* —
  ``module.exports = (sequelize) => { sequelize.define('user', …) }`` — so the whole file is
  walked, not only its top level.
- ``class X extends Model`` where ``Model`` is **bound from ``sequelize``** (Objection.js names
  its base the same) **and** ``X.init({…})`` is in the file. A class with no ``init`` is an
  abstract base the real models extend, and has no table. ``modelName`` renames the model;
  ``tableName`` names its table.

**``REFERENCES`` follows the foreign key**, so it agrees with the schema it is reconciled
against: ``A.belongsTo(B)`` puts the key on A (A references B); ``A.hasMany(B)`` and
``A.hasOne(B)`` put it on B (B references A). The redundant pair a real app writes —
``orchestra.hasMany(instrument)`` *and* ``instrument.belongsTo(orchestra)`` — states one edge
twice, not two. An end is named by: a destructuring of the models registry
(``const { instrument } = sequelize.models``), a ``….models.name`` access, a Sequelize model
class, a ``const User = sequelize.define(…)`` binding, or an **import** — which names whatever
the imported module *defines*, not the local's spelling (``const Author = require('./user')``
is the ``user`` model). An import is settled by :func:`settle` once every file is read, and the
front-end's ``finalize`` then drops any ``REFERENCES`` whose ends are not both entities.

Left out, and declared rather than guessed: ``belongsToMany`` (its keys live on a join table,
often one Sequelize generates from a ``through`` string at run time), a self-association, a
model name or attribute map that is not a literal, ``class X extends Sequelize.Model``,
sequelize-cli's generated models (``module.exports = (sequelize, DataTypes) => …`` — no
``sequelize`` import, so no marker), its ``static associate(models)`` form, ``@sequelize/core``
v7, the column-level ``references: {model: …}`` form, and a *renamed* CommonJS destructure of
the package (``const { Model: Base } = require('sequelize')``) — the front-end binds no local for
a renamed key, so its base is unseen, where the ESM ``import { Model as Base }`` is read.

Also declared, each measured and left as it is:

- **The receiver of ``define`` is not checked**, and neither is every link of a type chain:
  ``customElements.define('x-el', { a: DataTypes.STRING })`` mints an entity, and so does a
  chain that reaches no type, such as ``Sequelize.Op.STRING``. Telling the connection from
  another receiver needs receiver tracking this front-end does not have; neither shape is
  realistic in a column position.
- **A type imported by name marks nothing.** After ``const { STRING } = require('sequelize')``,
  ``STRING(64)`` is no ``DataTypes.`` chain, so a model typed only that way is missed: a
  recall gap.
- **An import that does not match what the module exports can still invent**, in consumer
  code that is itself broken: a whole-module ``const Booking = require('./b')`` of a module
  exporting ``{ Booking }`` takes the module's one model, and an ESM ``import { Post }`` from
  a file exporting only ``{ Post as Article }`` falls back to the model named ``post``. So do
  an ESM ``import { Post }`` of a file whose model is its ``export default`` — the ESM form of a
  destructured default — and a whole-module import of a module whose default is a function
  that is not a model, which still takes the module's one model.
- **A callback is taken to run after the module has loaded**, so it reads a name's last
  declaration: `var Post = a; [1].forEach(() => Post.x()); var Post = b` binds `b`, though a
  synchronous callback runs while `Post` is still `a`. Telling a synchronous callee from an
  asynchronous one needs the callee; a function invoked on the spot and a `static {}` block are
  read where they stand. So is an `async` one, though code after its first `await` runs later,
  and a generator's, though its body waits for `next()`; `(function () {}).call(this)` and
  `new (function () {})` are taken to run later. Each needs one model declared twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.typescript_extractor import _relative_module, _supertypes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tree_sitter import Node as TSNode

    from orchestrator.pkg.extractor import ExportMap

_LANG = "javascript"
_PACKAGE = "sequelize"
#: Association → whether the foreign key sits on the *receiver* (True) or the argument (False).
_ASSOCIATIONS = {"belongsTo": True, "hasMany": False, "hasOne": False}
#: Placeholder for "the entity module M defines", settled by :func:`settle` once every file is
#: read: ``ts:entity-of:<module id>#<imported name>``, or ``#*`` for a whole-module/default import.
ENTITY_OF = "ts:entity-of:"
_WHOLE = "*"
#: Sequelize's column types. A value rooted at a `sequelize` binding is not enough to mark a model:
#: `Op.is` and `Sequelize.NOW` are both rooted there and neither is a type, and `scopes.define(
#: 'active', { deletedAt: Op.is })` minted an entity. `NOW`, `UUIDV1` and `UUIDV4` are defaults.
#: `NUMERIC` is `DECIMAL`'s alias. A modifier (`UNSIGNED`, `ZEROFILL`, `BINARY`) is not listed: it
#: is never a type on its own, only a suffix on one — see `_sequelize_type`.
_TYPE_NAMES = frozenset(
    {
        "STRING",
        "CHAR",
        "TEXT",
        "CITEXT",
        "TSVECTOR",
        "TINYINT",
        "SMALLINT",
        "MEDIUMINT",
        "INTEGER",
        "BIGINT",
        "NUMBER",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "BOOLEAN",
        "TIME",
        "DATE",
        "DATEONLY",
        "HSTORE",
        "JSON",
        "JSONB",
        "BLOB",
        "RANGE",
        "UUID",
        "VIRTUAL",
        "ENUM",
        "ARRAY",
        "GEOMETRY",
        "GEOGRAPHY",
        "CIDR",
        "INET",
        "MACADDR",
        "MACADDR8",
    }
)


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _string(node: TSNode | None, source: bytes) -> str | None:
    if node is None or node.type != "string":
        return None
    return _text(node, source).strip("\"'") or None


def _walk(root: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(node.named_children))
    return out


def _args(call: TSNode) -> list[TSNode]:
    """A call's arguments. Comments are named children too, and `define(/* c */ 'x', …)` is real."""
    args = call.child_by_field_name("arguments")
    return [a for a in args.named_children if a.type != "comment"] if args is not None else []


def entity_id(name: str) -> str:
    return f"ts:entity:{name}"


def scan(
    root: TSNode,
    module_id: str,
    source: bytes,
    rel: str,
    batch: FactBatch,
    imports: dict[str, str],
    import_names: dict[str, str | None] | None = None,
    exports_objects: frozenset[str] = frozenset({"exports", "module.exports"}),
) -> dict[str, str]:
    """Emit this file's Sequelize models and associations into ``batch``; return its bindings.

    ``import_names`` maps each local to the name it was imported *as* — None for a whole-module
    or default import — so an association end can name the export it really is.
    ``exports_objects`` are the names that *are* the exports object here (`exports`,
    `module.exports`, and the aliases the front-end found: `db` in `module.exports = db`).

    The bindings are ``{name: model}`` for every name this file holds a model under **at the top
    level** — a variable (`const Booking = sequelize.define('gig', …)`) or a model class — plus
    ``exports.X`` for a model written onto the exports object under the key ``X``, however it is
    spelled: `exports.X = define(…)`, `module.exports.X`, an alias's `db.X`, every link of
    `exports.a = exports.b = define(…)`, a pair `{ X: define(…) }` in the exported literal, and an
    ESM `export const X = define(…)` or `export { A as X }`. :func:`settle` needs them because an
    import names the *binding* it was exported as, which need not be the model's name in any case.
    A `const Draft = define(…)` inside a function is that function's, not the module's: counting
    it let the top-level `Draft` a file exports resolve to a model it does not hold.
    """
    names = import_names if import_names is not None else {}
    nodes = _walk(root)
    sequelize = {local for local, spec in imports.items() if spec == _PACKAGE}
    #: The locals that are Sequelize's `Model` — `import { Model as SeqModel }` included.
    model_base = {local for local in sequelize if names.get(local, local) == "Model"}

    #: `const User = sequelize.define('User', …)` → {User: [(scope start, scope end, model)]}. A
    #: binding reaches only the block that declares it: a function-local `const Post = define(…)`
    #: in the importing file is that function's, not the imported `Post` everywhere else.
    defined: dict[str, list[tuple[int, int, int, str]]] = {}
    #: An ES module's top-level `this` is `undefined`, not `exports`.
    esm = rel.endswith(".mjs") or any(
        c.type in ("import_statement", "export_statement") for c in root.named_children
    )
    whole_file = (root.start_byte, root.end_byte)
    top: dict[str, str] = {}  # the same, top-level declarations only: what a module can export
    exported: dict[str, str] = {}  # `exports.Post = sequelize.define('article', …)` → {exports.Post: …}
    if sequelize:
        for node in nodes:
            if node.type != "call_expression":
                continue
            model = _define(node, module_id, source, rel, batch, sequelize)
            if model is None:
                continue
            parent = node.parent
            while parent is not None and parent.type == "assignment_expression":
                # `exports.a = exports.b = define(…)` exports it under every key in the chain
                left = parent.child_by_field_name("left")
                key = _exports_key(left, source, exports_objects, esm)
                if key is not None:
                    exported[key] = model
                parent = parent.parent
            if parent is None:
                continue
            if parent.type == "variable_declarator":
                name = parent.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    local = _text(name, source)
                    scope = _scope_of(parent)
                    defined.setdefault(local, []).append((*scope, parent.start_byte, model))
                    declaration = parent.parent
                    above = declaration.parent if declaration is not None else None
                    if scope == whole_file or (above is not None and above.type == "export_statement"):
                        top[local] = model  # a top-level `var` in a block or `try` is the module's too
                        if above is not None and above.type == "export_statement":
                            exported[f"exports.{local}"] = model
            elif parent.type == "pair" and _exported_literal(parent.parent, source, exports_objects):
                key = _text(parent.child_by_field_name("key"), source).strip("\"'")
                exported[f"exports.{key}"] = model

    classes: dict[str, str] = {}  # {class name: model name}, for classes with an `init`
    if model_base:
        for cls in _model_classes(nodes, source, model_base):
            model = _init(nodes, cls, module_id, source, rel, batch)
            if model is not None:
                classes[cls] = model

    # A class is a binding the module can export only when it is declared at the top level.
    top_classes = {
        _text(decl.child_by_field_name("name"), source)
        for child in root.named_children
        for decl in (
            [child.child_by_field_name("declaration")] if child.type == "export_statement" else [child]
        )
        if decl is not None and decl.type == "class_declaration"
    }
    held = {**top, **{name: model for name, model in classes.items() if name in top_classes}}
    for child in root.named_children:  # `export class Post extends Model {}`
        decl = child.child_by_field_name("declaration") if child.type == "export_statement" else None
        if decl is not None and decl.type == "class_declaration":
            exported_class = _text(decl.child_by_field_name("name"), source)
            if exported_class in classes:
                exported[f"exports.{exported_class}"] = classes[exported_class]
    for node in root.named_children:  # `export { Post, Draft as Article }`
        if node.type != "export_statement" or node.child_by_field_name("source") is not None:
            continue
        for clause in (c for c in node.named_children if c.type == "export_clause"):
            for spec in (c for c in clause.named_children if c.type == "export_specifier"):
                local = _text(spec.child_by_field_name("name"), source)
                alias = _text(spec.child_by_field_name("alias"), source) or local
                if local in held:
                    exported[f"exports.{alias}"] = held[local]

    registry = _registry_names(nodes, source)
    for call in (n for n in nodes if n.type == "call_expression"):
        _association(call, source, rel, batch, registry, classes, defined, imports, names)
    return {**held, **exported}


def _exports_key(
    left: TSNode | None, source: bytes, exports_objects: frozenset[str], esm: bool = False
) -> str | None:
    """``exports.X`` for `<exports object>.X` — `exports.api.X` is a member of a member, not it.

    The owner must be the *module's* binding: a function whose own `const db = {}` shadows the
    exported `db` writes onto its local, and `db.Post = define(…)` there exports nothing. A
    top-level `this` is `exports`.
    """
    if left is None or left.type != "member_expression":
        return None
    owner = left.child_by_field_name("object")
    if owner is None:
        return None
    key = f"exports.{_text(left.child_by_field_name('property'), source)}"
    from orchestrator.pkg.js_extractor import _shadowed, _top_level_this

    if owner.type == "this":
        return key if not esm and _top_level_this(owner) else None
    text = _text(owner, source)
    if text not in exports_objects:
        return None
    binding = text.split(".", 1)[0]  # `module` of `module.exports`
    return None if _shadowed(owner, binding, source) else key


#: Where a `var` is hoisted to — and so a `let`/`const` block never contains it.
_VAR_SCOPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_static_block",
        "program",
    }
)


def _scope_of(declarator: TSNode) -> tuple[int, int]:
    """The byte range a declared binding covers: a `let`/`const` its nearest enclosing block, a
    `var` its whole function (or the file) — `var` in an `if` or a `try` is not the block's."""
    hoisted = declarator.parent is not None and declarator.parent.type == "variable_declaration"
    stops = _VAR_SCOPES if hoisted else _VAR_SCOPES | {"statement_block"}
    current = declarator.parent
    while current is not None and current.type not in stops:
        current = current.parent
    target = current if current is not None else declarator
    return target.start_byte, target.end_byte


def _in_scope(bindings: list[tuple[int, int, int, str]] | None, at: TSNode) -> str | None:
    """The model a name at ``at`` is bound to: the innermost scope that contains it, and within
    that scope the last declaration before ``at`` — `var Post = a; var Post = b; Post.x()` is
    `b` — or, used above every one of them, the last.

    A use inside a function nested in that scope runs when the function is called, after the
    module has loaded: it reads the *last* declaration, wherever the function is written.
    `var Post = a; function wire() { Post.x() } var Post = b` is `b`.
    """
    innermost: list[tuple[int, int, int, str]] = []
    for binding in bindings or ():
        start, end = binding[0], binding[1]
        if not start <= at.start_byte < end:
            continue
        if innermost and end - start > innermost[0][1] - innermost[0][0]:
            continue
        if innermost and end - start < innermost[0][1] - innermost[0][0]:
            innermost = []
        innermost.append(binding)
    if not innermost:
        return None
    start, end = innermost[0][0], innermost[0][1]
    before = [] if _deferred(at, start, end) else [b for b in innermost if b[2] < at.start_byte]
    return max(before or innermost, key=lambda b: b[2])[3]


def _deferred(at: TSNode, start: int, end: int) -> bool:
    """Whether ``at`` sits in a function that is itself inside the scope ``start``–``end``.

    Code that runs where it is written is not deferred: a `static {}` block, and a function
    called on the spot — `(function () { … })()`, `(() => { … })()`. A callback handed to
    another call is taken to run later; a synchronous one (`[1].forEach(() => …)`) is declared.
    """
    current = at.parent
    while current is not None and start <= current.start_byte:
        if (
            current.type in _VAR_SCOPES
            and current.type not in ("program", "class_static_block")
            and not _called_here(current)
            and ((current.start_byte, current.end_byte) != (start, end) and current.end_byte <= end)
        ):
            return True
        current = current.parent
    return False


def _called_here(function: TSNode) -> bool:
    """`(function () {})()` / `(() => {})()`: a function expression invoked where it is written."""
    callee = function
    while callee.parent is not None and callee.parent.type == "parenthesized_expression":
        callee = callee.parent
    call = callee.parent
    invoked = (
        call.child_by_field_name("function") if call is not None and call.type == "call_expression" else None
    )
    return invoked is not None and invoked.id == callee.id


def _exported_literal(obj: TSNode | None, source: bytes, exports_objects: frozenset[str]) -> bool:
    """Whether ``obj`` is the object literal `module.exports` (or an alias of it) is set to —
    directly, or frozen: `module.exports = Object.freeze({ Post: define(…) })`."""
    if obj is None or obj.type != "object" or obj.parent is None:
        return False
    holder = obj.parent
    if holder.type == "arguments" and holder.parent is not None:
        call = holder.parent
        frozen = _text(call.child_by_field_name("function"), source) in ("Object.freeze", "Object.seal")
        if not frozen or call.parent is None:
            return False
        holder = call.parent
    if holder.type == "assignment_expression":
        from orchestrator.pkg.js_extractor import _shadowed

        left = holder.child_by_field_name("left")
        text = _text(left, source)
        if text not in exports_objects or left is None:
            return False
        # `function f(module) { module.exports = {…} }` assigns a parameter's member, not the module's
        return not _shadowed(left, text.split(".", 1)[0], source)
    if holder.type == "variable_declarator":
        declaration = holder.parent
        top = (
            declaration is not None
            and declaration.parent is not None
            and declaration.parent.type == "program"
        )
        return top and _text(holder.child_by_field_name("name"), source) in exports_objects
    return False


def _model_classes(nodes: list[TSNode], source: bytes, model_base: set[str]) -> list[str]:
    """Classes that descend from Sequelize's ``Model`` in this file, directly or through a base.

    `class BaseModel extends Model {}` then `class Product extends BaseModel {}` is the usual way
    to share hooks between models: the base has no ``init`` and no table, the subclass is the
    model. Read only directly, the subclass was missed; read by shape, the base was minted. So
    descent is followed within the file, and ``init`` decides which of them is a table.
    """
    bases: dict[str, set[str]] = {}
    for node in nodes:
        if node.type == "class_declaration":
            name = _text(node.child_by_field_name("name"), source)
            if name:
                bases[name] = set(_supertypes(node, source))
    derived = set(model_base)  # the binding, not the word: a local class named `Model` is not it
    grew = True
    while grew:
        grew = False
        for name, supers in bases.items():
            if name not in derived and supers & derived:
                derived.add(name)
                grew = True
    return sorted(derived - model_base)


def _sequelize_type(node: TSNode | None, source: bytes, sequelize: set[str]) -> bool:
    """Whether a value is one of Sequelize's column types: `DataTypes.STRING`,
    `Sequelize.DataTypes.TEXT`, the parameterized forms `STRING(120)`, `DECIMAL(10, 2)`,
    `ENUM('new', 'paid')`, and the modified ones `INTEGER.UNSIGNED`, `INTEGER(11).UNSIGNED`,
    `BIGINT.UNSIGNED.ZEROFILL`.

    The type is *somewhere in* the chain, not at its end: reading only the last property missed
    every modifier, so a MySQL join model whose columns are all `INTEGER.UNSIGNED` keys was no
    model at all. Each call is unwrapped to what it parameterizes, and the chain must still end
    at a binding from `sequelize` — `Op.is` does, but names no type anywhere, and is refused.
    """
    typed = False
    while node is not None:
        if node.type == "call_expression":
            node = node.child_by_field_name("function")
        elif node.type == "member_expression":
            typed = typed or _text(node.child_by_field_name("property"), source) in _TYPE_NAMES
            node = node.child_by_field_name("object")
        else:
            break
    return typed and node is not None and node.type == "identifier" and _text(node, source) in sequelize


def _typed_attributes(attributes: TSNode, source: bytes, sequelize: set[str]) -> bool:
    """Whether an attribute map uses a Sequelize type — the marker `define` itself cannot give."""
    for pair in attributes.named_children:
        if pair.type != "pair":
            continue
        value = pair.child_by_field_name("value")
        if _sequelize_type(value, source, sequelize):
            return True
        if value is not None and value.type == "object":
            for inner in value.named_children:
                if (
                    inner.type == "pair"
                    and _text(inner.child_by_field_name("key"), source) == "type"
                    and _sequelize_type(inner.child_by_field_name("value"), source, sequelize)
                ):
                    return True
    return False


def _option(options: TSNode | None, key: str, source: bytes) -> str | None:
    """A literal ``key: '…'`` in an options object — `modelName`, `tableName`."""
    if options is None or options.type != "object":
        return None
    for pair in options.named_children:
        if pair.type == "pair" and _text(pair.child_by_field_name("key"), source) == key:
            return _string(pair.child_by_field_name("value"), source)
    return None


def _define(
    call: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, sequelize: set[str]
) -> str | None:
    """``sequelize.define('user', {…}, {tableName: 'users'})`` → the model name, when it is one."""
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    if _text(fn.child_by_field_name("property"), source) != "define":
        return None
    args = _args(call)
    name = _string(args[0], source) if args else None
    attributes = args[1] if len(args) > 1 and args[1].type == "object" else None
    if name is None or attributes is None or not _typed_attributes(attributes, source, sequelize):
        return None
    table = _option(args[2] if len(args) > 2 else None, "tableName", source)
    _entity(name, table, attributes, module_id, source, rel, call.start_point[0] + 1, batch)
    return name


def _init(
    nodes: list[TSNode], cls: str, module_id: str, source: bytes, rel: str, batch: FactBatch
) -> str | None:
    """``User.init({…}, {modelName, tableName})`` for a Sequelize class → its model name."""
    for node in nodes:
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if (
            fn is None
            or fn.type != "member_expression"
            or _text(fn.child_by_field_name("object"), source) != cls
            or _text(fn.child_by_field_name("property"), source) != "init"
        ):
            continue
        args = _args(node)
        if not args or args[0].type != "object":
            continue
        options = args[1] if len(args) > 1 else None
        model = _option(options, "modelName", source) or cls
        table = _option(options, "tableName", source)
        _entity(model, table, args[0], module_id, source, rel, node.start_point[0] + 1, batch)
        return model
    return None


def _entity(
    name: str,
    table: str | None,
    attributes: TSNode | None,
    module_id: str,
    source: bytes,
    rel: str,
    line: int,
    batch: FactBatch,
) -> None:
    """The id is the *model* name, which associations name; the node's name is the table."""
    eid = entity_id(name)
    batch.add_node(Node(eid, NodeKind.ENTITY, table or name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(module_id, eid, EdgeKind.CONTAINS, Provenance(rel, line)))
    if attributes is None:
        return
    for pair in attributes.named_children:
        if pair.type != "pair":
            continue  # a spread or a shorthand names no column this pass can see
        key = pair.child_by_field_name("key")
        if key is None or key.type not in ("property_identifier", "string"):
            continue
        column = _text(key, source).strip("\"'")
        if not column:
            continue
        fline = pair.start_point[0] + 1
        fid = f"{eid}.{column}"
        batch.add_node(Node(fid, NodeKind.FIELD, column, _LANG, Provenance(rel, fline)))
        batch.add_edge(Edge(eid, fid, EdgeKind.CONTAINS, Provenance(rel, fline)))


def _registry_names(nodes: list[TSNode], source: bytes) -> set[str]:
    """Locals destructured from the models registry: ``const { instrument } = sequelize.models``."""
    out: set[str] = set()
    for node in nodes:
        if node.type != "variable_declarator":
            continue
        pattern = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if (
            pattern is None
            or value is None
            or pattern.type != "object_pattern"
            or value.type != "member_expression"
            or _text(value.child_by_field_name("property"), source) != "models"
        ):
            continue
        for child in pattern.named_children:
            if child.type == "shorthand_property_identifier_pattern":
                out.add(_text(child, source))
    return out


def _model_end(
    node: TSNode | None,
    source: bytes,
    rel: str,
    registry: set[str],
    classes: dict[str, str],
    defined: dict[str, list[tuple[int, int, int, str]]],
    imports: dict[str, str],
    names: dict[str, str | None],
) -> str | None:
    """The entity id an association end names, a placeholder :func:`settle` resolves, or ``None``."""
    if node is None:
        return None
    if node.type == "member_expression":  # sequelize.models.orchestra
        owner = node.child_by_field_name("object")
        if owner is not None and _text(owner.child_by_field_name("property"), source) == "models":
            name = _text(node.child_by_field_name("property"), source)
            return entity_id(name) if name else None
        return None
    if node.type != "identifier":
        return None
    name = _text(node, source)
    if name in classes:
        return entity_id(classes[name])
    local = _in_scope(defined.get(name), node)  # a define binding, in the block that declares it
    if local is not None:
        return entity_id(local)
    if name in registry:
        return entity_id(name)
    if name in imports:
        # `const Author = require('./models/user')`: the local is not the model's name. It names
        # the export it was imported *as* — the whole module, or a named export — known only once
        # that module has been read.
        module = _relative_module(imports[name], rel)
        imported = names.get(name, name)
        return f"{ENTITY_OF}ts:{module}#{imported or _WHOLE}" if module is not None else None
    return None


def _association(
    call: TSNode,
    source: bytes,
    rel: str,
    batch: FactBatch,
    registry: set[str],
    classes: dict[str, str],
    defined: dict[str, list[tuple[int, int, int, str]]],
    imports: dict[str, str],
    names: dict[str, str | None],
) -> None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    kind = _text(fn.child_by_field_name("property"), source)
    if kind not in _ASSOCIATIONS:
        return
    args = _args(call)
    ends = (registry, classes, defined, imports, names)
    receiver = _model_end(fn.child_by_field_name("object"), source, rel, *ends)
    target = _model_end(args[0] if args else None, source, rel, *ends)
    if receiver is None or target is None or receiver == target:
        return  # a self-association keys a row to its own table; Python's reader skips it too
    holder, referenced = (receiver, target) if _ASSOCIATIONS[kind] else (target, receiver)
    batch.add_edge(Edge(holder, referenced, EdgeKind.REFERENCES, Provenance(rel, call.start_point[0] + 1)))


def settle(
    batch: FactBatch,
    cjs: Mapping[str, ExportMap] | None = None,
    models: dict[str, dict[str, str]] | None = None,
) -> FactBatch:
    """Replace each ``ts:entity-of:<module>#<name>`` end with the entity that module defines.

    It reads the same three tiers the front-end routes calls by (``cjs``: see
    ``js_extractor._export_surface``) — one rule for both edge kinds, because a second fallback
    order of its own is how an invented association slipped past the rule calls obeyed.

    A **named** import names an *export*:

    - **dead** — written only onto an object `module.exports` replaced — resolves to nothing;
    - a **readable** or **names-known** map decides: a name it does not list is not exported. A
      listed name is the model written onto the exports under that key (`exports.Post =
      define(…)`, `db.Post = define(…)` on an alias, `{ Post: define(…) }`), or the model the
      declaration it names holds at the top level (`{ Booking }` → `const Booking =
      define('gig')`). A readable name that is neither holds no model. A names-known name with
      no nameable value (`Object.assign(module.exports, { Post: m.post })`) falls through to the
      model's name, below;
    - an **opaque** module, or one with no CommonJS surface (ESM), resolves through a key the
      file visibly writes (`exports.Post = …`, `export const Post = …`), else a model of exactly
      that name, then of that name case aside — `const { Post }` is the `post` model — when
      exactly one matches.

    When the module's default slot holds a model (`const Post = define(…); module.exports =
    Post`), `require` returns that model, and a named import not visibly written as an export
    reads a property of it — nothing.

    A **whole-module or default** import names what `require` returns: the module's default slot
    when it holds a model (`module.exports = Musician`), else the one model the module defines —
    several are ambiguous. Bindings are top-level only (``models``, from :func:`scan`).
    Unresolved, the edge is dropped. A self-association only resolution reveals is dropped too.
    Run by the JavaScript front-end's `finalize`, before its existence check.
    """
    if not any(e.src.startswith(ENTITY_OF) or e.dst.startswith(ENTITY_OF) for e in batch.edges):
        return batch
    maps = cjs or {}
    bindings = models or {}
    by_module: dict[str, set[str]] = {}
    for edge in batch.edges:
        if edge.kind is EdgeKind.CONTAINS and edge.dst.startswith("ts:entity:") and "." not in edge.dst:
            by_module.setdefault(edge.src, set()).add(edge.dst)

    def held_by(module: str, target: str | None) -> str | None:
        """The model a declaration `ts:<module>.<local>` holds at the top level, if any."""
        if target is None or not target.startswith(f"{module}."):
            return None
        local = target[len(module) + 1 :]
        return bindings.get(module, {}).get(local) if "." not in local else None

    def named(module: str, name: str, candidates: set[str]) -> str | None:
        entry = maps.get(module)
        written = bindings.get(module, {}).get(f"exports.{name}")
        if entry is not None and name in entry.dead:
            return None  # written onto an object the module no longer exports
        if entry is not None and name in entry.names:  # a name the file visibly writes: it decides
            if written is not None:
                return entity_id(written)
            target = entry.names[name]
            if target is not None or entry.tier == "readable":
                model = held_by(module, target)
                return entity_id(model) if model is not None else None
            # names-known or opaque, written to a value this pass cannot name: by the model's name —
            # unless `require` returns a model, whose property this cannot be
            if held_by(module, entry.default) is not None:
                return None
        elif entry is not None and entry.tier != "opaque":
            return None  # not exported under that name
        elif written is not None:
            return entity_id(written)
        elif entry is not None and held_by(module, entry.default) is not None:
            return None  # `require` returns a model, and `{ Post }` reads a property of it
        exact = entity_id(name)
        if exact in candidates:
            return exact
        folded = [c for c in candidates if c.lower() == exact.lower()]
        return folded[0] if len(folded) == 1 else None

    def whole(module: str, candidates: set[str]) -> str | None:
        entry = maps.get(module)
        model = held_by(module, entry.default) if entry is not None else None
        if model is not None:
            return entity_id(model)
        return next(iter(candidates)) if len(candidates) == 1 else None

    def resolve(end: str) -> str | None:
        if not end.startswith(ENTITY_OF):
            return end
        module, _, imported = end[len(ENTITY_OF) :].rpartition("#")
        candidates = by_module.get(module, set())
        found = whole(module, candidates) if imported == _WHOLE else named(module, imported, candidates)
        return found if found in candidates else None

    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        if edge.kind is EdgeKind.REFERENCES:
            src, dst = resolve(edge.src), resolve(edge.dst)
            if src is None or dst is None or src == dst:
                continue
            edge = Edge(src, dst, edge.kind, edge.provenance)
        out.add_edge(edge)
    return out


__all__ = ["ENTITY_OF", "entity_id", "scan", "settle"]
