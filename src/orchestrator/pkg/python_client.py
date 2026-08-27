"""HTTP calls in Python source → ``CONSUMES`` edges (caller → the endpoint it calls).

``python_routes`` closed half of a join: an ``Endpoint`` node and an ``EXPOSES`` edge to
the handler that serves it. The other half was still missing, and the graph said so —
**zero** edges pointed *at* an endpoint. A route was a leaf: something the server
declared, that nothing in the repo appeared to want.

That is a wrong answer with consequences. ``orchestrator template list`` calls
``GET /v1/agent-templates``; the handler for it lives in ``registry/api/``. Before this,
nothing connected the two, so a ticket about "the registry API" retrieved the *server*
modules and never reached the CLI that calls them — which is exactly what happened on
SSPN-49, where the investigation brief named eight server modules and never ``cli.py``.

**The same precision rule as routes: resolve what is literal, skip what is computed.**
A path built from an f-string or held in a variable yields no edge. A wrong edge is worse
than an absent one, because every surface downstream presents it as grounded.

**And one more: never point at an endpoint that does not exist.** The join is by
``(verb, path)`` against the endpoints already in the batch, so a call to a third-party
API — or to a path this repo does not serve — produces nothing rather than a dangling
edge. This is why emission is deferred to ``finalize`` and ordered *after* routes.

**Detection is gated on the module importing an HTTP library** (``httpx``, ``requests``,
``aiohttp``, ``urllib3``). Without that gate, ``config.get("/default")`` on a plain dict
reads as a GET, and a graph that invents traffic is worse than one that misses some.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind, Provenance

# Method names that name an HTTP verb directly: ``client.get("/x")``.
_VERB_ATTRS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# Importing one of these is what makes a `.get("/x")` an HTTP call rather than a lookup.
_HTTP_LIBS = frozenset({"httpx", "requests", "aiohttp", "urllib3", "http"})


@dataclass(frozen=True)
class PendingCall:
    """One HTTP call found in one file, before we know which endpoints exist."""

    verb: str
    path: str
    caller_id: str
    provenance: Provenance


@dataclass
class ClientState:
    """Calls accumulated across the walk, emitted once every endpoint is known."""

    calls: list[PendingCall] = field(default_factory=list)
    #: Calls that matched no endpoint in this repository. **Not facts, and deliberately not.**
    #: See :func:`emit`.
    unmatched: list[PendingCall] = field(default_factory=list)

    def clear(self) -> None:
        """Clear the pending calls. ``unmatched`` survives — it is the run's output."""
        self.calls.clear()


def _norm(path: str) -> str:
    """One spelling for a path, so both sides of the join agree.

    A full URL keeps only its path — ``httpx.get("http://localhost:8000/v1/runs")`` is a
    call to ``/v1/runs``, and the host is deployment detail the graph has no opinion on.
    """
    if "://" in path:
        path = urlsplit(path).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _literal(node: ast.expr | None) -> str | None:
    """The string this expression *is*, or None when it is computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _uses_http(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in _HTTP_LIBS for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _HTTP_LIBS:
            return True
    return False


def _call_of(node: ast.Call) -> tuple[str, str] | None:
    """``(verb, path)`` for an HTTP call, or None when this is not one.

    Two shapes: the verb as the method (``client.get("/x")``) and the verb as an
    argument (``client.request("GET", "/x")``). Both must carry a literal path.
    """
    attr = getattr(node.func, "attr", "")
    if attr in _VERB_ATTRS:
        path = _literal(node.args[0]) if node.args else None
        return (attr.upper(), path) if path else None
    if attr == "request" and len(node.args) >= 2:
        verb, path = _literal(node.args[0]), _literal(node.args[1])
        if verb and path and verb.upper() in {v.upper() for v in _VERB_ATTRS}:
            return verb.upper(), path
    return None


def _collect(body: list[ast.stmt], *, parent_id: str, caller_id: str, rel: str, state: ClientState) -> None:
    """Walk defs, mirroring the front-end's id scheme so the edge starts at a real node.

    ``caller_id`` is the innermost enclosing function — the thing a reader would name as
    "what calls this endpoint". At module level it is the module itself, which is honest:
    a call in module scope really is the module doing it.
    """
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            _collect(
                stmt.body, parent_id=f"{parent_id}.{stmt.name}", caller_id=caller_id, rel=rel, state=state
            )
            continue
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            fn_id = f"{parent_id}.{stmt.name}"
            _collect(stmt.body, parent_id=fn_id, caller_id=fn_id, rel=rel, state=state)
            continue
        # Any other statement: scan it whole for calls, attributed to the enclosing def.
        # `with _client() as c: _check(c.get("/x"))` is the shape this repo actually uses,
        # and a walk that only looked at bare Expr statements would miss all of it.
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            found = _call_of(node)
            if found is None:
                continue
            verb, path = found
            state.calls.append(
                PendingCall(
                    verb=verb,
                    path=_norm(path),
                    caller_id=caller_id,
                    provenance=Provenance(rel, node.lineno),
                )
            )


def scan_module(tree: ast.Module, *, module_id: str, rel: str, state: ClientState) -> None:
    """Collect this file's HTTP calls into ``state``. No emission — endpoints come later."""
    if not _uses_http(tree):
        return
    _collect(tree.body, parent_id=module_id, caller_id=module_id, rel=rel, state=state)


def emit(state: ClientState, batch: FactBatch) -> None:
    """Join each collected call to the endpoint it calls, when that endpoint exists.

    Must run *after* ``python_routes.emit`` — the endpoints it joins against are the ones
    that pass added to this same batch. A call that matches nothing emits **no edge**: it is
    either a third-party API or a path this repo does not serve, and neither is a fact about
    this codebase.

    **It is recorded on ``state.unmatched`` rather than discarded, and that distinction is the
    whole design.** In a multi-repo graph these calls are the candidates for a cross-repo join —
    a call to a path *another* declared repository serves. Losing them means the join has
    nothing to propose from.

    But they stay **out of the batch**. Emitting an edge here would assert *"this function calls
    `POST /v1/orders`"* about an endpoint nothing in scope is known to serve, and creating the
    endpoint node alongside it would make ``pkg verify`` report zero dangling — the
    self-consistent invention this project has now removed twice. ``invention`` would not catch
    it either: that oracle detects shadowing and nothing else.

    So the graph is byte-identical with and without this bookkeeping, which is what keeps the
    commit-keyed cache, the committed scoreboard and every corpus fixture valid. The shape is
    ``RepoCodeExtractor.skipped``: a list on the object, never a node in the graph.
    """
    endpoints = {node.name: node.id for node in batch.nodes if node.kind is NodeKind.ENDPOINT}
    for call in state.calls:
        endpoint_id = endpoints.get(f"{call.verb} {call.path}")
        if endpoint_id is None:
            state.unmatched.append(call)
            continue
        batch.add_edge(Edge(call.caller_id, endpoint_id, EdgeKind.CONSUMES, call.provenance))


__all__ = ["ClientState", "PendingCall", "emit", "scan_module"]
