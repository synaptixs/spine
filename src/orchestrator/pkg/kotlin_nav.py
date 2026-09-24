"""Compose navigation → in-app routes as ``Endpoint``s with verb ``NAV``.

P4 of docs/specs/kotlin-support-roadmap.md, D14.

``composable("topic/{topicId}") { TopicRoute(…) }`` *declares* a route and
``navController.navigate("topic/$id")`` *consumes* one. That is the same shape as
an HTTP route with the app as both provider and consumer, so it reuses the same
vocabulary rather than adding a node kind: ``Endpoint`` named ``NAV topic/{topicId}``,
``EXPOSES`` to the screen it shows, ``CONSUMES`` from the function that navigates.
``NAV`` cannot collide with an HTTP verb, so an in-app route never joins to a real
one in ``pkg joins``.

**Routes are constants, not literals — which is the whole difficulty.** The
textbook form passes a string, but real navigation code names a route once and
imports it: the validation app writes ``composable(route = forYouNavigationRoute)``
and declares ``const val forYouNavigationRoute = "for_you_route"`` in *another
file*. A reader that only accepts string literals finds almost nothing. So route
constants are collected across the whole walk and resolved in ``finalize``, the
same two-stage shape the Room table join uses.

**Two paths match when they differ only in their parameters.** A declaration
writes ``"topic_route/{$topicIdArg}"`` and the call writes ``"topic_route/$encodedId"``.
Both normalise to ``topic_route/{}`` for matching, while the endpoint keeps the
readable resolved form (``topic_route/{topicId}``) as its name. Without that, the
declaration and the call would never pair and every in-app route would look unused.

**What it refuses.** A route built by concatenation or a function call yields
nothing. A ``composable`` lambda that calls no named screen, or several, gets an
``Endpoint`` but no ``EXPOSES`` — the closure rule, because picking one of several
would be a guess about which one is "the" screen.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.kotlin_names import decoded_escape, string_value, text

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "kotlin"

#: The pseudo-verb that keeps in-app routes out of the HTTP join (D14).
NAV = "NAV"

#: `{anything}` or `$ident` — the parts of a route that vary per navigation.
_PARAM = re.compile(r"\{[^}]*\}|\$\w+")

#: The short interpolation form the grammar does not tag; group 1 is the name.
_BARE_INTERPOLATION = re.compile(r"\$(\w+)")


@dataclass(frozen=True)
class _Part:
    """One piece of a route: literal text, or the name of a constant to substitute."""

    kind: Literal["text", "const"]
    value: str


#: A route expression, decomposed. Empty means the empty route, which is a real one.
_Template = tuple[_Part, ...]


@dataclass(frozen=True)
class _Site:
    """Where a route expression was written, and what names were in scope there.

    A route constant is resolved from the *using* file's point of view, so the
    package and import map of that file travel with the route rather than being
    looked up later — by ``finalize`` the file is long gone.
    """

    package: str
    #: simple name → fully-qualified name, from this file's `import` headers
    imports: Mapping[str, str]
    rel: str
    line: int
    #: this file's ``import a.b.*`` prefixes — #395: the repo-wide unique-name
    #: fallback in ``NavState.lookup`` is restricted to names reachable through one
    #: of these, the same rule ``_type_candidates`` already applies in the extractor.
    wildcard_prefixes: frozenset[str]


@dataclass
class NavState:
    """Routes, declarations and navigations collected across the whole walk.

    Everything here waits for ``finalize`` because a route constant is almost
    always declared in a different file from the ``composable`` that uses it.

    **Constants are keyed by package, not by bare name.** Found in review: a flat
    repo-wide dict silently merges two modules that each declare ``const val
    route`` — the survivor wins, the other module's ``composable`` is credited with
    a path its source never contains, and one endpoint collects an ``EXPOSES`` to
    both screens. Two modules declaring the same constant name is the ordinary
    Compose feature-module convention, not a corner case.
    """

    #: (package, `const val` name) → its literal value, from every file seen so far
    consts: dict[tuple[str, str], str] = field(default_factory=dict)
    #: (route template, screen id or "", site)
    declarations: list[tuple[_Template, str, _Site]] = field(default_factory=list)
    #: (route template, calling function id, site)
    navigations: list[tuple[_Template, str, _Site]] = field(default_factory=list)

    def clear(self) -> None:
        self.consts.clear()
        self.declarations.clear()
        self.navigations.clear()

    def lookup(self, name: str, site: _Site) -> str | None:
        """The value of constant ``name`` as ``site`` sees it, or ``None``.

        Own package first, then an explicit import, then a fallback restricted to a
        package reachable through one of this file's ``import a.b.*`` prefixes —
        firing only when exactly one such package declares the name, the same
        "ambiguous means unresolved" rule ``kotlin_routes`` applies to mount names.

        #395: the fallback used to consider a package declaring the name *anywhere
        in the repository*, with no restriction to what this file could actually
        see — a route or a local variable of the same name could then be captured by
        an unrelated module's constant across package and service boundaries, with
        no import at all. Restricting it to a recorded wildcard prefix, the same
        rule ``_type_candidates`` applies in the extractor, closes that while
        keeping the genuine wildcard-import case working.
        """
        own = self.consts.get((site.package, name))
        if own is not None:
            return own
        imported = site.imports.get(name)
        if imported is not None and "." in imported:
            package, _, simple = imported.rpartition(".")
            found = self.consts.get((package, simple))
            if found is not None:
                return found
        matches = {
            value
            for (pkg, simple), value in self.consts.items()
            if simple == name and pkg in site.wildcard_prefixes
        }
        return matches.pop() if len(matches) == 1 else None


def collect_consts(root: TSNode, source: bytes, state: NavState, *, package: str) -> None:
    """Record every top-level ``const val NAME = "literal"`` in this file."""
    for node in root.named_children:
        if node.type != "property_declaration":
            continue
        decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
        if decl is None:
            continue
        name = next((text(c, source) for c in decl.named_children if c.type == "identifier"), "")
        literal = next(
            (string_value(c, source) for c in node.named_children if c.type == "string_literal"),
            None,
        )
        if name and literal:
            state.consts[package, name] = literal


def scan_calls(
    body: TSNode,
    func_id: str,
    source: bytes,
    rel: str,
    state: NavState,
    *,
    package: str,
    imports: Mapping[str, str],
    wildcard_prefixes: frozenset[str],
) -> None:
    """Collect ``composable(...)`` declarations and ``navigate(...)`` calls in a body."""
    for call in _walk(body):
        if call.type != "call_expression" or _is_inner_callee(call):
            continue
        # A trailing lambda wraps the call it decorates: `composable(route = r) { … }`
        # is an outer `call_expression` whose callee is the inner `composable(route = r)`.
        # The arguments live on the inner node and the lambda on the outer, so both
        # have to be read from the right one — reading only the node whose callee is
        # named `composable` finds the route and never the screen.
        inner = _arguments_holder(call)
        name = _callee_name(inner, source)
        if name not in ("composable", "navigate"):
            continue
        argument = _argument_node(inner, source, named="route" if name == "composable" else "")
        template = _template(argument, source)
        if template is None:
            continue
        site = _Site(package, imports, rel, call.start_point[0] + 1, wildcard_prefixes)
        if name == "composable":
            state.declarations.append((template, _single_screen(call, source), site))
        else:
            state.navigations.append((template, func_id, site))


def emit(state: NavState, batch: FactBatch, resolve: Any) -> None:
    """Turn collected routes into ``Endpoint`` + ``EXPOSES`` + ``CONSUMES``.

    Runs once, in ``finalize``, when every route constant in the repository is known.
    """
    by_key: dict[str, str] = {}
    for template, screen, site in state.declarations:
        path = _resolve(template, state, site)
        if path is None:
            continue
        endpoint_id = f"java:endpoint:{NAV} {path}"
        provenance = Provenance(site.rel, site.line)
        batch.add_node(Node(endpoint_id, NodeKind.ENDPOINT, f"{NAV} {path}", _LANG, provenance))
        by_key.setdefault(_key(path), endpoint_id)
        target = resolve(screen) if screen else None
        if target:
            batch.add_edge(Edge(endpoint_id, target, EdgeKind.EXPOSES, provenance))

    for template, caller, site in state.navigations:
        path = _resolve(template, state, site)
        if path is None:
            continue
        declared = by_key.get(_key(path))
        if declared is None:
            # Navigating to a route nothing in this tree declares. Saying nothing is
            # the honest answer — inventing the endpoint would make `pkg verify`
            # report zero dangling for a destination that does not exist.
            continue
        batch.add_edge(Edge(caller, declared, EdgeKind.CONSUMES, Provenance(site.rel, site.line)))


def _argument_node(call: TSNode, source: bytes, *, named: str = "") -> TSNode | None:
    """The route argument's expression node — named when given, else the first one."""
    args = next((c for c in call.named_children if c.type == "value_arguments"), None)
    if args is None:
        return None
    positional: list[TSNode] = []
    for arg in args.named_children:
        if arg.type != "value_argument" or not arg.named_children:
            continue
        children = arg.named_children
        if named and len(children) >= 2 and children[0].type == "identifier":
            if text(children[0], source) == named:
                return children[-1]
            continue
        if len(children) == 1:
            positional.append(children[0])
    return positional[0] if positional else None


