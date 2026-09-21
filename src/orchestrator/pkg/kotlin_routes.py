"""Kotlin server routes: the Ktor DSL, and Spring read through ``jvm_routes``.

P6 of docs/specs/kotlin-support-roadmap.md, D16.

Until now every Kotlin route this front-end could see was one the app *called*
(Retrofit, D10) or navigated to in-process (Compose, D14). This module is where a
Kotlin program becomes a **provider**: a Ktor or Spring service emits ``Endpoint``
nodes in the same ``java:endpoint:<VERB> <path>`` namespace as Java's, so
``pkg joins`` can pair an Android app's Retrofit call with the Kotlin service that
answers it.

Spring is only adapted here — the semantics live in :mod:`orchestrator.pkg.jvm_routes`
so Java gets the same reader. Ktor is Kotlin-only and lives here in full.

**Ktor is a DSL, so the difficulty is knowing when a call is a route at all.**
``get`` is one of the most common method names in Kotlin: the sample corpus has 341
calls spelled ``get("…")`` and only a minority are routes — the rest are
``map.get(k)``, ``client.get(url)``, ``call.sessions.get<S>()``. Two rules keep
them apart, and both are necessary:

* the call must have a **plain identifier callee** — anything with a receiver
  (``board.post(text)``) is a method call, never a route declaration; and
* it must be lexically inside a **route context** — a ``routing { … }`` block, or
  the body of a ``fun Route.x()`` extension, which is how Ktor code splits routes
  across files.

**Prefixes compose, and an extension's prefix is not lexical.** ``route("/data") {
route("/users") { get("/{id}") } }`` is ``GET /data/users/{id}`` — that much is in
one tree. But ``fun Route.videos()`` declares routes at whatever path its *caller*
mounts it on, which is usually another file, so its endpoints cannot be emitted
while reading it. Routes are therefore recorded against their owner and the mount
points are resolved in ``finalize``, to a fixpoint, because a route module can
mount another route module. A module nothing in the tree mounts yields **no**
endpoints: its path is genuinely unknown, and rooting it at ``/`` would be a guess
that happens to be right in the common case, which is the worst kind.

**What it refuses.** A ``route(Paths.API)`` group whose path is not a literal
silences everything inside it (D16, the PHP lesson: a wrong prefix is worse than a
missing route). A verb call with a non-literal path yields nothing. Typed resource
routes (``get<Index> { … }``, Ktor Resources) yield nothing — the path lives in a
``@Resource`` annotation on another class and is not read here. ``webSocket``,
``sse`` and ``staticFiles`` are not HTTP verbs and yield nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.jvm_routes import (
    MAPPING_ANNOTATIONS,
    METHOD_ARGUMENT,
    PATH_ARGUMENTS,
    RouteAnnotation,
    class_prefix,
    emit_endpoints,
    is_controller,
    join_path,
    literal_path,
    resolves_into_spring,
)
from orchestrator.pkg.kotlin_names import (
    annotations_of,
    collection_items,
    field_text,
    string_value,
    text,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "kotlin"

#: The Ktor builders that open a route context and a route group.
_ROUTING = "routing"
_ROUTE = "route"

#: Receiver types whose extension functions are route modules.
ROUTE_RECEIVERS = frozenset({"Route", "Routing"})

#: The DSL functions that declare an endpoint, and the verb each stands for.
_VERBS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
}

#: Iterations allowed when resolving mount points. A route module mounting another
#: is ordinary; ten levels is far past anything real and bounds a mutual-mount cycle.
_MAX_MOUNT_DEPTH = 10

#: The owner key of a route declared directly inside a ``routing { … }`` block.
_ROOT = ""


@dataclass(frozen=True)
class _Route:
    """One declared route, held against the owner whose prefix it is relative to."""

    owner: str
    verb: str
    path: str
    handler: str  # the ``::name`` a function-reference handler gives, else ""
    rel: str
    line: int


@dataclass(frozen=True)
class _Mount:
    """A call that mounts a route module at ``prefix`` inside ``host``.

    ``package`` and ``imports`` are the mounting *file*'s, and they are what makes the
    name mean something. Found in review: a mount used to resolve by bare name across
    the whole repository, so in a monorepo a ``health()`` call in one service mounted
    another service's ``fun Route.health()`` under the caller's prefix — a route the
    second service does not serve, attributed to a function in a file that never
    mentions it. Two services declaring a route module of the same name is the normal
    shape of a monorepo, not a corner case.
    """

    host: str
    prefix: str
    name: str
    package: str
    imports: Mapping[str, str]
    #: this file's ``import a.b.*`` prefixes — #395: the repo-wide unique-name
    #: fallback in ``_mounted_module`` is restricted to names reachable through one
    #: of these, the same rule ``_type_candidates`` already applies in the extractor.
    wildcard_prefixes: frozenset[str]
    #: whether the mounting file declares no ``package``; ``package`` is then a path
    #: and names no package at all, so the own-package tier keys on this instead.
    default_package: bool = False


@dataclass
class KtorState:
    """Routes and mount points collected across the whole walk.

    Everything waits for ``finalize`` for the reason the module docstring gives: a
    route module's path is set by its caller, which is nearly always another file.
    """

    #: simple name → the ids of every ``fun Route.<name>`` declaring it
    modules_by_name: dict[str, list[str]] = field(default_factory=dict)
    #: the ids among those whose file declares no ``package`` — #395: their module is
    #: the repo-relative path, so "same package" cannot be a string comparison there
    default_package_modules: set[str] = field(default_factory=set)
    routes: list[_Route] = field(default_factory=list)
    mounts: list[_Mount] = field(default_factory=list)

    def clear(self) -> None:
        self.modules_by_name.clear()
        self.default_package_modules.clear()
        self.routes.clear()
        self.mounts.clear()


def register_module(
    name: str, func_id: str, receiver: str, state: KtorState, *, default_package: bool = False
) -> bool:
    """Record ``fun Route.<name>()`` as a route module. Returns whether it is one."""
    if receiver.rsplit(".", 1)[-1] not in ROUTE_RECEIVERS:
        return False
    ids = state.modules_by_name.setdefault(name, [])
    if func_id not in ids:
        ids.append(func_id)
    if default_package:
        state.default_package_modules.add(func_id)
    return True


def scan_calls(
    body: TSNode,
    source: bytes,
    rel: str,
    state: KtorState,
    *,
    owner: str | None,
    package: str = "",
    default_package: bool = False,
    imports: Mapping[str, str] | None = None,
    wildcard_prefixes: frozenset[str] = frozenset(),
) -> None:
    """Collect the Ktor routes and mounts in one function body.

    ``owner`` is the function's own id when the function is a route module, and
    ``None`` otherwise — in which case the walk is only looking for ``routing { … }``.
    ``package`` and ``imports`` travel with every mount recorded here, because by
    ``emit`` the file they came from is gone (see :class:`_Mount`).
    """
    _scan(
        body, owner, "", state, source, rel, _Site(package, imports or {}, wildcard_prefixes, default_package)
    )


def emit(state: KtorState, batch: FactBatch, resolve: Any) -> int:
    """Turn collected routes into ``Endpoint`` + ``EXPOSES``. Returns the count."""
    prefixes = _mount_points(state)
    emitted = 0
    for route in state.routes:
        for base in prefixes.get(route.owner, ()):
            path = join_path(base, route.path)
            provenance = Provenance(route.rel, route.line)
            endpoint_id = f"java:endpoint:{route.verb} {path}"
            batch.add_node(Node(endpoint_id, NodeKind.ENDPOINT, f"{route.verb} {path}", _LANG, provenance))
            emitted += 1
            # The closure rule (D16): a handler written inline is an anonymous lambda
            # with no id to point at, so it gets an `Endpoint` and no `EXPOSES`. Only
            # `get("/x", ::handler)` names a function the graph already holds.
            target = resolve(route.handler) if route.handler else None
            if target:
                batch.add_edge(Edge(endpoint_id, target, EdgeKind.EXPOSES, provenance))
    return emitted


# ---- the walk ---------------------------------------------------------------


@dataclass(frozen=True)
class _Site:
    """The package and import map of the file a mount was written in."""

    package: str
    imports: Mapping[str, str]
    wildcard_prefixes: frozenset[str] = frozenset()
    default_package: bool = False


def _scan(
    node: TSNode, owner: str | None, prefix: str, state: KtorState, source: bytes, rel: str, site: _Site
) -> None:
    for child in node.named_children:
        if child.type == "call_expression" and not _is_inner_callee(child):
            _call(child, owner, prefix, state, source, rel, site)
        else:
            _scan(child, owner, prefix, state, source, rel, site)


def _call(
    call: TSNode, owner: str | None, prefix: str, state: KtorState, source: bytes, rel: str, site: _Site
) -> None:
    """Read one call, and descend into its lambda with whatever prefix now holds."""
    inner = _arguments_holder(call)
    name = _plain_callee(inner, source)
    lam = _lambda_of(call)

    if name == _ROUTING and lam is not None:
        _scan(lam, _ROOT, "", state, source, rel, site)  # a route context opens here
        return
    if owner is None:
        _scan(call, None, prefix, state, source, rel, site)  # still looking for `routing`
        return
    if name == _ROUTE:
        group = _literal_argument(inner, source)
        if lam is not None and group is not None:
            _scan(lam, owner, join_path(prefix, group), state, source, rel, site)
        # else: a computed group path silences every route inside it (D16)
        return
    verb = _VERBS.get(name)
    if verb is not None and _declare(call, inner, lam, verb, owner, prefix, state, source, rel):
        return
    if name and lam is None:
        # A bare call in route context with no lambda is Ktor's idiom for mounting a
        # route module (`videos(database)`). Recorded as a candidate only: it resolves
        # in `emit` if some `fun Route.<name>` declares it, and is dropped otherwise.
        state.mounts.append(
            _Mount(
                owner, prefix, name, site.package, site.imports, site.wildcard_prefixes, site.default_package
            )
        )
        return
    # Anything else: a wrapper that nests routes without changing the path
    # (`authenticate("x") { … }`, `install(…) { … }`), or a chained call whose
    # *receiver* is the route — `get("/get") { … }.describe { … }` is one call on
    # another, and reading only the outer half loses the route entirely. Descending
    # through the whole node rather than just the lambda is what catches both.
    _scan(call, owner, prefix, state, source, rel, site)


def _declare(
    call: TSNode,
    inner: TSNode,
    lam: TSNode | None,
    verb: str,
    owner: str,
    prefix: str,
    state: KtorState,
    source: bytes,
    rel: str,
) -> bool:
    """Record one verb call as a route. ``False`` when it is not one after all.

    A verb name is not proof: ``fun Route.delete(dao, hashFunction)`` is a *route
    module* that happens to be named after a verb, and Ktor code really does write
    them (the sample corpus has one). Returning ``False`` lets the caller fall back
    to reading it as a mount instead of silently dropping the module.
    """
    args = _positional_arguments(inner)
    line = call.start_point[0] + 1
    if lam is not None:
        # `get("/x") { … }`, or a bare `get { … }` that serves the group's own path.
        if not args:
            path = prefix or "/"
        else:
            literal = string_value(args[0], source)
            if literal is None:
                return True  # a computed path — a route, but not one we can name
            path = join_path(prefix, literal)
        state.routes.append(_Route(owner, verb, path, "", rel, line))
        return True
    # `get("/x", ::handler)` — no lambda, so the handler is a function with a name.
    if len(args) < 2:
        return False
    literal = string_value(args[0], source)
    handler = _callable_reference(args[1], source)
    if literal is None or not handler:
        return False
    state.routes.append(_Route(owner, verb, join_path(prefix, literal), handler, rel, line))
    return True


def _mount_points(state: KtorState) -> dict[str, list[str]]:
    """Owner key → every absolute prefix it is mounted at.

    The root context is mounted at the root by definition. A route module's
    prefixes come from its mount sites, which may themselves be inside a module
    that is not yet resolved — hence the fixpoint rather than a single pass. A
    module mounted twice genuinely serves two paths, and gets both.
    """
    resolved: dict[str, list[str]] = {_ROOT: [""]}
    for _ in range(_MAX_MOUNT_DEPTH):
        changed = False
        for mount in state.mounts:
            target = _mounted_module(mount, state)
            bases = resolved.get(mount.host)
            if target is None or bases is None:
                continue
            here = resolved.setdefault(target, [])
            for base in list(bases):
                path = join_path(base, mount.prefix)
                if path not in here:
                    here.append(path)
                    changed = True
        if not changed:
            break
    return resolved


def _mounted_module(mount: _Mount, state: KtorState) -> str | None:
    """Which ``fun Route.<name>`` this mount call names, or ``None``.

    Resolved from the *calling* file's point of view, in the order Kotlin itself
    resolves a name: a declaration in the same package, then one an explicit import
    names, then — only when exactly one exists — a declaration reachable through one
    of this file's ``import a.b.*`` prefixes.

    #395: the third tier used to fire on a declaration *anywhere in the repository*,
    with no restriction to what this file could actually see — a mount would then
    cross package and service boundaries with no import at all, mounting a route
    module Kotlin itself could not have compiled the call against. Restricting it to
    a recorded wildcard prefix, the same rule ``_type_candidates`` applies in the
    extractor, closes that while keeping the genuine wildcard-import case working.

    A name several candidates answer to is not resolved at all, the same rule the
    Compose screen resolver uses.
    """
    ids = state.modules_by_name.get(mount.name, [])
    if not ids:
        return None
    if mount.default_package:
        # Both files are in the *default* package, which is one package however many
        # files it spans — but their modules are repo-relative paths, so the string
        # comparison below can never see it. Same "exactly one, or unresolved" rule.
        here = [i for i in ids if i in state.default_package_modules]
        return here[0] if len(here) == 1 else None
    if mount.package:
        own = f"java:{mount.package}.{mount.name}"
        if own in ids:
            return own
    imported = mount.imports.get(mount.name)
    if imported is not None:
        candidate = f"java:{imported}"
        if candidate in ids:
            return candidate
    candidates = {f"java:{prefix}.{mount.name}" for prefix in mount.wildcard_prefixes}
    reachable = [i for i in ids if i in candidates]
    return reachable[0] if len(reachable) == 1 else None


# ---- Spring, adapted to the Kotlin grammar ----------------------------------


def read_controller(
    node: TSNode,
    type_id: str,
    is_spring: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> bool:
    """Emit Spring ``Endpoint`` + ``EXPOSES`` for a ``@Controller`` class's methods.

    Returns whether the class was a controller at all, so a caller can tell "not a
    controller" from "a controller with no mappings".
    """
    annotations = _spring_annotations(node, is_spring, source)
    if not is_controller(annotations):
        return False
    prefix = class_prefix(annotations)
    if prefix is None:
        return True  # a computed class prefix silences every method (D16)
    for body in (c for c in node.named_children if c.type in ("class_body", "enum_class_body")):
        for member in body.named_children:
            if member.type != "function_declaration":
                continue
            name = field_text(member, "name", source)
            if not name:
                continue
            emit_endpoints(
                _spring_annotations(member, is_spring, source),
                method_id=f"{type_id}.{name}",
                prefix=prefix,
                language=_LANG,
                rel=rel,
                batch=batch,
            )
    return True


def spring_resolver(by_simple: dict[str, str], wildcard_prefixes: set[str]) -> Any:
    """A ``simple annotation name → is it Spring's?`` predicate over one file."""

    def is_spring(name: str) -> bool:
        return resolves_into_spring(name, by_simple=by_simple, wildcard_prefixes=wildcard_prefixes)

    return is_spring


