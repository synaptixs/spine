"""Go front-end for the PKG extractor (the 8th language).

Maps Go source onto the same universal ``facts`` vocabulary the other front-ends use, so
the knowledge graph stays language-neutral. Parsing is via tree-sitter (accurate ASTs);
it's an OPTIONAL dependency behind the ``go`` extra
(``uv pip install 'orchestrator[go]'``), lazy-imported so the base install stays
stdlib-only and importing this module never fails.

**Go's module unit is the package = its directory** — every ``.go`` file in a directory
shares one ``Module`` node. This is the first front-end where ``module_name`` returns a
*directory* rather than a per-file name; the dispatcher merges every file's facts under
that one id. Ids are prefixed ``go:``.

Phase 4.1 emitted the declaration graph: ``Module`` (package/dir), ``Type`` (struct /
interface / alias), ``Function`` (top-level funcs, receiver methods, interface method
specs), ``Field`` (named struct fields); ``IMPORTS`` + ``CONTAINS``. Phase 4.3 adds the
deeper edges, precision-first:

- ``CALLS`` — resolved **within a file**: an unqualified call ``Foo()`` to a package-level
  function declared in this file, and a receiver-method call ``r.M()`` to a method of the
  receiver's own type. Cross-file/cross-package and interface-value calls need a
  package-wide symbol table / type inference and are **not** emitted (a guess poisons
  grounding).
- ``REFERENCES`` — a struct field whose type is another named type in the same package
  (``next *Node`` → the ``Node`` type); qualified / slice / map field types are skipped.
- ``IMPLEMENTS`` — the net-new **method-set match**, computed in a whole-repo ``finalize``
  pass (interfaces and their implementors span files): a concrete type implements an
  in-repo interface when the type's method **signature** set — name + arity, value + pointer
  receivers — is a superset of the interface's (embedded interfaces expanded). Arity guards
  the common cross-package false positive (look-alike method names, different parameter
  counts). Matched by name+arity (not the full type signature) and scoped to in-repo types —
  see the caveats in ``docs/specs/go-support-roadmap.md``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

# Predeclared types — never the target of an in-repo REFERENCES edge.
_GO_BUILTIN_TYPES = frozenset(
    [
        "bool",
        "string",
        "error",
        "any",
        "byte",
        "rune",
        "uintptr",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float32",
        "float64",
        "complex64",
        "complex128",
    ]
)


# A method signature key for set-matching: (name, arity). Arity (parameter count) is what
# separates, e.g., a gRPC client's `SayHello(ctx, req, ...opts)` from a server's
# `SayHello(ctx, req)` — a name-only match declares false IMPLEMENTS across such look-alikes.
_Sig = tuple[str, int]


@dataclass
class _Iface:
    """A captured interface's direct method signatures + embedded (bare) interface names."""

    methods: frozenset[_Sig]
    embeds: frozenset[str]


@dataclass
class _Body:
    """A function/method body queued for the per-file CALLS resolution pass."""

    caller_id: str
    recv_var: str  # the receiver variable name (``""`` for a plain func / unnamed receiver)
    recv_type: str  # the receiver's type name (``""`` for a plain func)
    node: TSNode


