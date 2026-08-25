"""What a call site's enclosing scopes bind — per language, over the same parser.

The invention oracle asks one question of a ``CALLS`` edge: *was the name at the call
site bound by the calling function itself?* If it was, the call goes through a parameter,
a local, or a nested closure, and any edge to a file-level definition of that name is
fiction. :mod:`orchestrator.pkg.invention` answers that question for Python with the
stdlib ``ast``. This module answers it for the tree-sitter front-ends.

**This is a second implementation on purpose, and that is the whole point of an oracle.**
``c_extractor._bound_names`` already computes bindings, and reusing it here would build a
detector that agrees with the extractor by construction — including where the extractor is
wrong. The measurement is only worth taking if it can disagree. What is deliberately *not*
duplicated is the parser: each language is parsed with the same factory the front-end used,
because a second grammar would be a second opinion about the syntax, and then a disagreement
would not tell us which side was wrong.

**Only bindings inside a function count.** A file-level ``function send() {}`` binds ``send``
too, but that is the target the extractor resolved to, and calling it is correct. Invention is
*shadowing* — a binding strictly inside the calling function, which the front-ends' name→id
tables (file-level callables, sibling methods) cannot see. So :class:`Scope` carries ``local``,
and the file scope is excluded from the test.

Five languages are walked here. Two are excluded, with reasons rather than silence:

- **Java** — the JLS gives variables and methods separate namespaces (§6.5.7), so ``int send``
  does not shadow ``send()``. A shadowing test would report false positives, not findings.
- **SQL** — the ``CALL``/``PERFORM`` fallback matches two keywords and a parenthesis; there is
  no lexical scope for a name to be bound in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from tree_sitter import Node as TSNode


@dataclass(frozen=True)
class Binding:
    """A name, and the first line at which it is in scope.

    The line matters, and getting it wrong invents findings. Go's spec starts a short
    variable declaration's scope *after* the statement, so ``cmd := cmd(binaryPath, …)``
    calls the package-level ``cmd`` — legitimately — while binding a local of the same name
    for everything below it. Treating the binding as covering its own declaration reported
    that as fiction; it is idiomatic Go, and C and C++ read the same way.

    Parameters are in scope from the head of the function; declarations from the line after
    they end. TypeScript and C# are held to the same rule even though both forbid
    use-before-declaration outright — where the language makes the earlier call impossible,
    the conservative reading costs nothing, and this oracle is precision-first for the same
    reason the graph is.
    """

    name: str
    line: int


@dataclass(frozen=True)
class Scope:
    """A line range and the names bound directly in it.

    ``local`` is False only for the file scope. A name bound there is a declaration the
    front-end can resolve, not a shadow of one.
    """

    start: int
    end: int
    bindings: tuple[Binding, ...]
    local: bool

    @property
    def names(self) -> frozenset[str]:
        return frozenset(b.name for b in self.bindings)


@dataclass(frozen=True)
class BareCall:
    """A call site whose callee is a plain identifier — the only shape that can be shadowed.

    ``obj.method()`` is excluded: the name that could be shadowed there is ``obj``, and
    resolving it is a different question (the front-ends already refuse it without a type).
    """

    line: int
    name: str


@dataclass(frozen=True)
class FileScopes:
    """Every scope in one file, and every bare-identifier call site."""

    scopes: tuple[Scope, ...]
    calls: tuple[BareCall, ...]

    def shadowed(self, line: int) -> frozenset[str]:
        """Names bound by some function enclosing ``line``, and in scope *at* it.

        The file scope is excluded: a name bound there is the declaration the front-end
        resolved to, and calling it is correct.
        """
        return frozenset(
            b.name
            for s in self.scopes
            if s.local and s.start <= line <= s.end
            for b in s.bindings
            if line >= b.line
        )

    def bare_call_names(self, line: int) -> frozenset[str]:
        return frozenset(c.name for c in self.calls if c.line == line)


# ---- the per-language contract ---------------------------------------------


class _Lang(Protocol):
    """One language's answer to: what opens a scope, what binds a name, what is a call."""

    scope_nodes: frozenset[str]
    call_nodes: frozenset[str]

    def params(self, node: TSNode, src: bytes) -> Iterable[str]:
        """Names a scope node binds in *its own* body — parameters, captures."""

    def declares(self, node: TSNode, src: bytes) -> Iterable[str]:
        """Names this node binds in the *enclosing* scope — locals, loop vars, catch targets."""