def _template(node: TSNode | None, source: bytes) -> _Template | None:
    """A route expression → the parts it is built from, or ``None`` when it is computed.

    Two spellings are routes; everything else is refused. A bare identifier is a
    constant reference, resolved against the constants collected repo-wide. A string
    literal becomes its literal chunks interleaved with the simple names interpolated
    into it, because a Compose route is genuinely written that way —
    ``"topic_route/{$topicIdArg}"`` names its parameter with a constant.

    Found in review: this used to be the *raw source text* of the argument, and
    ``_resolve`` recognised a literal by ``raw.startswith('"')`` and then stripped the
    first and last character. So ``composable(route = "topic/" + BASE)`` — an
    ``additive_expression``, not a literal at all — produced the route ``topic/" + BAS``
    and an ``Endpoint`` node with it. Reading the parsed node instead means a
    concatenation, a function call or a template with a computed expression in it is
    refused by the grammar rather than by a string test that cannot see the shape.
    """
    if node is None:
        return None
    if node.type == "identifier":
        return (_Part(kind="const", value=text(node, source)),)
    if node.type not in ("string_literal", "multiline_string_literal"):
        return None  # a concatenation, a call, `buildString { }` — computed, so never
    parts: list[_Part] = []
    # Consecutive `string_content` children are joined before the short-form split,
    # because the grammar hands `"item_route/{$itemIdArg}"` back as the four chunks
    # `item_route/{`, `$`, `itemIdArg`, `}` — splitting each chunk on its own never
    # sees a `$` beside its name and reads the whole route as literal text.
    literal = ""
    for child in node.named_children:
        if child.type == "string_content":
            literal += text(child, source)
            continue
        # An escaped dollar is literal text and must not become a name, so the buffer
        # is flushed around it rather than split through it.
        parts.extend(_split_bare_interpolation(literal))
        literal = ""
        if child.type == "escape_sequence":
            decoded = decoded_escape(text(child, source))
            if decoded is None:
                return None
            parts.append(_Part(kind="text", value=decoded))
        elif child.type == "interpolation":
            inner = text(child, source).lstrip("$").strip("{}").strip()
            if not inner.isidentifier():
                return None  # `${cfg.version}`, `${a + b}` — a computed segment
            parts.append(_Part(kind="const", value=inner))
        else:
            return None
    parts.extend(_split_bare_interpolation(literal))
    return tuple(parts)