def _spring_annotations(node: TSNode, is_spring: Any, source: bytes) -> list[RouteAnnotation]:
    """Read this declaration's Spring annotations into the shared neutral form."""
    out: list[RouteAnnotation] = []
    for annotation in annotations_of(node, source):
        if not is_spring(annotation.name):
            continue
        if annotation.name in MAPPING_ANNOTATIONS:
            out.append(
                RouteAnnotation(
                    name=annotation.name,
                    path=_mapping_path(annotation, source),
                    methods=_mapping_methods(annotation, source),
                    line=annotation.line,
                )
            )
        else:
            out.append(RouteAnnotation(name=annotation.name, line=annotation.line))
    return out


def _mapping_path(annotation: Any, source: bytes) -> str | None:
    """``""`` when the annotation names no path, ``None`` when it is not a literal."""
    node = None
    for name in PATH_ARGUMENTS:
        node = annotation.arg(name)
        if node is not None:
            break
    if node is None:
        return ""
    # `@GetMapping(["/a", "/b"])` — Spring allows several; the first is enough to
    # ground one endpoint and the rest would each need their own, so take them all.
    items = collection_items(node)
    # `literal_path` rather than the raw literal: Kotlin refuses an interpolated path
    # at the grammar, but `"\\${api.base}/x"` is an *escaped* dollar and decodes to the
    # same Spring placeholder Java's reader had to be taught to refuse.
    return literal_path(string_value(items[0] if items else node, source))