def _text(node: TSNode | None, src: bytes) -> str:
    return "" if node is None else src[node.start_byte : node.end_byte].decode("utf-8", "replace")


@dataclass
class _Open:
    start: int
    end: int
    local: bool
    bindings: list[Binding] = field(default_factory=list)


def collect(root: TSNode, src: bytes, lang: _Lang) -> FileScopes:
    """Walk once: attribute every binding to its innermost scope, record every bare call.

    Scopes are recorded as line ranges and tested by containment afterwards rather than
    resolved during the walk, so declaration order inside a scope does not change what a
    later line sees — only :class:`Binding`'s own line does.
    """
    opened: list[_Open] = [_Open(1, 1 << 30, local=False)]

    def visit(node: TSNode, current: _Open) -> None:
        scope = current
        if node.type in lang.scope_nodes:
            start = node.start_point[0] + 1
            scope = _Open(
                start,
                node.end_point[0] + 1,
                local=True,
                bindings=[Binding(n, start) for n in lang.params(node, src)],
            )
            opened.append(scope)
        else:
            # A declaration binds from the line after it ends — see `Binding`.
            from_line = node.end_point[0] + 2
            current.bindings.extend(Binding(n, from_line) for n in lang.declares(node, src))
            if node.type in lang.call_nodes:
                fn = node.child_by_field_name("function")
                if fn is not None and fn.type == "identifier":
                    calls.append(BareCall(node.start_point[0] + 1, _text(fn, src)))
        for child in node.named_children:
            visit(child, scope)

    calls: list[BareCall] = []
    visit(root, opened[0])
    return FileScopes(
        scopes=tuple(Scope(o.start, o.end, tuple(o.bindings), o.local) for o in opened),
        calls=tuple(calls),
    )


# ---- helpers shared by more than one walker --------------------------------


def _idents(node: TSNode | None, src: bytes, kinds: frozenset[str]) -> Iterator[str]:
    """Every identifier of ``kinds`` in a subtree — for destructuring patterns and name lists.

    Safe only on subtrees that hold no type annotation: a pattern's type is a *sibling* field
    in every grammar walked here, never a child, so descending cannot pick up a type's own
    parameter names.
    """
    if node is None:
        return
    if node.type in kinds:
        yield _text(node, src)
        return
    for child in node.named_children:
        yield from _idents(child, src, kinds)


def _named_fields(node: TSNode, src: bytes, fname: str) -> Iterator[str]:
    """Every child of ``node`` under field ``fname`` — Go's ``a, b int`` repeats ``name``."""
    for i, child in enumerate(node.children):
        if child.is_named and node.field_name_for_child(i) == fname:
            yield _text(child, src)


# ---- TypeScript -------------------------------------------------------------

_TS_PATTERN_IDS = frozenset({"identifier", "shorthand_property_identifier_pattern"})

# Field to follow instead of every named child, for the pattern nodes that carry an
# expression alongside the name. `const { arg: slotName = createSimpleExpression(…) } = x`
# binds `slotName`; a blind identifier sweep also binds `createSimpleExpression`, and then
# every legitimate call to that import reads as a shadowed call. Measured on vue/core:
# 3 of 4 findings were this, and all three were fiction.
_TS_PATTERN_FIELD = {"pair_pattern": "value", "assignment_pattern": "left"}


def _ts_pattern_names(node: TSNode | None, src: bytes) -> Iterator[str]:
    """Names a TypeScript binding pattern binds — never a default value's contents."""
    if node is None:
        return
    if node.type in _TS_PATTERN_IDS:
        yield _text(node, src)
        return
    field_name = _TS_PATTERN_FIELD.get(node.type)
    if field_name is not None:
        yield from _ts_pattern_names(node.child_by_field_name(field_name), src)
        return
    if node.type in ("object_pattern", "array_pattern", "rest_pattern", "object_assignment_pattern"):
        for child in node.named_children:
            yield from _ts_pattern_names(child, src)