class GoExtractor:
    """Go front-end (tree-sitter). Install the ``go`` extra to use it."""

    language: str = "go"
    suffixes: tuple[str, ...] = (".go",)

    def __init__(self) -> None:
        # Accumulated across the files of one repo walk, consumed (and cleared) by
        # ``finalize`` — interfaces and their implementors span files. ``_interfaces`` maps an
        # interface id to its signatures; ``_concrete_sigs`` maps a concrete type id to the
        # signatures of its methods (value + pointer receivers, merged across files).
        self._interfaces: dict[str, _Iface] = {}
        self._concrete_sigs: dict[str, set[_Sig]] = defaultdict(set)

    def module_name(self, path: Path, root: Path) -> str:
        # Go's compilation unit is the PACKAGE, which is the directory (all files in a dir
        # declare the same package). Return the repo-relative directory so every .go file
        # in it merges into one Module node — unlike every other front-end (one per file).
        rel = path.resolve().relative_to(root.resolve())
        parent = rel.parent.as_posix()
        return "" if parent == "." else parent

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _go_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"go:{module}" if module else "go:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or "<root>", "go", Provenance(rel, 1)))

        # Pass 1: emit declarations; collect this file's package-level func names, per-type
        # method names, and the bodies to resolve calls in (all file-local, so CALLS stay precise).
        local_funcs: set[str] = set()
        type_methods: dict[str, set[str]] = defaultdict(set)
        bodies: list[_Body] = []
        for node in tree.root_node.named_children:
            kind = node.type
            if kind == "import_declaration":
                self._imports(node, module_id, source, rel, batch)
            elif kind == "type_declaration":
                self._type_decl(node, module_id, source, rel, batch)
            elif kind == "function_declaration":
                self._function(node, module_id, source, rel, batch, local_funcs, bodies)
            elif kind == "method_declaration":
                self._method(node, module_id, source, rel, batch, type_methods, bodies)

        # Pass 2: resolve the precisely-resolvable call sites in each body.
        for body in bodies:
            self._resolve_calls(body, module_id, source, rel, batch, local_funcs, type_methods)
        return batch

    def _imports(self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
        """Emit IMPORTS edges to external package nodes (single or grouped import form)."""
        for spec in _descendants(node, "import_spec"):
            imp = _field_text(spec, "path", source).strip('"`')
            if not imp:
                continue
            tid = f"go:{imp}"
            batch.add_node(Node(tid, NodeKind.MODULE, imp, "go", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, spec.start_point[0] + 1)))

    def _type_decl(self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
        """A `type` declaration — one spec, or several in a grouped `type ( … )` block."""
        for spec in node.named_children:
            if spec.type != "type_spec":
                continue
            name = _field_text(spec, "name", source)
            if not name:
                continue
            type_id = f"{module_id}.{name}"
            line = spec.start_point[0] + 1
            batch.add_node(
                Node(type_id, NodeKind.TYPE, name, "go", Provenance(rel, line, spec.end_point[0] + 1))
            )
            batch.add_edge(Edge(module_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))
            underlying = spec.child_by_field_name("type")
            if underlying is None:
                continue
            if underlying.type == "struct_type":
                self._struct_fields(underlying, type_id, module_id, source, rel, batch)
            elif underlying.type == "interface_type":
                self._interface_methods(underlying, type_id, source, rel, batch)

    def _struct_fields(
        self, struct: TSNode, type_id: str, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        """Named struct fields → Field nodes; a field whose type is another named type in the
        same package → a REFERENCES edge. Embedded (anonymous) fields carry no name and are
        skipped in the field list — they matter for promoted-method resolution (a documented
        IMPLEMENTS limit)."""
        for flist in struct.named_children:
            if flist.type != "field_declaration_list":
                continue
            for fdecl in flist.named_children:
                if fdecl.type != "field_declaration":
                    continue
                line = fdecl.start_point[0] + 1
                for nm in _field_children(fdecl, "name"):
                    fname = _text(nm, source)
                    fid = f"{type_id}.{fname}"
                    batch.add_node(Node(fid, NodeKind.FIELD, fname, "go", Provenance(rel, line)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
                base = _base_type_name(fdecl.child_by_field_name("type"), source)
                if base and base not in _GO_BUILTIN_TYPES and _field_children(fdecl, "name"):
                    batch.add_edge(
                        Edge(type_id, f"{module_id}.{base}", EdgeKind.REFERENCES, Provenance(rel, line))
                    )

    def _interface_methods(
        self, iface: TSNode, type_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        """Interface method specs → Function nodes owned by the interface Type; capture the
        interface's method + embedded-interface names for the IMPLEMENTS pass."""
        methods: set[_Sig] = set()
        embeds: set[str] = set()
        for member in iface.named_children:
            if member.type == "method_elem":
                mname = _field_text(member, "name", source)
                if not mname:
                    continue
                methods.add((mname, _arity(member.child_by_field_name("parameters"))))
                fid = f"{type_id}.{mname}"
                line = member.start_point[0] + 1
                batch.add_node(Node(fid, NodeKind.FUNCTION, mname, "go", Provenance(rel, line)))
                batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
            elif member.type == "type_elem":
                emb = _base_type_name(member.named_children[0] if member.named_children else None, source)
                if emb:
                    embeds.add(emb)  # embedded interface (same-package, bare name)
        self._interfaces[type_id] = _Iface(frozenset(methods), frozenset(embeds))

    def _function(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        local_funcs: set[str],
        bodies: list[_Body],
    ) -> None:
        """A package-level `func Name(…)` → Function owned by the module."""
        name = _field_text(node, "name", source)
        if not name:
            return
        fid = f"{module_id}.{name}"
        line = node.start_point[0] + 1
        batch.add_node(Node(fid, NodeKind.FUNCTION, name, "go", Provenance(rel, line)))
        batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        local_funcs.add(name)
        body = node.child_by_field_name("body")
        if body is not None:
            bodies.append(_Body(fid, "", "", body))

    def _method(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        type_methods: dict[str, set[str]],
        bodies: list[_Body],
    ) -> None:
        """A `func (r Recv) Name(…)` → Function owned by its receiver Type. The receiver
        type may be declared in another file of the same package; the shared (dir-based)
        module id keeps the id namespace consistent, so the CONTAINS edge still resolves."""
        name = _field_text(node, "name", source)
        recv_var, recv_type = _receiver(node, source)
        if not name or not recv_type:
            return
        type_id = f"{module_id}.{recv_type}"
        fid = f"{type_id}.{name}"
        line = node.start_point[0] + 1
        batch.add_node(Node(fid, NodeKind.FUNCTION, name, "go", Provenance(rel, line)))
        batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        type_methods[recv_type].add(name)
        self._concrete_sigs[type_id].add((name, _arity(node.child_by_field_name("parameters"))))
        body = node.child_by_field_name("body")
        if body is not None:
            bodies.append(_Body(fid, recv_var, recv_type, body))

    def _resolve_calls(
        self,
        body: _Body,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        local_funcs: set[str],
        type_methods: dict[str, set[str]],
    ) -> None:
        """Emit CALLS for the two precisely-resolvable call shapes in a body: unqualified
        calls to a same-file package function, and `recv.M()` to a method of the receiver's
        own type. Everything else (cross-package, interface values, other objects) is left
        unresolved rather than guessed."""
        for call in _all_of_type(body.node, "call_expression"):
            fn = call.child_by_field_name("function")
            if fn is None:
                continue
            target: str | None = None
            if fn.type == "identifier":
                name = _text(fn, source)
                if name in local_funcs:
                    target = f"{module_id}.{name}"
            elif fn.type == "selector_expression":
                operand = fn.child_by_field_name("operand")
                field = fn.child_by_field_name("field")
                if (
                    operand is not None
                    and field is not None
                    and operand.type == "identifier"
                    and body.recv_var
                    and _text(operand, source) == body.recv_var
                ):
                    m = _text(field, source)
                    if m in type_methods.get(body.recv_type, ()):
                        target = f"{module_id}.{body.recv_type}.{m}"
            if target is not None:
                batch.add_edge(
                    Edge(body.caller_id, target, EdgeKind.CALLS, Provenance(rel, call.start_point[0] + 1))
                )

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Whole-repo pass: emit ``IMPLEMENTS`` where a concrete type's method **signature**
        set (name + arity, value + pointer receivers) is a superset of an in-repo interface's
        (embedded interfaces expanded). Matched by name+arity — not the full type signature —
        and scoped to in-repo types. Clears the caches so a later extraction starts clean."""
        interfaces = self._interfaces
        concrete_sigs = self._concrete_sigs
        self._interfaces = {}
        self._concrete_sigs = defaultdict(set)
        if not interfaces or not concrete_sigs:
            return batch

        def _iface_set(iid: str, seen: set[str]) -> set[_Sig]:
            if iid in seen:
                return set()
            seen.add(iid)
            info = interfaces.get(iid)
            if info is None:
                return set()
            out = set(info.methods)
            pkg = iid.rsplit(".", 1)[0]  # embedded interfaces resolve within the same package
            for emb in info.embeds:
                out |= _iface_set(f"{pkg}.{emb}", seen)
            return out

        iface_sigs = {iid: _iface_set(iid, set()) for iid in interfaces}
        iface_sigs = {iid: s for iid, s in iface_sigs.items() if s}  # skip empty interfaces
        if not iface_sigs:
            return batch

        prov_of = {n.id: n.provenance for n in batch.nodes}
        for cid, csigs in concrete_sigs.items():
            if cid in interfaces:
                continue  # a type is either an interface or a concrete implementor, not both
            for iid, isigs in iface_sigs.items():
                if iid != cid and isigs <= csigs:
                    prov = prov_of.get(cid) or Provenance("", 1)
                    batch.add_edge(Edge(cid, iid, EdgeKind.IMPLEMENTS, prov))
        return batch


def _arity(params: TSNode | None) -> int:
    """Parameter count of a ``parameter_list`` — grouped names count individually
    (``a, b int`` = 2), a type-only or variadic parameter counts as one."""
    if params is None:
        return 0
    total = 0
    for child in params.named_children:
        if child.type == "parameter_declaration":
            total += max(1, len(_field_children(child, "name")))
        elif child.type == "variadic_parameter_declaration":
            total += 1
    return total


def _receiver(node: TSNode, source: bytes) -> tuple[str, str]:
    """``(receiver var name, receiver type name)`` of a method; ``("", "")`` if absent."""
    recv = node.child_by_field_name("receiver")
    if recv is None:
        return "", ""
    for pd in _descendants(recv, "parameter_declaration"):
        var = _field_text(pd, "name", source)
        return var, _base_type_name(pd.child_by_field_name("type"), source)
    return "", ""


def _base_type_name(t: TSNode | None, source: bytes) -> str:
    """`T`, `*T`, `T[X]`, `*T[X]` → `T` (a bare in-repo type name); qualified/slice/map → ``""``."""
    if t is None:
        return ""
    if t.type == "pointer_type":
        return _base_type_name(t.named_children[0] if t.named_children else None, source)
    if t.type == "generic_type":
        return _text(t.child_by_field_name("type"), source)
    if t.type == "type_identifier":
        return _text(t, source)
    return ""


def _descendants(node: TSNode, type_name: str) -> list[TSNode]:
    """Descendants of a type, not recursing into a match once found."""
    out: list[TSNode] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type == type_name:
            out.append(n)
        else:
            stack.extend(n.named_children)
    return out


def _all_of_type(node: TSNode, type_name: str) -> list[TSNode]:
    """Every descendant of a type (recurses through matches too — nested calls count)."""
    out: list[TSNode] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type == type_name:
            out.append(n)
        stack.extend(n.named_children)
    return out


def _field_children(node: TSNode, field: str) -> list[TSNode]:
    """Every direct child of ``node`` bound to the named field (e.g. multi-name fields)."""
    return [node.children[i] for i in range(node.child_count) if node.field_name_for_child(i) == field]


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _go_parser() -> Any:
    try:
        import tree_sitter_go
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Go extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-go>=0.21'"
        ) from exc
    language = Language(tree_sitter_go.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["GoExtractor"]
