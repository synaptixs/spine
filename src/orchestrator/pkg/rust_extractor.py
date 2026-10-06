"""Precision-first Rust source facts, scoped by Cargo package and crate target.

The module tree is indexed before facts are emitted. Macro expansion and Cargo
execution are intentionally absent from this comprehension front-end.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.rust_cargo import CargoIndex, CargoTarget

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode


def _parser() -> Any:
    import tree_sitter_rust_orchard
    from tree_sitter import Language, Parser

    return Parser(Language(tree_sitter_rust_orchard.language()))


def _text(node: TSNode | None, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace") if node else ""


def _field(node: TSNode, name: str, src: bytes) -> str:
    return _text(node.child_by_field_name(name), src)


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(reversed(current.named_children))
    return out


def _type_constructor(node: TSNode | None, src: bytes) -> str:
    if node is None:
        return ""
    while node.type in {"generic_type", "reference_type", "pointer_type"}:
        inner = node.child_by_field_name("type") or next(iter(node.named_children), None)
        if inner is None:
            break
        node = inner
    return _text(node, src)


def _pattern_names(node: TSNode | None, src: bytes) -> set[str]:
    if node is None:
        return set()
    if node.type == "identifier":
        return {_text(node, src)}
    if node.type in {"type_identifier", "scoped_identifier", "scoped_type_identifier"}:
        return set()
    names: set[str] = set()
    for child in node.named_children:
        names.update(_pattern_names(child, src))
    return names


def _binding_pattern(node: TSNode) -> TSNode | None:
    pattern = node.child_by_field_name("pattern")
    if pattern is not None:
        return pattern
    return next(
        (
            child
            for child in node.named_children
            if child.type
            in {"identifier", "tuple_pattern", "struct_pattern", "tuple_struct_pattern", "closure_parameters"}
        ),
        None,
    )


@dataclass(frozen=True)
class _Context:
    target: CargoTarget | None
    module: str
    module_dir: Path
    standalone: bool = False


@dataclass(frozen=True)
class _Call:
    caller: str
    module: str
    owner: str | None
    trait: str | None
    callee: str
    rel: str
    line: int
    shadowed: frozenset[str]


@dataclass(frozen=True)
class _Import:
    module: str
    path: str
    binding: str | None
    public: bool
    provenance: Provenance


class RustExtractor:
    language = "rust"
    suffixes: tuple[str, ...] = (".rs",)

    def __init__(self) -> None:
        self.index: CargoIndex | None = None
        self._contexts: dict[Path, list[_Context]] = defaultdict(list)
        self._sources: dict[Path, bytes] = {}
        self._trees: dict[Path, TSNode] = {}
        self._calls: list[_Call] = []
        self._imports: list[_Import] = []
        self._impls: list[tuple[str, str, str, Provenance]] = []
        self._refs: list[tuple[str, str, str, Provenance]] = []
        self.known_gaps: set[str] = set()

    def prepare(self, root: Path, paths: list[Path]) -> None:
        self.index = CargoIndex(root)
        self._contexts.clear()
        self._sources.clear()
        self._trees.clear()
        self._calls.clear()
        self._imports.clear()
        self._impls.clear()
        self._refs.clear()
        self.known_gaps.clear()
        if self.index.targets:
            for target in self.index.targets:
                self._index_file(target.source, _Context(target, target.scope, target.source.parent), set())
        elif self.index.workspace_root is None:
            for path in paths:
                if path.suffix == ".rs":
                    rel = path.relative_to(root).as_posix()
                    scope = f"rust:<standalone>@file/{rel}"
                    self._index_file(path, _Context(None, scope, path.parent, standalone=True), set())

    def _parse(self, path: Path) -> tuple[bytes, TSNode]:
        if path not in self._trees:
            src = path.read_bytes()
            self._sources[path] = src
            tree = _parser().parse(src).root_node
            self._trees[path] = tree
            if tree.has_error:
                self.known_gaps.add("parser ERROR span")
        return self._sources[path], self._trees[path]

    def _index_file(self, path: Path, context: _Context, active: set[tuple[Path, str]]) -> None:
        key = (path, context.module)
        if key in active or not path.is_file():
            return
        active = active | {key}
        if context not in self._contexts[path]:
            self._contexts[path].append(context)
        src, root = self._parse(path)
        self._index_mods(root, src, path, context, active)

    def _index_mods(
        self, parent: TSNode, src: bytes, path: Path, context: _Context, active: set[tuple[Path, str]]
    ) -> None:
        siblings = parent.named_children
        for i, node in enumerate(siblings):
            if node.type != "mod_item" or node.has_error:
                continue
            name = _field(node, "name", src)
            if not name:
                continue
            child = f"{context.module}::{name}"
            body = node.child_by_field_name("body")
            if body is not None:
                self._index_mods(
                    body, src, path, _Context(context.target, child, context.module_dir / name), active
                )
                continue
            explicit: str | None = None
            attributes = node.child_by_field_name("attributes")
            if attributes is not None:
                path_attributes = attributes.named_children
            else:
                path_attributes = []
                for previous in reversed(siblings[:i]):
                    if previous.type != "attribute_item":
                        break
                    path_attributes.append(previous)
            for attribute in path_attributes:
                raw = _text(attribute, src)
                if raw.startswith("#[path"):
                    value = next((n for n in _walk(attribute) if n.type == "string_literal"), None)
                    if value is not None:
                        explicit = _text(value, src).strip('"')
            if explicit is not None:
                candidate = context.module_dir / explicit
            else:
                first = context.module_dir / f"{name}.rs"
                second = context.module_dir / name / "mod.rs"
                candidate = first if first.is_file() else second
            if candidate.is_file():
                next_dir = (
                    candidate.parent if candidate.name == "mod.rs" else candidate.parent / candidate.stem
                )
                self._index_file(candidate, _Context(context.target, child, next_dir), active)
            else:
                self.known_gaps.add("unresolved outlined module")

    def module_name(self, path: Path, root: Path) -> str:
        contexts = self._contexts.get(path, ())
        return contexts[0].module if contexts else ""

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        batch = FactBatch()
        contexts = self._contexts.get(path, ())
        if not contexts:
            return batch  # Cargo project: an unowned fixture is not compiled source.
        src, tree = self._parse(path)
        for context in contexts:
            self._emit_module(batch, context.module, rel, 1, edge=False)
            self._declarations(tree, src, rel, batch, context.module)
        return batch

    def _emit_module(self, batch: FactBatch, module: str, rel: str, line: int, *, edge: bool = True) -> None:
        name = module.rsplit("::", 1)[-1]
        prov = Provenance(rel, line)
        batch.add_node(Node(module, NodeKind.MODULE, name, "rust", prov))
        if edge and "::" in module:
            parent = module.rsplit("::", 1)[0]
            batch.add_node(Node(parent, NodeKind.MODULE, parent.rsplit("::", 1)[-1], "rust", prov))
            batch.add_edge(Edge(parent, module, EdgeKind.CONTAINS, prov))

    def _declarations(self, parent: TSNode, src: bytes, rel: str, batch: FactBatch, module: str) -> None:
        for node in parent.named_children:
            if node.has_error and node.type == "ERROR":
                self.known_gaps.add("parser ERROR span")
                continue
            kind = node.type
            line = node.start_point[0] + 1
            prov = Provenance(rel, line, node.end_point[0] + 1)
            name = _field(node, "name", src)
            if kind == "mod_item" and name:
                child = f"{module}::{name}"
                self._emit_module(batch, child, rel, line)
                body = node.child_by_field_name("body")
                if body is not None:
                    self._declarations(body, src, rel, batch, child)
            elif kind in {"struct_item", "enum_item", "union_item", "trait_item", "type_item"} and name:
                symbol = self._declare(batch, module, name, NodeKind.TYPE, prov)
                if kind in {"struct_item", "union_item"}:
                    self._fields(node.child_by_field_name("body"), src, rel, batch, symbol, module)
                elif kind == "enum_item":
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for variant in body.named_children:
                            if variant.type == "enum_variant":
                                vname = _field(variant, "name", src)
                                if vname:
                                    vid = self._declare(
                                        batch,
                                        symbol,
                                        vname,
                                        NodeKind.FIELD,
                                        Provenance(rel, variant.start_point[0] + 1),
                                    )
                                    self._fields(
                                        variant.child_by_field_name("body"), src, rel, batch, vid, module
                                    )
                elif kind == "trait_item":
                    bounds = next((c for c in node.named_children if c.type == "trait_bounds"), None)
                    if bounds is not None:
                        for bound in bounds.named_children:
                            target = _type_constructor(bound, src)
                            if target:
                                self._impls.append((symbol, target, module, prov))
                    self._associated(node.child_by_field_name("body"), src, rel, batch, symbol, module, None)
            elif kind == "impl_item":
                self._impl(node, src, rel, batch, module)
            elif kind in {"function_item", "function_signature_item"} and name:
                fid = self._declare(batch, module, name, NodeKind.FUNCTION, prov)
                self._queue_calls(node, src, rel, fid, module, None, None)
            elif kind in {"const_item", "static_item"} and name:
                self._declare(batch, module, name, NodeKind.FIELD, prov)
            elif kind == "use_declaration":
                self._use(node, src, rel, module)
            elif kind == "extern_crate_declaration":
                raw = _text(node, src).removeprefix("extern crate ").rstrip(";")
                path = raw.split(" as ")[0]
                self._imports.append(_Import(module, path, raw.split(" as ")[-1], False, prov))

    @staticmethod
    def _declare(batch: FactBatch, owner: str, name: str, kind: NodeKind, prov: Provenance) -> str:
        symbol = f"{owner}::{name}"
        batch.add_node(Node(symbol, kind, name, "rust", prov))
        batch.add_edge(Edge(owner, symbol, EdgeKind.CONTAINS, prov))
        return symbol

    def _fields(
        self, body: TSNode | None, src: bytes, rel: str, batch: FactBatch, owner: str, module: str
    ) -> None:
        if body is None:
            return
        ordinal = 0
        for child in body.named_children:
            if child.type == "field_declaration":
                name = _field(child, "name", src)
            elif child.type in {
                "type_identifier",
                "generic_type",
                "primitive_type",
                "reference_type",
                "scoped_type_identifier",
                "tuple_type",
                "array_type",
            }:
                name = str(ordinal)
                ordinal += 1
            else:
                continue
            if not name:
                continue
            prov = Provenance(rel, child.start_point[0] + 1)
            field = self._declare(batch, owner, name, NodeKind.FIELD, prov)
            type_node = child.child_by_field_name("type") if child.type == "field_declaration" else child
            if type_node is not None:
                for candidate in _walk(type_node):
                    if candidate.type == "type_identifier":
                        self._refs.append((field, _text(candidate, src), module, prov))

    def _associated(
        self,
        body: TSNode | None,
        src: bytes,
        rel: str,
        batch: FactBatch,
        owner: str,
        module: str,
        trait: str | None,
    ) -> None:
        if body is None:
            return
        for child in body.named_children:
            name = _field(child, "name", src)
            if not name:
                continue
            prov = Provenance(rel, child.start_point[0] + 1, child.end_point[0] + 1)
            if child.type in {"function_item", "function_signature_item"}:
                method_owner = f"{owner}::<{trait}>" if trait else owner
                fid = f"{method_owner}::{name}"
                batch.add_node(Node(fid, NodeKind.FUNCTION, name, "rust", prov))
                batch.add_edge(Edge(owner, fid, EdgeKind.CONTAINS, prov))
                self._queue_calls(child, src, rel, fid, module, owner, trait)
            elif child.type in {"const_item", "static_item"}:
                self._declare(batch, owner, name, NodeKind.FIELD, prov)

    def _impl(self, node: TSNode, src: bytes, rel: str, batch: FactBatch, module: str) -> None:
        self_type = node.child_by_field_name("type")
        trait_node = node.child_by_field_name("trait")
        if self_type is None:
            return
        name = _type_constructor(self_type, src)
        params = {_field(n, "name", src) for n in _walk(node) if n.type == "type_parameter"}
        if not name or name in params or name.startswith("!"):
            return  # Blanket or negative impl cannot ground one concrete owner.
        owner = f"{module}::{name}" if "::" not in name else self._normalize_path(name, module)
        prov = Provenance(rel, node.start_point[0] + 1)
        if not any(n.id == owner for n in batch.nodes):
            batch.add_node(Node(owner, NodeKind.TYPE, name.rsplit("::", 1)[-1], "rust", external=True))
        trait = _type_constructor(trait_node, src) if trait_node else None
        if trait and "!" not in _text(node, src).split("for", 1)[0]:
            self._impls.append((owner, trait, module, prov))
        self._associated(node.child_by_field_name("body"), src, rel, batch, owner, module, trait)

    def _normalize_path(self, path: str, module: str) -> str:
        scope = module.split("::", 1)[0]
        if path.startswith("crate::"):
            return scope + "::" + path[7:]
        if path.startswith("self::"):
            return module + "::" + path[6:]
        if path.startswith("super::"):
            return module.rsplit("::", 1)[0] + "::" + path[7:]
        return module + "::" + path

    def _use(self, node: TSNode, src: bytes, rel: str, module: str) -> None:
        prov = Provenance(rel, node.start_point[0] + 1)
        public = any(child.type == "visibility_modifier" for child in node.named_children)

        def add(path: str, binding: str | None) -> None:
            self._imports.append(_Import(module, path, binding, public, prov))

        def visit(item: TSNode, prefix: str = "") -> None:
            kind = item.type
            if kind == "scoped_use_list":
                head = _field(item, "path", src)
                for child in item.named_children:
                    if child.type == "use_list":
                        for member in child.named_children:
                            visit(member, f"{prefix}{head}::")
            elif kind == "use_list":
                for child in item.named_children:
                    visit(child, prefix)
            elif kind == "use_as_clause":
                path = prefix + _field(item, "path", src)
                alias = _text(item.named_children[-1], src)
                add(path, alias)
            elif kind == "use_wildcard":
                add(prefix.rstrip(":"), None)
            elif kind == "self":
                path = prefix.removesuffix("::")
                add(path, path.rsplit("::", 1)[-1])
            elif kind in {"scoped_identifier", "identifier", "crate", "super"}:
                path = prefix + _text(item, src)
                add(path, path.rsplit("::", 1)[-1])

        for child in node.named_children:
            if child.type not in {"visibility_modifier", "attribute_item"}:
                visit(child)

    def _queue_calls(
        self,
        node: TSNode,
        src: bytes,
        rel: str,
        caller: str,
        module: str,
        owner: str | None,
        trait: str | None,
    ) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            return
        parameters: set[str] = set()
        params = next((c for c in node.named_children if c.type == "parameters"), None)
        if params is not None:
            for p in params.named_children:
                if p.type == "parameter":
                    parameters.update(_pattern_names(_binding_pattern(p), src))
        for call in _walk(body):
            if call.type != "call_expression":
                continue
            callee = _field(call, "function", src)
            if not callee:
                continue
            shadowed = set(parameters)
            branch = call
            parent = call.parent
            nested_callable = False
            while parent is not None and parent != node:
                if parent.type in {"function_item", "closure_expression"}:
                    nested_callable = True
                    break
                if parent.type in {"for_expression", "match_arm"}:
                    shadowed.update(_pattern_names(_binding_pattern(parent), src))
                if parent.type == "block":
                    for previous in parent.named_children:
                        if previous.end_byte > branch.start_byte:
                            break
                        if previous.type == "let_declaration":
                            shadowed.update(_pattern_names(_binding_pattern(previous), src))
                branch = parent
                parent = parent.parent
            if nested_callable:
                self.known_gaps.add("nested callable bodies deferred")
                continue
            self._calls.append(
                _Call(caller, module, owner, trait, callee, rel, call.start_point[0] + 1, frozenset(shadowed))
            )

    def finalize(self, batch: FactBatch) -> FactBatch:
        nodes = {n.id: n for n in batch.nodes}
        duplicates: set[str] = set()
        counts: dict[str, int] = defaultdict(int)
        for edge in batch.edges:
            if (
                edge.kind == EdgeKind.CONTAINS
                and nodes.get(edge.dst)
                and nodes[edge.dst].kind == NodeKind.FUNCTION
            ):
                counts[edge.dst] += 1
        # Duplicate cfg variants coalesce as one node, but are not one callable target.
        duplicates = {key for key, count in counts.items() if count > 1}
        if duplicates:
            self.known_gaps.add("cfg-ambiguous duplicate declarations")

        bound: dict[str, str] = {}
        external_aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
        for item in self._imports:
            if item.binding and item.path.split("::", 1)[0] in {"std", "core", "alloc"}:
                external_aliases[(item.module, item.binding)].add(item.path)

        def external_path(path: str, module: str) -> str:
            head, separator, tail = path.partition("::")
            if separator:
                imports = external_aliases.get((module, head), set())
                if len(imports) == 1:
                    return f"{next(iter(imports))}::{tail}"
            return path

        def resolve(
            path: str, module: str, kind: NodeKind | None = None, *, bindings_allowed: bool = True
        ) -> str | None:
            options: list[str] = []
            if path.startswith(("crate::", "self::", "super::")):
                options.append(self._normalize_path(path, module))
            elif "::" in path:
                options.append(self._normalize_path(path, module))
                options.append(module.split("::", 1)[0] + "::" + path)
            else:
                options.extend((f"{module}::{path}", f"{module.split('::', 1)[0]}::{path}"))
            if (
                self.index is not None
                and "::" in path
                and not path.startswith(("crate::", "self::", "super::"))
            ):
                alias, rest = path.split("::", 1)
                package_name = module.split("@", 1)[0].removeprefix("rust:")
                package = self.index.packages.get(package_name)
                dep_dir = package.path_dependencies.get(alias) if package else None
                dependency = next((p for p in self.index.packages.values() if p.directory == dep_dir), None)
                if dependency is not None:
                    lib = next((t for t in dependency.targets if t.kind == "lib"), None)
                    if lib is not None:
                        options.append(f"{lib.scope}::{rest}")
            found = [
                key
                for key in dict.fromkeys(options)
                if key in nodes and (kind is None or nodes[key].kind == kind) and nodes[key].grounded
            ]
            if not found and bindings_allowed:
                found = [
                    bound[key]
                    for key in dict.fromkeys(options)
                    if key in bound
                    and bound[key] in nodes
                    and (kind is None or nodes[bound[key]].kind == kind)
                ]
            return found[0] if len(found) == 1 else None

        for _ in range(len(self._imports) + 1):
            before = len(bound)
            for item in self._imports:
                if item.binding:
                    target = resolve(item.path, item.module)
                    if target:
                        bound[f"{item.module}::{item.binding}"] = target
            if len(bound) == before:
                break
        for item in self._imports:
            target = resolve(item.path, item.module)
            if target is None:
                target = f"rust:external::{item.path}"
                batch.add_node(Node(target, NodeKind.MODULE, item.path, "rust", external=True))
            batch.add_edge(Edge(item.module, target, EdgeKind.IMPORTS, item.provenance))
        for source, path, module, prov in self._impls:
            target = resolve(path, module, NodeKind.TYPE)
            if target is None:
                target = f"rust:external::{external_path(path, module)}"
                batch.add_node(Node(target, NodeKind.TYPE, path.rsplit("::", 1)[-1], "rust", external=True))
            batch.add_edge(Edge(source, target, EdgeKind.IMPLEMENTS, prov))
        for source, path, module, prov in self._refs:
            target = resolve(path, module, NodeKind.TYPE)
            if target:
                batch.add_edge(Edge(source, target, EdgeKind.REFERENCES, prov))
        for call in self._calls:
            callee = call.callee
            target = None
            if "::" not in callee and "." not in callee and callee not in call.shadowed:
                target = resolve(callee, call.module, NodeKind.FUNCTION, bindings_allowed=False)
            elif call.owner and callee.startswith("self.") and call.trait is None:
                candidate = f"{call.owner}::{callee[5:]}"
                target = candidate if candidate in nodes else None
            elif call.owner and callee.startswith("Self::"):
                suffix = callee[6:]
                candidate = (
                    f"{call.owner}::<{call.trait}>::{suffix}" if call.trait else f"{call.owner}::{suffix}"
                )
                target = candidate if candidate in nodes else None
            elif (
                "::" in callee
                and callee.count("::") == 1
                and not callee.startswith(("crate::", "self::", "super::"))
            ):
                typ, method = callee.split("::")
                owner = resolve(typ, call.module, NodeKind.TYPE)
                candidate = f"{owner}::{method}" if owner else ""
                target = candidate if candidate in nodes else None
            if target and target not in duplicates and nodes.get(target) and nodes[target].grounded:
                batch.add_edge(Edge(call.caller, target, EdgeKind.CALLS, Provenance(call.rel, call.line)))
        self._calls.clear()
        self._imports.clear()
        self._impls.clear()
        self._refs.clear()
        return batch


__all__ = ["RustExtractor"]
