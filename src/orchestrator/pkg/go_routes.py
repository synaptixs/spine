"""Gin routes in Go source → ``Endpoint`` nodes + ``EXPOSES`` edges.

The Go half of what :mod:`orchestrator.pkg.python_routes` and
:mod:`orchestrator.pkg.typescript_routes` do, closing the same wrong answer: nothing in Go
*calls* a Gin handler — the framework does, at runtime — so a route handler has zero callers
and ``impact_of`` reports *"safe to refactor"* for a public endpoint. It also lets a Go service
be a **provider** in a cross-repo join, which it could not be before.

============================  ==================================================
``gin.Default()`` / ``New()``  a variable that is a router, prefix ``""``
``r.Group("/v1")``             a router whose prefix is its parent's plus ``/v1``
``r.GET("/orders", h)``        verb from the method, path from a **string literal**
============================  ==================================================

**Routes live inside function bodies**, not at the top level as they do in TypeScript — Gin is
wired in ``func main()``. So this walks the whole tree in source order, which is also what makes
``Group`` resolvable: a group is always declared before it is used.

**Precision: resolve what is literal, skip what is computed.** A path built by ``fmt.Sprintf``
or held in a variable yields no endpoint. **An inline ``func(c *gin.Context)`` literal yields an
endpoint but no ``EXPOSES``** — there is no named symbol to point at, and inventing one is the
fabrication this graph refuses.

``net/http``'s ``HandleFunc`` is deliberately **not** read: it registers a path with no verb,
the joiner matches on verb equality, and an ``ANY`` endpoint would join to everything. See
``docs/specs/endpoints-typescript-go.md`` D2.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: Gin router methods naming an HTTP verb. ``Any`` is absent for the reason D2 gives.
_VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

#: Calls returning a fresh engine: ``gin.Default()``, ``gin.New()``.
_ENGINES = frozenset({"gin.Default", "gin.New"})


@dataclass(frozen=True)
class PendingRoute:
    """One ``r.GET("/x", h)``, with its router's prefix already applied."""

    verb: str
    path: str
    handler: str | None
    provenance: Provenance


@dataclass
class RouteState:
    """Routers seen so far, keyed by variable name, each with its composed prefix."""

    prefixes: dict[str, str] = field(default_factory=dict)
    routes: list[PendingRoute] = field(default_factory=list)


def _text(node: TSNode, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _string_literal(node: TSNode | None, source: bytes) -> str | None:
    """An interpreted string literal's value, or ``None`` for anything computed."""
    if node is None or node.type != "interpreted_string_literal":
        return None
    raw = _text(node, source)
    return raw[1:-1] if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"' else None


def _join(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


def _walk(node: TSNode) -> Iterator[TSNode]:
    """Pre-order, which is source order — what makes a ``Group`` resolvable before its use."""
    yield node
    for child in node.named_children:
        yield from _walk(child)


def scan_tree(root: TSNode, source: bytes, rel: str) -> RouteState:
    """Collect routers, groups and routes from one file, in source order."""
    state = RouteState()
    for node in _walk(root):
        if node.type == "short_var_declaration":
            _scan_binding(node, source, state)
        elif node.type == "call_expression":
            _scan_registration(node, source, rel, state)
    return state


def _scan_binding(node: TSNode, source: bytes, state: RouteState) -> None:
    """``r := gin.Default()`` and ``v1 := r.Group("/v1")`` both bind a router."""
    left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
    if left is None or right is None or not left.named_children or not right.named_children:
        return
    name_node, value = left.named_children[0], right.named_children[0]
    if value.type != "call_expression":
        return
    fn = value.child_by_field_name("function")
    if fn is None:
        return
    name, called = _text(name_node, source), _text(fn, source)
    if called in _ENGINES:
        state.prefixes[name] = ""
        return
    if fn.type == "selector_expression":
        obj, prop = fn.child_by_field_name("operand"), fn.child_by_field_name("field")
        if obj is None or prop is None or _text(prop, source) != "Group":
            return
        parent = _text(obj, source)
        if parent not in state.prefixes:
            return
        args = value.child_by_field_name("arguments")
        parts = list(args.named_children) if args is not None else []
        prefix = _string_literal(parts[0], source) if parts else None
        if prefix is None:
            return  # a computed group prefix: no router, rather than a wrong path
        state.prefixes[name] = _join(state.prefixes[parent], prefix)


def _scan_registration(node: TSNode, source: bytes, rel: str, state: RouteState) -> None:
    """``r.GET("/orders", listOrders)`` → a route at the router's composed prefix."""
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "selector_expression":
        return
    obj, prop = fn.child_by_field_name("operand"), fn.child_by_field_name("field")
    if obj is None or prop is None:
        return
    receiver, verb = _text(obj, source), _text(prop, source)
    if verb not in _VERBS or receiver not in state.prefixes:
        return
    args = node.child_by_field_name("arguments")
    parts = list(args.named_children) if args is not None else []
    if not parts:
        return
    path = _string_literal(parts[0], source)
    if path is None:
        return
    handler = None
    if len(parts) > 1 and parts[-1].type == "identifier":
        handler = _text(parts[-1], source)
    full = _join(state.prefixes[receiver], path)
    state.routes.append(PendingRoute(verb, full, handler, Provenance(rel, node.start_point[0] + 1)))


def emit(state: RouteState, module_id: str, local_funcs: set[str], batch: FactBatch) -> None:
    """Add each route's ``Endpoint``, and its ``EXPOSES`` when the handler is a known function."""
    for route in state.routes:
        endpoint_id = f"go:endpoint:{route.verb} {route.path}"
        batch.add_node(
            Node(
                endpoint_id,
                NodeKind.ENDPOINT,
                f"{route.verb} {route.path}",
                "go",
                route.provenance,
            )
        )
        if route.handler is not None and route.handler in local_funcs:
            batch.add_edge(
                Edge(
                    endpoint_id,
                    f"{module_id}.{route.handler}",
                    EdgeKind.EXPOSES,
                    route.provenance,
                )
            )


__all__ = ["PendingRoute", "RouteState", "emit", "scan_tree"]