def _mapping_methods(annotation: Any, source: bytes) -> tuple[str, ...]:
    """``method = [RequestMethod.GET]`` → ``("GET",)``; ``()`` when absent."""
    # Looked up by name *exactly*, not through `Annotation.arg`: that helper falls
    # back to the first positional argument, which for `@RequestMapping("/any")`
    # would read the path as a verb and mint an endpoint named after it.
    node = next((value for name, value in annotation.args if name == METHOD_ARGUMENT), None)
    if node is None:
        return ()
    items = collection_items(node) or [node]
    return tuple(name for item in items if (name := text(item, source).rsplit(".", 1)[-1]))


# ---- grammar helpers --------------------------------------------------------


def _is_inner_callee(call: TSNode) -> bool:
    """Whether this call is only the callee half of an enclosing trailing-lambda call."""
    parent = call.parent
    return (
        parent is not None
        and parent.type == "call_expression"
        and next(iter(parent.named_children), None) is call
    )


def _arguments_holder(call: TSNode) -> TSNode:
    """The node carrying ``value_arguments`` — the inner call when a lambda wraps it."""
    first = next(iter(call.named_children), None)
    return first if first is not None and first.type == "call_expression" else call


def _lambda_of(call: TSNode) -> TSNode | None:
    return next((c for c in call.named_children if c.type == "annotated_lambda"), None)


