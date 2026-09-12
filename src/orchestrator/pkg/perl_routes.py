"""Perl route extraction — Mojolicious (full app) and Mojolicious::Lite/Dancer2 (P3, §3.3).

Precision-first, same discipline as every other framework-edge pass in this codebase:

- A verb-less or `any` registration emits **nothing** — no `Endpoint` at all (D2,
  endpoints-typescript-go.md: an "any verb" node would falsely join every consumer in a
  cross-repo joiner, which requires exact verb equality).
- A computed path (a variable, an interpolated string with a `$var` in it) emits nothing.
- A closure handler (`->to(sub {...})`, `get '/x' => sub {...}`) yields an `Endpoint` with
  **no** `EXPOSES` edge — there is no named symbol to point at, and inventing one is exactly
  the fabrication this graph refuses.
- An unbound or unresolvable router variable (`$thing->get(...)` where `$thing` was never
  seen as `$self->routes` or a `->under(...)` result) is left alone — never guessed at.

No `finalize()` needed: a target controller sub is emitted as an `external` placeholder node
(the same eager-placeholder-plus-`FactBatch`-dedup pattern D2's `IMPORTS`/`IMPLEMENTS`
placeholders already use elsewhere in this front-end), so route scanning runs per-file,
inside `extract()`, right alongside the P1 declaration walk.

Two independent readers:

- `scan_mojo_full_app` — walks one `sub` body (typically `sub startup`) for
  `$r->VERB('/path')->to(...)` chains, and `my $api = $r->under('/api');`-style route-group
  variables (tracked as a `{varname: prefix}` dict in source order — the same shape
  `go_routes.py` uses for Gin's `v1 := r.Group("/v1")`, not Laravel's closure recursion,
  because Mojolicious's `under()` binds a *variable*, not a closure).
- `scan_lite_route` — one `ambiguous_function_call_expression` at statement scope:
  Mojolicious::Lite's and Dancer2's shared bareword DSL, `get '/x' => sub {...}` /
  `get '/y' => \\&handler`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

# Dancer2 spells DELETE as `del` (a bareword, since `delete` is a Perl builtin); Mojolicious
# spells it `delete`. Both accepted; `any`/`route`/`match`/anything else is never a route verb
# here — D2 excludes verb-less and "matches everything" registrations outright.
HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "del", "options", "head"})

# `del`'s own uppercase is "DEL", not the real HTTP verb "DELETE" — normalized only here, at
# the one place an `Endpoint`'s name/id is built, so a cross-language joiner matching against
# a client's literal `DELETE` request can still find this route (found in review: `del`
# routes emitted "DEL" and were unjoinable).
_VERB_DISPLAY = {"del": "DELETE"}


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _first_named_of_type(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.named_children:
        if child.type == type_name:
            return child
    return None


def _plain_literal(node: TSNode | None, source: bytes) -> str | None:
    """A literal string with no interpolation — standalone copy of the same check
    ``perl_extractor._plain_string_literal_text`` makes, kept local so this module has no
    import-cycle risk (matches ``php_routes.py``'s reason for a local, not module-level,
    import at its call site).
    """
    if node is None or node.type not in ("string_literal", "interpolated_string_literal"):
        return None
    if len(node.named_children) != 1 or node.named_children[0].type != "string_content":
        return None
    content = node.named_children[0]
    if content.named_children:
        return None
    return _text(content, source)


def _camelize(name: str) -> str:
    """Mojolicious's controller-name convention: ``-`` becomes a namespace separator
    (``::`` in the real class name), each segment's first letter capitalized (``orders``
    -> ``Orders``, ``foo-bar`` -> ``Foo.Bar``). Joined with ``.``, not ``::`` — every
    caller splices this straight into an already-dotted id (D3), and a literal ``::``
    embedded in it (found in review) never matches the real declaration's own fully-dotted
    id (`perl_extractor.py`'s ``_to_dotted`` converts every ``::``), leaving a permanently
    dangling external placeholder beside the real node instead of resolving to it."""
    return ".".join(seg[:1].upper() + seg[1:] for seg in name.split("-") if seg)


def _join(prefix: str, path: str) -> str:
    p = prefix.rstrip("/")
    q = path if path.startswith("/") else "/" + path
    return (p + q) if q != "/" else (p or "/")


def _emit_endpoint(verb: str, path: str, rel: str, line: int, batch: FactBatch) -> str:
    display_verb = _VERB_DISPLAY.get(verb, verb.upper())
    name = f"{display_verb} {path}"
    eid = f"perl:endpoint:{name}"
    batch.add_node(Node(eid, NodeKind.ENDPOINT, name, "perl", Provenance(rel, line)))
    return eid


def _emit_exposes(
    endpoint_id: str, target_id: str, target_name: str, rel: str, line: int, batch: FactBatch
) -> None:
    batch.add_node(Node(target_id, NodeKind.FUNCTION, target_name, "perl", external=True))
    batch.add_edge(Edge(endpoint_id, target_id, EdgeKind.EXPOSES, Provenance(rel, line)))


def _resolve_to_handler(
    to_args: TSNode | None,
    app_package: str | None,
    sub_name: str | None,
    source: bytes,
    rel: str,
    line: int,
    endpoint_id: str,
    batch: FactBatch,
) -> None:
    """``->to(...)``'s argument: a ``'controller#action'`` string, a
    ``controller => .., action => ..`` hash, or a closure (Endpoint only — the closure rule).

    The controller *namespace* Mojolicious actually resolves against is the app class, not
    whichever package happens to call ``->to()`` — a route registered from a helper/plugin
    method (``MyApp::Routes::install``, say) is real, common Mojolicious, and its own
    package name is not the app's namespace. Found wrong in review: this used to resolve
    against ``app_package`` unconditionally. Two ways it's still resolved, both verified
    rather than guessed: an explicit ``namespace => 'X'`` in the hash form (the developer's
    own literal override, honoured regardless of which sub calls ``->to()``), or the
    default resolving against ``app_package`` only when ``sub_name`` is literally
    ``startup`` — Mojolicious's own required, unambiguous entry-point name for the app
    class itself. Anything else: `Endpoint` only, no `EXPOSES` — the same treatment an
    unresolved closure already gets.
    """
    if to_args is None or app_package is None:
        return
    if to_args.type in ("string_literal", "interpolated_string_literal"):
        if sub_name != "startup":
            return
        literal = _plain_literal(to_args, source)
        if literal is None or "#" not in literal:
            return
        controller, _, action = literal.partition("#")
        target = f"perl:{app_package}.Controller.{_camelize(controller)}.{action}"
        _emit_exposes(endpoint_id, target, action, rel, line, batch)
        return
    if to_args.type == "list_expression":
        pairs: dict[str, str] = {}
        children = to_args.named_children
        i = 0
        while i + 1 < len(children):
            key, val = children[i], children[i + 1]
            if key.type == "autoquoted_bareword":
                text = _plain_literal(val, source)
                if text is not None:
                    pairs[_text(key, source)] = text
            i += 2
        hash_controller, hash_action = pairs.get("controller"), pairs.get("action")
        namespace = pairs.get("namespace")
        if not hash_controller or not hash_action:
            return
        if namespace is not None:
            dotted_ns = namespace.replace("::", ".")
            target = f"perl:{dotted_ns}.Controller.{_camelize(hash_controller)}.{hash_action}"
            _emit_exposes(endpoint_id, target, hash_action, rel, line, batch)
        elif sub_name == "startup":
            target = f"perl:{app_package}.Controller.{_camelize(hash_controller)}.{hash_action}"
            _emit_exposes(endpoint_id, target, hash_action, rel, line, batch)
    # A closure (`sub {...}`) or anything else unresolved: Endpoint only, no EXPOSES.


def _receiver_prefix(receiver: TSNode, router_prefix: dict[str, str], source: bytes) -> str | None:
    if receiver.type != "scalar":
        return None
    name = _first_named_of_type(receiver, "varname")
    if name is None:
        return None
    vn = _text(name, source)
    # `$r` is the conventional name for `$self->routes`'s result — treated as the app's own
    # root router (prefix "") even without tracing the assignment, the same bounded
    # convention-based read `php_routes.py` makes for `Route::` as the router root.
    return router_prefix.get(vn, "" if vn == "r" else None)


def _maybe_under_binding(assign: TSNode, router_prefix: dict[str, str], source: bytes) -> None:
    """``my $api = $r->under('/api');`` — a route-group variable, Go's ``v1 := r.Group(...)``
    shape: keyed by variable name, in source order, never a closure to recurse into."""
    children = assign.named_children
    if len(children) < 2:
        return
    lhs, rhs = children[0], children[1]
    if lhs.type != "variable_declaration" or rhs.type != "method_call_expression":
        return
    scalar = _first_named_of_type(lhs, "scalar")
    varname_node = _first_named_of_type(scalar, "varname") if scalar is not None else None
    if varname_node is None:
        return
    var = _text(varname_node, source)
    method = _first_named_of_type(rhs, "method")
    if method is None or _text(method, source) != "under":
        return
    rhs_children = rhs.named_children
    if not rhs_children:
        return
    base = _receiver_prefix(rhs_children[0], router_prefix, source)
    if base is None:
        return
    args = rhs_children[-1] if len(rhs_children) > 1 else None
    literal = _plain_literal(args, source) if args is not None else None
    if literal is None:
        return  # a computed/unreadable group prefix — the variable is never registered,
        # so every route registered against it later fails `_receiver_prefix` too (dropped,
        # never guessed) — the same "no router, rather than a wrong path" rule go_routes.py
        # applies to an unresolved `r.Group(...)` prefix.
    router_prefix[var] = _join(base, literal)


def _maybe_route_chain(
    outer: TSNode,
    router_prefix: dict[str, str],
    app_package: str | None,
    sub_name: str | None,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """``$r->get('/orders')->to(...)`` — the outer call is ``->to(...)``, its receiver is the
    inner ``->VERB(path)`` call."""
    method = _first_named_of_type(outer, "method")
    if method is None or _text(method, source) != "to":
        return
    children = outer.named_children
    if not children:
        return
    inner = children[0]
    if inner.type != "method_call_expression":
        return
    verb_node = _first_named_of_type(inner, "method")
    if verb_node is None:
        return
    verb = _text(verb_node, source)
    if verb not in HTTP_VERBS:
        return  # `any`, `websocket`, `route`, or anything else — D2, never a guess
    inner_children = inner.named_children
    if not inner_children:
        return
    prefix = _receiver_prefix(inner_children[0], router_prefix, source)
    if prefix is None:
        return  # an unbound/unknown router variable — never guessed
    path_arg = inner_children[-1] if len(inner_children) > 1 else None
    path_literal = _plain_literal(path_arg, source) if path_arg is not None else None
    if path_literal is None:
        return  # a computed path
    full_path = _join(prefix, path_literal)
    line = outer.start_point[0] + 1
    endpoint_id = _emit_endpoint(verb, full_path, rel, line, batch)
    to_args = children[-1] if len(children) > 1 else None
    _resolve_to_handler(to_args, app_package, sub_name, source, rel, line, endpoint_id, batch)


def scan_mojo_full_app(
    body: TSNode,
    app_package: str | None,
    sub_name: str | None,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """Walk one sub body for Mojolicious full-app routes — any sub, not only ``startup``:
    a route plugin/helper method (``MyApp::Routes::install``) is a real, common pattern,
    and every ``Endpoint`` it registers is just as real. ``sub_name`` only gates the
    *controller-target* ``EXPOSES`` resolution inside ``_resolve_to_handler``, not whether
    a route is found at all — see that function's own docstring.

    Pre-order, source-order recursion — **not** a LIFO stack: ``my $api = $r->under('/api')``
    must be seen before ``$api->get(...)`` uses it, and a plain stack-pop visits a body's
    statements in reverse, silently dropping every route registered against a group that
    hadn't "happened" yet in the traversal.
    """
    router_prefix: dict[str, str] = {}

    def visit(n: TSNode) -> None:
        if n.type == "assignment_expression":
            _maybe_under_binding(n, router_prefix, source)
        elif n.type == "method_call_expression":
            _maybe_route_chain(n, router_prefix, app_package, sub_name, source, rel, batch)
        for child in n.named_children:
            visit(child)

    for stmt in body.named_children:
        visit(stmt)


def scan_lite_route(expr: TSNode, owner_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
    """``get '/x' => sub {...}`` / ``get '/y' => \\&handler`` — Mojolicious::Lite and Dancer2
    share this bareword-DSL shape. ``owner_id`` is the id a same-scope sub would be filed
    under (the enclosing package, or the module for an implicit-main script) — matching D2's
    id scheme exactly, so a `\\&handler` reference needs no separate lookup: the placeholder
    id it constructs *is* the id the real declaration would have, and `FactBatch` dedup does
    the rest.
    """
    fn_node = _first_named_of_type(expr, "function")
    if fn_node is None:
        return
    verb = _text(fn_node, source)
    if verb not in HTTP_VERBS:
        return
    args = expr.named_children[1] if len(expr.named_children) > 1 else None
    if args is None or args.type != "list_expression" or len(args.named_children) < 2:
        return
    path_literal = _plain_literal(args.named_children[0], source)
    if path_literal is None:
        return
    handler = args.named_children[1]
    line = expr.start_point[0] + 1
    endpoint_id = _emit_endpoint(verb, path_literal, rel, line, batch)
    if handler.type == "refgen_expression":
        fn = _first_named_of_type(handler, "function")
        name_node = _first_named_of_type(fn, "varname") if fn is not None else None
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            target = f"{owner_id}.{name}"
            _emit_exposes(endpoint_id, target, name, rel, line, batch)
    # A closure (or anything else): Endpoint only, no EXPOSES — the closure rule.


__all__ = ["scan_mojo_full_app", "scan_lite_route"]