class _TypeScript:
    scope_nodes = frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "function_expression",
            "generator_function",
            "function",
            "arrow_function",
            "method_definition",
        }
    )
    call_nodes = frozenset({"call_expression"})

    def params(self, node: TSNode, src: bytes) -> Iterable[str]:
        # `x => x()` binds through the `parameter` field; everything else through
        # `parameters`. Only the DIRECT children of `formal_parameters` are read: a
        # parameter typed `(v: string) => void` carries its own `formal_parameters`
        # under the `type` field, and `v` is bound nowhere.
        single = node.child_by_field_name("parameter")
        if single is not None:
            return list(_ts_pattern_names(single, src))
        params = node.child_by_field_name("parameters")
        if params is None:
            return []
        out: list[str] = []
        for child in params.named_children:
            pattern = child.child_by_field_name("pattern") or (
                child if child.type in _TS_PATTERN_IDS else None
            )
            out.extend(_ts_pattern_names(pattern, src))
        return out

    def declares(self, node: TSNode, src: bytes) -> Iterable[str]:
        if node.type == "variable_declarator":  # let / const / var
            return _ts_pattern_names(node.child_by_field_name("name"), src)
        if node.type in ("for_in_statement", "for_statement"):
            return _ts_pattern_names(node.child_by_field_name("left"), src)
        if node.type == "catch_clause":
            return _ts_pattern_names(node.child_by_field_name("parameter"), src)
        if node.type in ("function_declaration", "generator_function_declaration"):
            return [_text(node.child_by_field_name("name"), src)]
        return []


# ---- Go ---------------------------------------------------------------------


class _Go:
    scope_nodes = frozenset({"function_declaration", "method_declaration", "func_literal"})
    call_nodes = frozenset({"call_expression"})

    def params(self, node: TSNode, src: bytes) -> Iterable[str]:
        out: list[str] = []
        for fname in ("receiver", "parameters", "type_parameters", "result"):
            plist = node.child_by_field_name(fname)
            if plist is None:
                continue
            # Direct children only: `Send func(string)` nests a `parameter_list` under its
            # `type`, and those inner names are bound nowhere.
            for decl in plist.named_children:
                out.extend(_named_fields(decl, src, "name"))
        return out

    def declares(self, node: TSNode, src: bytes) -> Iterable[str]:
        if node.type == "short_var_declaration":  # `x := ...`
            return _idents(node.child_by_field_name("left"), src, frozenset({"identifier"}))
        if node.type in ("var_spec", "const_spec"):
            return _named_fields(node, src, "name")
        if node.type == "range_clause":
            return _idents(node.child_by_field_name("left"), src, frozenset({"identifier"}))
        if node.type == "type_switch_statement":
            return _idents(node.child_by_field_name("alias"), src, frozenset({"identifier"}))
        return []


# ---- C# ---------------------------------------------------------------------


class _CSharp:
    scope_nodes = frozenset(
        {
            "method_declaration",
            "constructor_declaration",
            "destructor_declaration",
            "operator_declaration",
            "local_function_statement",
            "lambda_expression",
            "anonymous_method_expression",
            "accessor_declaration",
        }
    )
    call_nodes = frozenset({"invocation_expression"})

    def params(self, node: TSNode, src: bytes) -> Iterable[str]:
        out: list[str] = []
        for fname in ("parameters", "parameter_list"):
            plist = node.child_by_field_name(fname)
            if plist is None:
                continue
            for p in plist.named_children:
                name = p.child_by_field_name("name")
                out.append(_text(name, src) if name is not None else _text(p, src))
        return [n for n in out if n]

    def declares(self, node: TSNode, src: bytes) -> Iterable[str]:
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            return [_text(name if name is not None else node, src)]
        if node.type in ("foreach_statement", "catch_declaration"):
            target = node.child_by_field_name("left") or node.child_by_field_name("name")
            return [_text(target, src)] if target is not None else []
        if node.type == "local_function_statement":
            return [_text(node.child_by_field_name("name"), src)]
        return []


# ---- C and C++ --------------------------------------------------------------

# Declarator wrappers, in the order the grammar nests them: `void (*send)(char *)` is
# function_declarator → parenthesized_declarator → pointer_declarator → identifier. Each is
# followed through its `declarator` field only — descending into `parameters` would bind the
# parameter names of a function *pointer*, which are bound nowhere.
_DECL_WRAPPERS = frozenset(
    {
        "init_declarator",
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "function_declarator",
        "parenthesized_declarator",
        "attributed_declarator",
        "abstract_function_declarator",
    }
)
_DECL_NAMES = frozenset({"identifier", "field_identifier", "qualified_identifier"})
_UNFIELDED_WRAPPERS = frozenset({"parenthesized_declarator", "reference_declarator", "pointer_declarator"})