def _plain_callee(call: TSNode, source: bytes) -> str:
    """The callee name, but **only** when it is a bare identifier.

    A receiver disqualifies the call: ``board.post(text)`` and ``client.get(url)``
    are method calls that happen to share a name with a Ktor verb, and the DSL is
    always written bare. This one restriction is what makes the reader usable at
    all — see the module docstring.
    """
    callee = next(iter(call.named_children), None)
    return text(callee, source) if callee is not None and callee.type == "identifier" else ""


def _positional_arguments(call: TSNode) -> list[TSNode]:
    """The value nodes of the call's positional arguments, in order."""
    args = next((c for c in call.named_children if c.type == "value_arguments"), None)
    if args is None:
        return []
    out: list[TSNode] = []
    for arg in args.named_children:
        if arg.type != "value_argument" or not arg.named_children:
            continue
        children = arg.named_children
        if len(children) >= 2 and children[0].type == "identifier":
            continue  # a named argument — Ktor's route builders take none
        out.append(children[-1])
    return out


def _literal_argument(call: TSNode, source: bytes) -> str | None:
    """The first positional argument as a literal string, else ``None``."""
    args = _positional_arguments(call)
    return string_value(args[0], source) if args else None


def _callable_reference(node: TSNode, source: bytes) -> str:
    """``::handler`` → ``handler``; ``""`` when the node is not a function reference."""
    if node.type != "callable_reference":
        return ""
    parts = [c for c in node.named_children if c.type == "identifier"]
    return text(parts[-1], source) if parts else ""


__all__ = [
    "ROUTE_RECEIVERS",
    "KtorState",
    "emit",
    "read_controller",
    "register_module",
    "scan_calls",
    "spring_resolver",
]
