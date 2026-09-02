"""Express routes in TypeScript source → ``Endpoint`` nodes + ``EXPOSES`` edges.

**The gap this closes is a wrong answer, not a missing one**, and it is the same one
:mod:`orchestrator.pkg.python_routes` closes for Python: nothing in TypeScript *calls* an
Express handler — the framework does, at runtime, through the router. So a route handler has
zero callers, and ``impact_of`` answers *"safe to refactor"* for a public endpoint whose blast
radius is every client of ``GET /v1/orders``.

It also unblocks something larger. The multi-repo ``http`` joiner matches a consumer's
unresolved calls against the **provider's** ``Endpoint`` nodes, so before this a Node service
could not be a *provider* in a cross-repo join at all — multi-repo comprehension worked only
when the provider was Java, C# or Python. See ``docs/specs/endpoints-typescript-go.md``.

Read from the same tree-sitter CST the front-end already parses — no new dependency:

============================  ==================================================
``express()`` / ``Router()``  a variable that is a router
``app.get("/x", h)``          verb from the method, path from a **string literal**
``app.use("/v1", router)``    a mount, applied to that router's routes
============================  ==================================================

Ids mirror the C# and Python scheme exactly (``ts:endpoint:GET /v1/orders``), so every renderer
that already understands an endpoint composes for free.

**Precision: resolve what is literal, skip what is computed.** A path built from a template
literal or held in a variable yields *no* endpoint, because a wrong path is presented as
grounded by every surface downstream.

**An inline handler yields an endpoint but no ``EXPOSES`` edge.** ``app.get("/x", (req, res) =>
…)`` is the dominant Express shape and there is no named symbol to point at. The route is still
a fact worth recording — it is what the joiner matches — but inventing a handler id to hang an
edge on would be the fabrication this graph refuses. The edge appears when the handler is a
named function this module declares.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: Express router methods that name an HTTP verb. ``all`` is deliberately absent: it registers
#: every verb, and the joiner matches on verb equality, so an ``ALL`` endpoint would either join
#: to nothing or to everything.
_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

#: Calls that produce a router: ``express()``, ``express.Router()``, ``Router()``.
_ROUTER_FACTORIES = frozenset({"express", "express.Router", "Router"})


@dataclass(frozen=True)
class PendingRoute:
    """One ``app.get("/x", h)``, before its mounts are known."""

    router: str
    verb: str
    path: str
    handler_id: str | None
    provenance: Provenance


@dataclass
class RouteState:
    """What one module contributes. Mounts are per-module — see ``emit``."""

    routers: set[str] = field(default_factory=set)
    routes: list[PendingRoute] = field(default_factory=list)
    mounts: dict[str, list[str]] = field(default_factory=dict)


def _text(node: TSNode, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _string_literal(node: TSNode | None, source: bytes) -> str | None:
    """The value of a plain string argument, or ``None`` for anything computed.

    A ``template_string`` is refused even when it has no substitution: the shape is the signal
    a reader relies on, and admitting the empty case invites admitting the interpolated one.
    """
    if node is None or node.type != "string":
        return None
    raw = _text(node, source)
    return raw[1:-1] if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0] else None


def _join(*parts: str) -> str:
    """Compose a path from prefixes, collapsing the slashes their spellings disagree on."""
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


def scan_module(
    decls: Iterable[TSNode | None], source: bytes, rel: str, local_funcs: dict[str, str]
) -> RouteState:
    """Collect routers, routes and mounts from one module's top-level declarations."""
    state = RouteState()
    for node in decls:
        if node is None:
            continue
        _scan_router_bindings(node, source, state)
    for node in decls:
        if node is None:
            continue
        _scan_registrations(node, source, rel, local_funcs, state)
    return state


def _scan_router_bindings(node: TSNode, source: bytes, state: RouteState) -> None:
    """``const app = express()`` / ``const r = express.Router()`` → ``app``, ``r`` are routers."""
    if node.type not in {"lexical_declaration", "variable_declaration"}:
        return
    for child in node.named_children:
        if child.type != "variable_declarator":
            continue
        name = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name is None or value is None or value.type != "call_expression":
            continue
        fn = value.child_by_field_name("function")
        if fn is not None and _text(fn, source) in _ROUTER_FACTORIES:
            state.routers.add(_text(name, source))


def _scan_registrations(
    node: TSNode, source: bytes, rel: str, local_funcs: dict[str, str], state: RouteState
) -> None:
    """``app.get("/x", h)`` → a route; ``app.use("/v1", r)`` → a mount."""
    if node.type != "expression_statement":
        return
    call = node.named_children[0] if node.named_children else None
    if call is None or call.type != "call_expression":
        return
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return
    obj, prop = fn.child_by_field_name("object"), fn.child_by_field_name("property")
    if obj is None or prop is None:
        return
    receiver, method = _text(obj, source), _text(prop, source)
    if receiver not in state.routers:
        return
    args = call.child_by_field_name("arguments")
    parts = [a for a in args.named_children] if args is not None else []
    if not parts:
        return
    path = _string_literal(parts[0], source)
    if path is None:
        return  # computed path: no endpoint, rather than a wrong one
    line = node.start_point[0] + 1

    if method == "use":
        for arg in parts[1:]:
            if arg.type == "identifier":
                state.mounts.setdefault(_text(arg, source), []).append(path)
        return
    if method not in _VERBS:
        return
    handler_id = None
    if len(parts) > 1 and parts[-1].type == "identifier":
        handler_id = local_funcs.get(_text(parts[-1], source))
    state.routes.append(PendingRoute(receiver, method.upper(), path, handler_id, Provenance(rel, line)))


def emit(state: RouteState, batch: FactBatch) -> None:
    """Compose each route's full path and add its ``Endpoint`` — and its ``EXPOSES`` if named.

    A router mounted twice yields two endpoints, which is the truth: the handler serves both
    paths. **A router with no resolvable mount yields one, at its local path** — dropping it
    would restore exactly the false negative this module exists to kill, a public handler with
    no inbound edge that ``impact_of`` then calls safe.
    """
    for route in state.routes:
        for mount in state.mounts.get(route.router) or [""]:
            full = _join(mount, route.path)
            endpoint_id = f"ts:endpoint:{route.verb} {full}"
            batch.add_node(
                Node(
                    endpoint_id,
                    NodeKind.ENDPOINT,
                    f"{route.verb} {full}",
                    "typescript",
                    route.provenance,
                )
            )
            if route.handler_id is not None:
                batch.add_edge(Edge(endpoint_id, route.handler_id, EdgeKind.EXPOSES, route.provenance))


__all__ = ["PendingRoute", "RouteState", "emit", "scan_module"]