def _split_bare_interpolation(chunk: str) -> list[_Part]:
    """Split ``topic/$id/x`` into text and name parts.

    tree-sitter-kotlin 1.1.0 tags ``${x}`` as an ``interpolation`` node but leaves the
    short ``$x`` form as plain ``string_content``, so the split has to be done here.
    """
    out: list[_Part] = []
    for index, piece in enumerate(_BARE_INTERPOLATION.split(chunk)):
        if not piece:
            continue
        out.append(_Part(kind="const" if index % 2 else "text", value=piece))
    return out


def _resolve(template: _Template, state: NavState, site: _Site) -> str | None:
    """A route template → its path, or ``None`` when it cannot be known.

    A name that resolves to a constant is substituted. A name that does not stays
    written as ``$name``: in a route that is a *parameter*, not a missing constant —
    a declaration writes ``topic_route/{$topicIdArg}`` and the matching call writes
    ``topic_route/$encodedId``, and :func:`_key` collapses both to ``topic_route/{}``
    so the two pair. A route consisting of nothing but an unresolved bare constant is
    refused, because there is no path there at all.
    """
    if len(template) == 1 and template[0].kind == "const":
        value = state.lookup(template[0].value, site)
        return value if value is not None else None
    out: list[str] = []
    for part in template:
        if part.kind == "text":
            out.append(part.value)
            continue
        value = state.lookup(part.value, site)
        out.append(value if value is not None else f"${part.value}")
    return "".join(out)


