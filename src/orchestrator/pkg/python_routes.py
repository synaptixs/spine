"""HTTP routes in Python source → ``Endpoint`` nodes + ``EXPOSES`` edges.

**The gap this closes is a wrong answer, not a missing one.** Nothing in Python *calls* an
HTTP handler — the framework does, at runtime, through the decorator. So before this, the
graph reported zero callers for a route handler, and ``impact_of`` answered *"safe to
refactor"* for a public endpoint whose blast radius is every client of ``GET /v1/runs``.

Detection is decorator- and call-shaped, which is to say statically readable from the ``ast``
the front-end already parses. No new dependency, no heuristics over identifier spelling:

============  ==================================================================
Framework     Read from
============  ==================================================================
FastAPI /     ``@app.get("/x")``, ``@router.post("/x")``, ``APIRouter(prefix=…)``,
Starlette     ``include_router(r, prefix=…)``
Flask         ``@app.route("/x", methods=[…])``, ``Blueprint(…, url_prefix=…)``
Django        ``urls.py``: ``path("x/", view)``, ``re_path(…)``, ``include(…)``
============  ==================================================================

Ids mirror the C# scheme exactly (``py:endpoint:GET /v1/runs``), so every renderer that
already understands an endpoint composes for free.

**Emission is deferred to ``finalize``.** ``include_router(runs.router, prefix="/v1/runs")``
lives in a different file from the ``@router.get("")`` it re-mounts, so the full path is
whole-repo knowledge that a per-file pass cannot have — the same reason ``link_imports``
exists. Routes are collected per file and emitted once, at the end, when the mounts are
known. ``FactBatch`` is add-only, which makes deferral the only honest option: an endpoint
emitted at the wrong path could not be corrected later, only duplicated.

**Precision: resolve what is literal, skip what is computed, never guess.** A path built from
an f-string or held in a variable yields *no* endpoint, because a wrong path in the graph is
worse than an absent one — every surface downstream presents it as grounded.

The one deliberate exception is an **unresolved prefix**: when a router's mount cannot be
followed, its endpoints are still emitted at their local path. Dropping them would restore
exactly the false negative this module exists to kill — a public handler with no inbound
edge, which ``impact_of`` then calls safe. A locally-pathed endpoint is a partially-known
fact; an absent one is a wrong answer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# Decorator attributes that name an HTTP verb directly: ``@router.get("/x")``.
_VERB_ATTRS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})

# Constructors that carry a path prefix for everything registered on them, and the keyword
# each spells it with. FastAPI's ``APIRouter(prefix=…)``; Flask's ``Blueprint(url_prefix=…)``.
_ROUTER_CTORS = {"APIRouter": "prefix", "Blueprint": "url_prefix", "Router": "prefix"}

# Django URL-conf callables. ``include`` mounts another conf under a prefix.
_DJANGO_ROUTE = frozenset({"path", "re_path", "url"})

# A Django route has no verb at the URL conf — the view decides. Naming it ``ANY`` is a
# statement that the verb is unknown, not an assertion that every verb is served.
_DJANGO_VERB = "ANY"


@dataclass(frozen=True)
class PendingRoute:
    """A route found in one file, still missing whatever prefix a mount will add."""

    verb: str
    path: str
    router_key: str  # "" when registered straight onto an app, so no mount applies
    handler_id: str
    provenance: Provenance


@dataclass
class RouteState:
    """Whole-repo route knowledge, accumulated file by file.

    Lives on the extractor instance for the duration of one repo walk, like the Go
    front-end's interface table.
    """

    routes: list[PendingRoute] = field(default_factory=list)
    # router_key → prefix declared where the router was constructed
    local_prefixes: dict[str, str] = field(default_factory=dict)
    # router_key → the prefixes it is mounted under (a router may be mounted twice)
    mounts: dict[str, list[str]] = field(default_factory=lambda: {})

    def clear(self) -> None:
        self.routes.clear()
        self.local_prefixes.clear()
        self.mounts.clear()


def _literal_path(node: ast.expr | None) -> str | None:
    """A string literal, or a concatenation of them. ``None`` for anything computed.

    This is the precision rule in one function: ``"/v1/" + "runs"`` resolves, ``f"/v1/{x}"``
    and ``PREFIX + "/runs"`` do not.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_path(node.left)
        right = _literal_path(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _join(*parts: str) -> str:
    """Compose path segments the way a router does: one slash between, leading slash kept."""
    joined = "/".join(p.strip("/") for p in parts if p.strip("/"))
    if not joined:
        return "/"
    return f"/{joined}"


def _verbs_from_methods(call: ast.Call) -> list[str]:
    """``methods=["POST", "PUT"]`` → the verbs; a Flask ``route`` with none means GET."""
    methods = _kwarg(call, "methods")
    if methods is None:
        return ["GET"]
    if not isinstance(methods, ast.List | ast.Tuple | ast.Set):
        return []  # computed method list — skip rather than assume GET
    verbs = [
        el.value.upper() for el in methods.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)
    ]
    return verbs