def _declarator_name(node: TSNode | None, src: bytes) -> str | None:
    """The innermost declared name of a C/C++ declarator, or None for an abstract one."""
    cur = node
    seen = 0
    while cur is not None and seen < 32:  # a declarator cannot nest meaningfully deeper
        if cur.type in _DECL_NAMES:
            return _text(cur, src)
        if cur.type not in _DECL_WRAPPERS:
            return None
        nxt = cur.child_by_field_name("declarator")
        if nxt is None and cur.type in _UNFIELDED_WRAPPERS:
            # `for (auto &item : xs)` nests the identifier under `reference_declarator`
            # with no field name at all; parentheses do the same.
            nxt = cur.named_children[0] if cur.named_children else None
        cur = nxt
        seen += 1
    return None


def _c_parameters(node: TSNode) -> TSNode | None:
    """The ``parameter_list`` of a definition, through any declarator wrappers around it."""
    cur = node.child_by_field_name("declarator")
    seen = 0
    while cur is not None and seen < 32:
        plist = cur.child_by_field_name("parameters")
        if plist is not None:
            return plist
        cur = cur.child_by_field_name("declarator")
        seen += 1
    return None


class _CFamily:
    call_nodes = frozenset({"call_expression"})

    def __init__(self, *, scope_nodes: frozenset[str]) -> None:
        self.scope_nodes = scope_nodes

    def params(self, node: TSNode, src: bytes) -> Iterable[str]:
        plist = _c_parameters(node)
        if plist is None:
            return []
        out: list[str] = []
        for p in plist.named_children:
            name = _declarator_name(p.child_by_field_name("declarator"), src)
            if name:
                out.append(name)
        return out

    def declares(self, node: TSNode, src: bytes) -> Iterable[str]:
        if node.type == "declaration":
            out: list[str] = []
            for i, child in enumerate(node.children):
                if child.is_named and node.field_name_for_child(i) == "declarator":
                    name = _declarator_name(child, src)
                    if name:
                        out.append(name)
            return out
        if node.type in ("for_range_loop", "init_declarator"):
            name = _declarator_name(node.child_by_field_name("declarator"), src)
            return [name] if name else []
        return []


_C = _CFamily(scope_nodes=frozenset({"function_definition"}))
_CPP = _CFamily(scope_nodes=frozenset({"function_definition", "lambda_expression"}))


# ---- dispatch ---------------------------------------------------------------

#: Languages this module can walk. A language absent here is *not measured*, and the oracle
#: must say so rather than report zero — see `invention.LanguageInvention.status`.
WALKERS: dict[str, _Lang] = {
    "typescript": _TypeScript(),
    "go": _Go(),
    "csharp": _CSharp(),
    "c": _C,
    "cpp": _CPP,
}

#: Languages excluded on purpose, with the reason, so "no finding" is legible.
NOT_APPLICABLE: dict[str, str] = {
    "java": "variables and methods occupy separate namespaces (JLS 6.5.7) — a local cannot shadow a method",
    "sql": "the CALL/PERFORM fallback matches two keywords; there is no lexical scope to shadow",
    "python": "measured by the stdlib-ast detector in `invention`, not by a tree-sitter walk",
}


def _parser_for(language: str, suffix: str) -> Any:
    """The front-end's own parser factory — never a second grammar (see the module docstring)."""
    if language == "typescript":
        from orchestrator.pkg.typescript_extractor import _ts_parser

        return _ts_parser(suffix)
    if language == "go":
        from orchestrator.pkg.go_extractor import _go_parser

        return _go_parser()
    if language == "csharp":
        from orchestrator.pkg.csharp_extractor import _csharp_parser

        return _csharp_parser()
    if language == "cpp":
        from orchestrator.pkg.cpp_extractor import _cpp_parser

        return _cpp_parser()
    if language == "c":
        from orchestrator.pkg.c_extractor import _c_parser

        return _c_parser()
    raise KeyError(language)


def scopes_for_source(source: bytes, language: str, suffix: str) -> FileScopes:
    """Parse ``source`` and collect its scopes and bare calls. Raises ``KeyError`` if unwalked."""
    walker = WALKERS[language]
    tree = _parser_for(language, suffix).parse(source)
    return collect(tree.root_node, source, walker)


__all__ = [
    "NOT_APPLICABLE",
    "WALKERS",
    "BareCall",
    "Binding",
    "FileScopes",
    "Scope",
    "collect",
    "scopes_for_source",
]