def _key(path: str) -> str:
    """One spelling for matching: every parameter segment collapses to ``{}``.

    ``topic_route/{topicId}`` from the declaration and ``topic_route/$encodedId``
    from the call are the same route; only this normalisation makes them pair.
    """
    return _PARAM.sub("{}", path)


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


def _callee_name(call: TSNode, source: bytes) -> str:
    callee = next(iter(call.named_children), None)
    if callee is None:
        return ""
    if callee.type == "identifier":
        return text(callee, source)
    if callee.type == "navigation_expression":  # `this.navigate(...)`, `controller.navigate(...)`
        parts = callee.named_children
        if parts and parts[-1].type == "identifier":
            return text(parts[-1], source)
    return ""


def _argument_text(call: TSNode, source: bytes, *, named: str = "") -> str:
    """The raw text of the route argument — named when given, else the first one."""
    args = next((c for c in call.named_children if c.type == "value_arguments"), None)
    if args is None:
        return ""
    positional: list[str] = []
    for arg in args.named_children:
        if arg.type != "value_argument" or not arg.named_children:
            continue
        children = arg.named_children
        if named and len(children) >= 2 and children[0].type == "identifier":
            if text(children[0], source) == named:
                return text(children[-1], source)
            continue
        if len(children) == 1:
            positional.append(text(children[0], source))
    return positional[0] if positional else ""


def _single_screen(call: TSNode, source: bytes) -> str:
    """The one named screen a ``composable`` lambda shows, or ``""``.

    Compose names screens with a capitalised function, which is what makes this
    readable at all. Zero or several means no ``EXPOSES`` — the closure rule from
    D14: a lambda that shows two things does not have "the" screen, and picking
    one would be a guess.
    """
    lambda_node = next((c for c in call.named_children if c.type == "annotated_lambda"), None)
    if lambda_node is None:
        return ""
    names = []
    for inner in _walk(lambda_node):
        if inner.type != "call_expression":
            continue
        name = _callee_name(inner, source)
        if name[:1].isupper() and name not in names:
            names.append(name)
    return names[0] if len(names) == 1 else ""


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


__all__ = ["NAV", "NavState", "collect_consts", "emit", "scan_calls"]