def _receiver(func: ast.expr) -> str | None:
    """The name a decorator hangs off: ``@router.get`` → ``router``. ``None`` if computed."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def _resolve_router(expr: ast.expr, module_id: str, imports: dict[str, str]) -> str | None:
    """The whole-repo key for a router expression, or ``None`` when it can't be followed.

    Handles the two shapes that actually appear: ``runs.router`` (module imported, attribute
    read) and a bare ``router`` (the object imported directly, or defined in this module).
    """
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        base = imports.get(expr.value.id)
        return f"{base}.{expr.attr}" if base else None
    if isinstance(expr, ast.Name):
        imported = imports.get(expr.id)
        return imported or f"{module_id}.{expr.id}"
    return None


def scan_module(
    tree: ast.Module,
    *,
    module_id: str,
    rel: str,
    imports: dict[str, str],
    state: RouteState,
) -> None:
    """Collect this file's routes, router prefixes and mounts into ``state``."""
    _collect_routers(tree, module_id=module_id, imports=imports, state=state)
    _collect_routes(tree.body, parent_id=module_id, module_id=module_id, rel=rel, state=state)
    if rel.endswith("urls.py"):
        _collect_django(tree, module_id=module_id, rel=rel, imports=imports, state=state)


def _collect_routers(tree: ast.Module, *, module_id: str, imports: dict[str, str], state: RouteState) -> None:
    """``router = APIRouter(prefix="/v1")`` and ``app.include_router(r, prefix="/x")``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            ctor = node.value.func
            name = ctor.id if isinstance(ctor, ast.Name) else getattr(ctor, "attr", "")
            keyword = _ROUTER_CTORS.get(name)
            if keyword is None:
                continue
            declared = _literal_path(_kwarg(node.value, keyword)) or ""
            for target in node.targets:
                if isinstance(target, ast.Name):
                    state.local_prefixes[f"{module_id}.{target.id}"] = declared

        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called not in {"include_router", "register_blueprint", "mount"} or not node.args:
                continue
            key = _resolve_router(node.args[0], module_id, imports)
            if key is None:
                continue
            keyword = "url_prefix" if called == "register_blueprint" else "prefix"
            prefix: str | None = _literal_path(_kwarg(node, keyword))
            if prefix is None and called == "mount" and len(node.args) > 1:
                prefix = _literal_path(node.args[0])  # Starlette: mount("/x", app)
            state.mounts.setdefault(key, []).append(prefix or "")


def _collect_routes(
    body: list[ast.stmt], *, parent_id: str, module_id: str, rel: str, state: RouteState
) -> None:
    """Walk defs, mirroring the front-end's id scheme so ``EXPOSES`` lands on a real node."""
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            _collect_routes(
                stmt.body, parent_id=f"{parent_id}.{stmt.name}", module_id=module_id, rel=rel, state=state
            )
            continue
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        handler_id = f"{parent_id}.{stmt.name}"
        # Descend into the body: an app factory (``def create_app(): @app.get(…)``) declares
        # its routes inside a function, and skipping them lost 12 of this repo's 77.
        _collect_routes(stmt.body, parent_id=handler_id, module_id=module_id, rel=rel, state=state)
        for decorator in stmt.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            receiver = _receiver(decorator.func)
            attr = getattr(decorator.func, "attr", "")
            if receiver is None or not decorator.args:
                continue
            path = _literal_path(decorator.args[0])
            if path is None:  # computed path — emit nothing rather than a wrong route
                continue
            if attr in _VERB_ATTRS:
                verbs = [attr.upper()]
            elif attr in {"route", "add_url_rule"}:
                verbs = _verbs_from_methods(decorator)
            else:
                continue
            for verb in verbs:
                state.routes.append(
                    PendingRoute(
                        verb=verb,
                        path=path,
                        router_key=f"{module_id}.{receiver}",
                        handler_id=handler_id,
                        provenance=Provenance(rel, decorator.lineno),
                    )
                )


def _collect_django(
    tree: ast.Module, *, module_id: str, rel: str, imports: dict[str, str], state: RouteState
) -> None:
    """``urlpatterns = [path("runs/", views.list_runs)]`` in a ``urls.py``.

    Only files named ``urls.py`` are read this way: ``path`` is far too common a name to
    treat as a route anywhere else, and a false endpoint is worse than a missing one.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if called not in _DJANGO_ROUTE or len(node.args) < 2:
            continue
        path = _literal_path(node.args[0])
        if path is None:
            continue
        view = node.args[1]
        if isinstance(view, ast.Call) and isinstance(view.func, ast.Attribute):
            # ``TemplateView.as_view()`` — a class-based view, not a named function.
            continue
        handler = _resolve_router(view, module_id, imports)
        if handler is None:
            continue
        state.routes.append(
            PendingRoute(
                verb=_DJANGO_VERB,
                path=path,
                router_key="",  # a URL conf is mounted by include(), handled as its own prefix
                handler_id=handler,
                provenance=Provenance(rel, node.lineno),
            )
        )


def emit(state: RouteState, batch: FactBatch) -> None:
    """Compose every route's full path and add its ``Endpoint`` + ``EXPOSES`` to ``batch``.

    A router mounted twice yields two endpoints, which is the truth: the handler serves both
    paths. A router with no resolvable mount yields one, at its local path.
    """
    for route in state.routes:
        local = state.local_prefixes.get(route.router_key, "")
        prefixes = state.mounts.get(route.router_key) or [""]
        for mount in prefixes:
            full = _join(mount, local, route.path)
            endpoint_id = f"py:endpoint:{route.verb} {full}"
            batch.add_node(
                Node(
                    endpoint_id,
                    NodeKind.ENDPOINT,
                    f"{route.verb} {full}",
                    "python",
                    route.provenance,
                )
            )
            batch.add_edge(Edge(endpoint_id, route.handler_id, EdgeKind.EXPOSES, route.provenance))


__all__ = ["PendingRoute", "RouteState", "emit", "scan_module"]
