"""Perl front-end for the PKG extractor (10th language; P1 comprehension, P2 CALLS, P3 routes
+ typed receivers).

Parsing is via tree-sitter (``tree-sitter-perl``), an OPTIONAL dependency — install the
``perl`` extra. The import is lazy so the base install stays stdlib-only.

**D2 is load-bearing.** A Perl ``package`` is both namespace and class — there is no
separate class-declaration syntax, and ``bless`` makes any package a class. So: ``Module``
is always path-keyed (``perl:lib/Shop/Cart.pm`` — a Perl file has no reliable one-module
identity the way a Java/C# file does), and **every** ``package``/5.38 ``class`` declaration
in the file is its own ``Type`` (``perl:Shop.Cart``, dotted from the source ``::`` per D3 —
matches ``import_link._DOTTED_PREFIXES`` join sites, the doc binder, and registry URLs).
Functions in an implicit ``main`` (no ``package`` seen yet) key on the file itself:
``perl:bin/report.pl.usage``.

**D5 — five inheritance spellings, literal-only:** ``use parent``/``use base`` lists,
``our @ISA = (...)``/``push @ISA``, Moo/Moose ``extends``, ``use Mojo::Base 'X'``, and 5.38
``class Foo :isa(X)``. A computed ``@ISA`` (a variable, a call, anything but a string
literal) yields nothing — inheritance in Perl is data, and a guess here would fabricate an
edge no reader could verify. D4 roles (``with 'Role'`` / ``with qw(A B)``) resolve to the
same ``IMPLEMENTS`` edge kind — a mixin is the behavioural claim IMPLEMENTS already
documents — but, unlike the five real spellings above, a role never enters ``_TypeRec.bases``
(found wrong in review: it used to, and ``SUPER::`` — §3.2 row 2 — walks ``bases``, so a
class consuming a role *and* extending a real parent could dispatch ``SUPER::m()`` through
the role; real Perl never does, roles flatten into the consumer at composition time).

**D6 — Field is declared accessors only:** Moo/Moose ``has``, ``Class::Accessor``
``mk_accessors``, 5.38 ``field``. Never inferred from ``$self->{key}`` — a hash access is
not a declaration.

**D7 — require/use:** ``use X qw(...)`` → ``IMPORTS`` to the dotted package placeholder
(upgraded by ``FactBatch`` dedup when the target is grounded elsewhere in the repo); a
lowercase-initial ``use`` target (``strict``, ``warnings``, ``utf8``, ``feature``, …) is a
pragma and skipped. ``require Foo::Bar;`` (bareword) resolves like a ``use``;
``require "lib/x.pl";`` (a literal string) is a path-keyed target, joined by
``import_link.py``'s path-suffix matcher (the C rule) — a computed require argument yields
nothing.

**No ``finalize()`` for D5/D4:** unlike PHP's same-namespace ``extends Bar`` guess (which
needs a deferred repoint because a bare name could resolve either way), every Perl D5/D4
target is already a literal, fully-qualified name — ``_emit_implements`` adds the external
placeholder node eagerly, and ``FactBatch.add_node``'s own dedup upgrades it to grounded if
the target is declared elsewhere in the repo. ``finalize()`` exists only for P2's ``CALLS``
pass (below), which genuinely needs whole-repo knowledge.

**P2 — CALLS (§3.2), precision-first.** One instance of ``PerlExtractor`` is used for every
file in a repo (``RepoCodeExtractor`` creates it once — see ``extractor.py``), so instance
state accumulates across ``extract()`` calls exactly like ``PythonExtractor``'s
``_routes``/``_orm``/``_calls`` — and ``finalize(batch)`` runs once, after every file is
known, which is what makes a same-package call resolvable regardless of file order. Six call
shapes, each resolved only when *verified*, never guessed:

1. ``$self->m()`` / ``$class->m()`` / ``__PACKAGE__->m`` / ``shift->m`` → a sibling sub or
   ``has`` field of the enclosing package.
2. ``$self->SUPER::m()`` → the sub on the first D5-resolved parent (Perl's default MRO:
   depth-first, left to right across ``@ISA``) that actually declares it; if none of
   several parents declare it, skipped rather than guessed; with exactly one resolved
   parent, falls back to the ``Type`` itself (row 3's own backstop — the base is
   verified, a ``Mojo::Base``-style implicit constructor further up its chain is not).
   Skipped outright when the package has no resolved base at all. Known limitation:
   ``use mro 'c3'`` changes the linearisation; this always follows the default DFS order.
3. ``Shop::Log->new`` (a qualified bareword receiver) → the sub when the target package
   declares it, else the ``Type`` itself (instantiation is a call to the type).
4. ``Shop::Util::fmt(...)`` (a qualified function call) → the exact id, a placeholder if
   third-party — no guessing needed, the qualification *is* the verification.
5. bare ``f()`` → same-package sub, else an explicit ``use X qw(... f ...)`` import, else
   (D10) a literal ``@EXPORT`` of a first-party package pulled in by a bare ``use X;`` with
   no list — **first match wins, priority order matters**, and D10's step goes through
   ``finalize_names.resolve_or_drop`` so a candidate that never grounds is dropped, never
   invented as an external placeholder (a method-shaped target has no backstop — see the
   front-end checklist). **Not** ``@ISA``-inherited (amended in P6 review: ``@ISA`` is
   consulted for method dispatch only — ``$self->m()``/``Class->m()`` — never for an
   unqualified ``f()``, which Perl raises "Undefined subroutine" on if it isn't declared or
   imported in the current package; the earlier version of D10 fabricated exactly that edge).
6. Never emitted: ``&f``, ``&Pkg::f()`` (the old calling convention, qualified or not —
   excluded by an explicit leading-``&`` check in ``_handle_function_call``, not by CST
   shape: the grammar gives both a ``function``-typed node like a static name would,
   just with a ``varname`` child holding the name *after* the sigil, which used to let a
   qualified ``&Pkg::f()`` slip past the row-4 qualified-call branch with the literal
   ``&`` embedded in the emitted id — found in review), ``&$code``, ``$self->$m()``,
   ``$obj->can('m')->()``, ``goto &f``, string ``eval``, ``AUTOLOAD``. The rest of these
   produce no ``method``/``function``-typed child the way a static name does, so the
   scanner excludes them by construction — see ``_scan_calls_in``.
7. ``$obj->m()`` where ``$obj`` holds a typed/literal-constructor receiver (``my $log =
   Shop::Log->new; $log->write``) — **P3**'s typed-receiver rule: ``_collect_local_constructor_types``
   builds a file-local ``{varname: type_id}`` map per sub, and the receiver resolves through
   it only when the target type actually declares the method (no ``->new``-style Type
   fallback — an arbitrary method name has no "call the type" meaning). An untyped parameter
   (``$thing`` with no local constructor assignment) stays permanently unresolved.

**P3 — routes (§3.3, ``perl_routes.py``), same precision discipline.** Runs per-file inside
``extract()`` (no whole-repo knowledge needed — a target controller sub is an eager external
placeholder, grounded later by ordinary ``FactBatch`` dedup, like every other cross-file
reference here): Mojolicious full-app route chains (``$r->get('/x')->to(...)``, ``my $api =
$r->under('/api')`` groups) scanned inside every sub body, and Mojolicious::Lite/Dancer2's
shared bareword DSL (``get '/x' => sub {...}`` / ``get '/y' => \\&handler``) scanned at
statement scope. A verb-less/``any`` registration or a computed path emits nothing at all (D2,
endpoints-typescript-go.md); a closure handler emits an ``Endpoint`` with no ``EXPOSES``.

**P4 — data layer (§3.4, ``perl_orm.py``).** Runs in ``finalize()``, like CALLS (a relation
target's local-vs-external classification needs every file's declarations known first).
Marker: ``__PACKAGE__->table('orders')`` — the one call every DBIx::Class Result class must
make, read literally rather than guessed from base class or shape. ``add_columns(...)``
(bareword list or ``name => {...}`` hash — only the keys) become ``Field``s on the
``Entity`` (``perl:entity:Dotted.Path``, a parallel id alongside the package's own ``Type``
node). ``belongs_to``/``has_many``/``might_have``/``has_one`` become ``REFERENCES``, ``src``
the declaring entity; a target outside the repo still gets the edge, to an external
placeholder ``Entity`` — never silently dropped. (``many_to_many`` deliberately excluded —
see ``perl_orm.py``'s own comment: its signature names a linking relation, not a target
class, so the same positional read would be wrong for it.)

**Windows note (not a repo defect):** ``tree_sitter_perl.language()`` returns a bare Python
``int`` (``PyLong_FromVoidPtr``), unlike grammars that return a ``PyCapsule``. tree-sitter's
Windows binding parses a bare int via a 32-bit ``unsigned long`` format code, which
overflows for a real 64-bit pointer — ``OverflowError: Python int too large to convert to
C unsigned long``. Linux (CI's ``ubuntu-latest``, 8-byte ``unsigned long``) is unaffected;
this is a local-Windows-development wrinkle in the upstream binding, not in this extractor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.finalize_names import declared_ids, resolve_or_drop
from orchestrator.pkg.perl_orm import DBIC_RELATIONS
from orchestrator.pkg.perl_orm import emit_entities as _emit_dbic_entities
from orchestrator.pkg.perl_routes import HTTP_VERBS, scan_lite_route, scan_mojo_full_app

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

# `use parent`/`use base`/`use Mojo::Base` are inheritance spellings (D5), never a plain
# IMPORTS edge — the module named here doesn't get *used*, it sets @ISA.
_PARENT_LIKE_USE = frozenset({"parent", "base", "Mojo::Base"})
# Conventional invocant names — the roadmap's own row 1 enumeration, not "any variable".
_INVOCANT_NAMES = frozenset({"self", "class"})


def _to_dotted(name: str) -> str:
    return name.replace("::", ".")


def _strip_sigil(name: str) -> str:
    return name.lstrip("$@%")


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _first_named_of_type(node: TSNode, type_name: str) -> TSNode | None:
    for child in node.named_children:
        if child.type == type_name:
            return child
    return None


def _varname_of(sigil_node: TSNode | None, source: bytes) -> str | None:
    if sigil_node is None:
        return None
    vn = _first_named_of_type(sigil_node, "varname")
    return _text(vn, source) if vn is not None else None


def _string_content_of(node: TSNode, source: bytes) -> str | None:
    sc = _first_named_of_type(node, "string_content")
    return _text(sc, source) if sc is not None else None


def _plain_string_literal_text(node: TSNode, source: bytes) -> str | None:
    """A ``'...'``/``"..."`` literal with no interpolation — ``None`` for anything computed.

    An interpolated scalar (``"lib/$x.pl"``) nests its ``scalar`` node *inside* the
    ``string_content`` node, not as a sibling — so the content node's own ``named_children``
    must be checked too, not just its parent's child count, or `"lib/$x.pl"` reads as the
    literal text `lib/$x.pl` instead of a computed value.
    """
    if node.type not in ("string_literal", "interpolated_string_literal"):
        return None
    if len(node.named_children) != 1 or node.named_children[0].type != "string_content":
        return None
    content = node.named_children[0]
    if content.named_children:
        return None
    return _text(content, source)


def _string_or_wordlist_targets(node: TSNode | None, source: bytes) -> list[str]:
    """Literal name(s) from ``'X'`` / ``qw(A B)`` / ``'X', 'Y'`` — never a computed value.

    Any element that isn't a plain string/word (a variable, a call, a bareword flag like
    ``-norequire``) is simply not a name — it's skipped, not treated as a reason to bail on
    the rest of the list, matching D5/D4's "read only the literal forms" rule.
    """
    if node is None:
        return []
    if node.type == "string_literal":
        text = _plain_string_literal_text(node, source)
        return [text] if text else []
    if node.type == "quoted_word_list":
        content = _string_content_of(node, source)
        return content.split() if content else []
    if node.type in ("list_expression", "parenthesized_expression"):
        out: list[str] = []
        for child in node.named_children:
            out.extend(_string_or_wordlist_targets(child, source))
        return out
    return []


def _has_norequire_flag(node: TSNode | None, source: bytes) -> bool:
    """``use parent -norequire, 'X';`` — the documented flag telling the `parent`/`base`
    pragma not to `require` the named class (it's already loaded another way). The
    inheritance itself is still real; only the module-load event isn't."""
    if node is None:
        return False
    if node.type == "autoquoted_bareword":
        return _text(node, source) == "-norequire"
    if node.type in ("list_expression", "parenthesized_expression"):
        return any(_has_norequire_flag(child, source) for child in node.named_children)
    return False


def _literal_array_assignment_targets(rhs: TSNode | None, source: bytes) -> list[str]:
    """``our @ISA = (...)`` / ``our @EXPORT = (...)``'s RHS — ``None``/anything non-literal
    anywhere means "computed", so the whole assignment yields nothing (D5), unlike the
    mixed-list tolerance above.
    """
    if rhs is None:
        return []
    out: list[str] = []
    stack = [rhs]
    while stack:
        n = stack.pop()
        if n.type == "string_literal":
            text = _plain_string_literal_text(n, source)
            if text is None:
                return []
            out.append(text)
        elif n.type == "quoted_word_list":
            content = _string_content_of(n, source)
            if content is None:
                return []
            out.extend(content.split())
        elif n.type in ("parenthesized_expression", "list_expression"):
            stack.extend(n.named_children)
        else:
            return []  # a variable, a call, anything computed — the whole thing is unusable
    return out


def _literal_push_targets(args: TSNode | None, array_name: str, source: bytes) -> list[str] | None:
    """``push @ISA, 'X', 'Y'`` / ``push @EXPORT, 'f'`` — ``None`` when this isn't that shape
    or an element is computed (D5-style all-or-nothing); ``[]`` is a valid "no targets".
    """
    if args is None or args.type != "list_expression":
        return None
    children = args.named_children
    if not children or children[0].type != "array" or _varname_of(children[0], source) != array_name:
        return None
    targets: list[str] = []
    for c in children[1:]:
        text = _plain_string_literal_text(c, source) if c.type == "string_literal" else None
        if text is None:
            return None  # a computed element anywhere means the whole push is unusable
        targets.append(text)
    return targets


def _has_field_names(args: TSNode | None, source: bytes) -> list[str]:
    """Field name(s) from ``has NAME => (...)`` / ``has [qw(a b)]`` / ``has qw(a b)``."""
    if args is None:
        return []
    if args.type == "list_expression":
        first = args.named_children[0] if args.named_children else None
        if first is not None and first.type == "autoquoted_bareword":
            return [_text(first, source)]
        return []
    if args.type == "anonymous_array_expression":
        inner = _first_named_of_type(args, "quoted_word_list")
        return _string_or_wordlist_targets(inner, source) if inner is not None else []
    if args.type in ("quoted_word_list", "autoquoted_bareword"):
        return _string_or_wordlist_targets(args, source) or (
            [_text(args, source)] if args.type == "autoquoted_bareword" else []
        )
    return []


@dataclass
class _Ctx:
    """The package in scope, plus this *file*'s bare-call resolution inputs (P2). Mutated in
    place for statement-form ``package X;`` so the rest of the same sibling loop sees the new
    owner; block-form gets its own fresh ``_Ctx``.

    ``use X qw(f)`` is a real Perl bug found in review: it runs ``Module->import(...)`` at
    compile time, and ``Exporter``-style ``import`` installs symbols into whichever package
    ``caller()`` names *at that line* — package-scoped, not file-scoped. ``package Foo;`` then
    ``use Bar qw(baz);`` then ``package Qux;`` does **not** give ``Qux`` a ``baz()``. So
    ``use_func_map``/``bare_use_targets`` are keyed by package in ``file_use_maps``/
    ``file_bare_targets`` (shared file-wide, by reference, so a *reopened* package still sees
    what it imported earlier in the same file) and looked up by ``self.package_id`` through
    the two properties below — never copied verbatim into a child or sibling ``_Ctx``.
    """

    package_id: str | None
    package_name: str | None
    file_use_maps: dict[str | None, dict[str, str]]
    file_bare_targets: dict[str | None, list[str]]

    @property
    def use_func_map(self) -> dict[str, str]:
        return self.file_use_maps.setdefault(self.package_id, {})

    @property
    def bare_use_targets(self) -> list[str]:
        return self.file_bare_targets.setdefault(self.package_id, [])


@dataclass
class _TypeRec:
    """Everything P2's CALLS pass and P4's ORM pass need about one package, built during
    the P1 walk and read back in ``finalize()`` once every file's walk is done.
    """

    type_id: str
    methods: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    bases: list[str] = field(default_factory=list)
    exports: set[str] = field(default_factory=set)
    # P4 (§3.4): set when this package calls `__PACKAGE__->table(...)` — DBIx::Class's own
    # required marker for a Result class, read literally rather than guessed from base class.
    is_dbic_entity: bool = False
    dbic_table_line: int | None = None
    dbic_rel: str = ""
    dbic_columns: list[tuple[str, int]] = field(default_factory=list)
    dbic_relations: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class _SubRec:
    """One sub/method body, queued in P1's walk and scanned for CALLS in ``finalize()`` —
    the two-pass split PHP/C# use, at repo scope instead of file scope, since a call's
    target may live in a file that hasn't been walked yet.
    """

    caller_id: str
    body: TSNode
    owner_type_id: str | None  # None => implicit main / free sub, no $self/SUPER shapes
    use_func_map: dict[str, str]
    bare_use_targets: list[str]
    rel: str


class PerlExtractor:
    """Perl front-end (tree-sitter). Install the ``perl`` extra to use it."""

    language: str = "perl"
    suffixes: tuple[str, ...] = (".pl", ".pm", ".t")

    def __init__(self) -> None:
        # Accumulate across every file in the repo (see the module docstring's P2
        # section) — but the extractor instance itself outlives one repo:
        # `RepoCodeExtractor.__init__` builds it once and a caller looping over several
        # repos with one `RepoCodeExtractor` (`pkg extract --repos`, multi-repo
        # `investigate`) reuses it across every `.extract()` call. `finalize()` resets
        # both to empty once it is done with them (Go's `finalize` does the same for its
        # own accumulators), or repo A's types/subs leak into repo B's graph.
        self._types: dict[str, _TypeRec] = {}
        self._subs: list[_SubRec] = []

    def module_name(self, path: Path, root: Path) -> str:
        # D2: always path-keyed — a Perl file has no single reliable namespace of its own
        # the way a Java/C# file does (a file may hold zero, one, or several packages).
        return rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _perl_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"perl:{module or rel}"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "perl", Provenance(rel, 1)))
        ctx = _Ctx(package_id=None, package_name=None, file_use_maps={}, file_bare_targets={})
        self._walk_siblings(tree.root_node.named_children, module_id, ctx, source, rel, batch)
        return batch

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Every file is walked by now: P2's CALLS pass over every sub body, then P4's
        DBIx::Class entities (whole-repo, so a relation target resolves the same way
        regardless of which file declares it or which file references it first).

        Takes local references and resets ``self._types``/``self._subs`` before
        returning — this instance is reused across repos in a multi-repo run (see
        ``__init__``), and a resolution pass reading last repo's declarations while
        grounding this one's calls is exactly the invention this whole track exists to
        prevent.

        ``declared_ids(batch)`` is computed once here and threaded through every sub's
        scan, rather than ``resolve_or_drop`` rebuilding it per bare-call resolution
        attempt — found in review: that was genuinely quadratic in repo size (every
        candidate check rescanned the whole, only-growing node list). No grounded node is
        added anywhere in this pass (only external placeholders are, for an unresolved
        qualified/`SUPER::` target), so the declared set is stable for the whole pass —
        computing it once is correct, not just faster.
        """
        types, subs = self._types, self._subs
        self._types, self._subs = {}, []
        declared = declared_ids(batch)
        for sub in subs:
            self._scan_calls_in(sub, batch, types, declared)
        _emit_dbic_entities(types, batch)
        return batch

    # --- top-level / block-body dispatch --------------------------------------

    def _walk_siblings(
        self,
        nodes: list[TSNode],
        module_id: str,
        ctx: _Ctx,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        for node in nodes:
            t = node.type
            if t in ("package_statement", "class_statement"):
                self._handle_package(
                    node, module_id, ctx, source, rel, batch, is_class=t == "class_statement"
                )
            elif t == "use_statement":
                self._handle_use(node, module_id, ctx, source, rel, batch)
            elif t == "require_expression":
                self._handle_require(node, module_id, source, rel, batch)
            elif t == "expression_statement":
                for child in node.named_children:
                    self._handle_statement_expr(child, module_id, ctx, source, rel, batch)
            elif t in ("subroutine_declaration_statement", "method_declaration_statement"):
                self._handle_sub(node, module_id, ctx, source, rel, batch)
            # Control flow / other statements are out of scope for this front-end.

    def _handle_statement_expr(
        self, expr: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        t = expr.type
        if t == "require_expression":
            self._handle_require(expr, module_id, source, rel, batch)
        elif t == "ambiguous_function_call_expression":
            self._handle_bareword_call(expr, ctx, source, rel, batch)
        elif t == "assignment_expression":
            self._handle_assignment(expr, ctx, source, rel, batch)
        elif t == "variable_declaration":
            self._maybe_field_decl(expr, ctx, source, rel, batch)
        elif t == "method_call_expression":
            self._handle_class_accessor_call(expr, ctx, source, rel, batch)

    # --- package / class -------------------------------------------------------

    def _handle_package(
        self,
        node: TSNode,
        module_id: str,
        ctx: _Ctx,
        source: bytes,
        rel: str,
        batch: FactBatch,
        *,
        is_class: bool,
    ) -> None:
        name_node = _first_named_of_type(node, "package")
        if name_node is None:
            return
        name = _text(name_node, source)
        type_id = f"perl:{_to_dotted(name)}"
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        batch.add_node(Node(type_id, NodeKind.TYPE, name, "perl", Provenance(rel, line, end_line)))
        batch.add_edge(Edge(module_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))
        rec = self._types.setdefault(type_id, _TypeRec(type_id=type_id))

        if is_class:
            self._handle_class_isa_attribute(node, type_id, rec, source, rel, line, batch)

        block = _first_named_of_type(node, "block")
        if block is not None:
            child_ctx = _Ctx(
                package_id=type_id,
                package_name=name,
                file_use_maps=ctx.file_use_maps,
                file_bare_targets=ctx.file_bare_targets,
            )
            self._walk_siblings(block.named_children, module_id, child_ctx, source, rel, batch)
        else:
            # Statement form: scope runs to the next package/class statement or EOF — mutate
            # the shared ctx so the rest of THIS sibling loop sees the new owner.
            ctx.package_id = type_id
            ctx.package_name = name

    def _handle_class_isa_attribute(
        self,
        node: TSNode,
        type_id: str,
        rec: _TypeRec,
        source: bytes,
        rel: str,
        line: int,
        batch: FactBatch,
    ) -> None:
        """5.38 ``class Foo :isa(Bar)`` — D5's fifth spelling."""
        attrlist = _first_named_of_type(node, "attrlist")
        if attrlist is None:
            return
        for attr in attrlist.named_children:
            if attr.type != "attribute":
                continue
            aname = _first_named_of_type(attr, "attribute_name")
            aval = _first_named_of_type(attr, "attribute_value")
            if aname is not None and aval is not None and _text(aname, source) == "isa":
                self._emit_implements(type_id, rec, _text(aval, source), rel, line, batch)

    # --- use / require -----------------------------------------------------

    def _handle_use(
        self, node: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        target_node = _first_named_of_type(node, "package")
        if target_node is None:
            return
        target = _text(target_node, source)
        line = node.start_point[0] + 1
        args = node.named_children[1] if len(node.named_children) > 1 else None

        if target in _PARENT_LIKE_USE:
            if ctx.package_id is None:
                return
            rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
            # `use parent`/`use base`/`use Mojo::Base` genuinely load the named base class
            # as a module dependency — Perl's `parent`/`base` pragma `require`s it — so
            # this file IMPORTS it too, the same fact a plain `use Shop::Base;` would
            # record; only IMPLEMENTS (the inheritance itself) was emitted before (found in
            # review). Except `-norequire` (`use parent -norequire, 'Shop::Base';`), the
            # documented flag that tells the pragma *not* to load it — the inheritance is
            # still real (IMPLEMENTS stays unconditional), but there is no load event to
            # report; emitting IMPORTS there would be the invented fact this codebase's
            # whole precision discipline forbids, not the gap the review actually asked to
            # close. The pragma module itself (`parent`/`base`/`Mojo::Base`) stays
            # unrecorded either way — Perl's own inheritance machinery, not a dependency a
            # graph consumer would act on.
            norequire = _has_norequire_flag(args, source)
            for base in _string_or_wordlist_targets(args, source):
                self._emit_implements(ctx.package_id, rec, base, rel, line, batch)
                if not norequire:
                    base_tid = f"perl:{_to_dotted(base)}"
                    batch.add_edge(Edge(module_id, base_tid, EdgeKind.IMPORTS, Provenance(rel, line)))
            return
        if target[:1].islower():
            return  # a pragma (strict, warnings, utf8, feature, lib, constant, …)

        # D2: a `use` target names a PACKAGE, and package == Type (not Module) — the
        # placeholder must share the dotted Type-id shape a real `package Foo::Bar`
        # declaration would produce, or the two would never dedup onto one grounded node.
        tid = f"perl:{_to_dotted(target)}"
        batch.add_node(Node(tid, NodeKind.TYPE, target, "perl", external=True))
        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))

        # P2 row 5: an explicit `use X qw(f g)` names function imports directly (verified,
        # never a guess — the source itself asserts the import). A bare `use X;` (no args at
        # all) is D10's default-@EXPORT candidate instead; anything else (a single non-list
        # arg, e.g. a version number) is neither and is left alone.
        if args is None:
            ctx.bare_use_targets.append(tid)
        elif args.type == "quoted_word_list":
            for fname in _string_or_wordlist_targets(args, source):
                ctx.use_func_map[fname] = f"{tid}.{fname}"

    def _handle_require(
        self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        line = node.start_point[0] + 1
        arg = node.named_children[0] if node.named_children else None
        if arg is None:
            return
        if arg.type == "bareword":
            # A bareword require names a PACKAGE, same as `use` (D2: package == Type).
            target = _text(arg, source)
            tid = f"perl:{_to_dotted(target)}"
            batch.add_node(Node(tid, NodeKind.TYPE, target, "perl", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))
            return
        literal = _plain_string_literal_text(arg, source)
        if literal is None:
            return  # a computed require target — never a guess (D7)
        tid = f"perl:{literal}"
        batch.add_node(Node(tid, NodeKind.MODULE, literal, "perl", external=True))
        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))

    # --- inheritance / roles / fields via bareword calls ------------------

    def _handle_bareword_call(
        self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        fn_node = _first_named_of_type(expr, "function")
        fn_text = _text(fn_node, source) if fn_node is not None else ""
        args = expr.named_children[1] if len(expr.named_children) > 1 else None
        line = expr.start_point[0] + 1

        if fn_text == "push":
            self._handle_push_isa_or_export(args, ctx, rel, line, source, batch)
        elif fn_text in ("extends", "with"):
            if ctx.package_id is None:
                return
            rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
            for target in _string_or_wordlist_targets(args, source):
                self._emit_implements(
                    ctx.package_id, rec, target, rel, line, batch, is_base=fn_text == "extends"
                )
        elif fn_text == "has":
            if ctx.package_id is None:
                return
            for name in _has_field_names(args, source):
                self._emit_field(ctx.package_id, name, rel, line, batch)
        elif fn_text in HTTP_VERBS:
            # Mojolicious::Lite / Dancer2 (P3, §3.3): `get '/x' => sub {...}` at statement
            # scope, sharing one bareword DSL.
            owner_id = ctx.package_id or f"perl:{rel}"
            scan_lite_route(expr, owner_id, source, rel, batch)

    def _handle_push_isa_or_export(
        self, args: TSNode | None, ctx: _Ctx, rel: str, line: int, source: bytes, batch: FactBatch
    ) -> None:
        if ctx.package_id is None:
            return
        isa_targets = _literal_push_targets(args, "ISA", source)
        if isa_targets is not None:
            rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
            for t in isa_targets:
                self._emit_implements(ctx.package_id, rec, t, rel, line, batch)
            return
        export_targets = _literal_push_targets(args, "EXPORT", source)
        if export_targets is not None:
            rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
            rec.exports.update(export_targets)

    def _handle_assignment(self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch) -> None:
        children = expr.named_children
        lhs = children[0] if children else None
        if lhs is None or lhs.type != "variable_declaration":
            return
        keyword = _text(lhs.children[0], source) if lhs.children else ""
        if keyword == "field":
            self._maybe_field_decl(lhs, ctx, source, rel, batch)
            return
        if keyword != "our" or ctx.package_id is None:
            return
        arr = _first_named_of_type(lhs, "array")
        arr_name = _varname_of(arr, source) if arr is not None else None
        if arr_name not in ("ISA", "EXPORT"):
            return
        rhs = children[1] if len(children) > 1 else None
        line = expr.start_point[0] + 1
        targets = _literal_array_assignment_targets(rhs, source)
        rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
        if arr_name == "ISA":
            for target in targets:
                self._emit_implements(ctx.package_id, rec, target, rel, line, batch)
        else:
            rec.exports.update(targets)

    def _maybe_field_decl(self, decl: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch) -> None:
        """5.38 ``field $x :param;`` / ``field $y :param = 0;`` (D6)."""
        if ctx.package_id is None or not decl.children or _text(decl.children[0], source) != "field":
            return
        scalar = _first_named_of_type(decl, "scalar")
        varname = _varname_of(scalar, source)
        if varname:
            self._emit_field(ctx.package_id, varname, rel, decl.start_point[0] + 1, batch)

    def _handle_class_accessor_call(
        self, expr: TSNode, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        """``__PACKAGE__->method(...)`` statement calls: ``mk_accessors`` (D6,
        Class::Accessor) and DBIx::Class's ``table``/``add_columns``/relation declarators
        (P4, §3.4) — same receiver shape, different method names.
        """
        if ctx.package_id is None:
            return
        children = expr.named_children
        if len(children) < 2:
            return
        receiver, method_node = children[0], _first_named_of_type(expr, "method")
        if receiver.type != "func0op_call_expression" or _text(receiver, source) != "__PACKAGE__":
            return
        if method_node is None:
            return
        method = _text(method_node, source)
        line = expr.start_point[0] + 1
        args = children[2:]

        if method == "mk_accessors":
            for arg in args:
                for name in _string_or_wordlist_targets(arg, source):
                    self._emit_field(ctx.package_id, name, rel, line, batch)
            return

        rec = self._types.setdefault(ctx.package_id, _TypeRec(type_id=ctx.package_id))
        if method == "table":
            rec.is_dbic_entity = True
            rec.dbic_table_line = line
            rec.dbic_rel = rel
        elif method == "add_columns":
            for arg in args:
                if arg.type == "list_expression":
                    items = arg.named_children
                    is_hash_form = any(c.type == "anonymous_hash_expression" for c in items)
                    for i, item in enumerate(items):
                        if is_hash_form:
                            # `add_columns(id => {...}, name => {...})` — only the keys
                            # (each an `autoquoted_bareword`) are read, never the
                            # type-info hash values (D6-style: declared, not guessed).
                            if i % 2 == 0 and item.type == "autoquoted_bareword":
                                rec.dbic_columns.append((_text(item, source), line))
                        elif item.type == "autoquoted_bareword":
                            rec.dbic_columns.append((_text(item, source), line))
                        else:
                            for name in _string_or_wordlist_targets(item, source):
                                rec.dbic_columns.append((name, line))
                else:
                    for name in _string_or_wordlist_targets(arg, source):
                        rec.dbic_columns.append((name, line))
        elif method in DBIC_RELATIONS and args:
            # DBIx::Class's own positional signature: `->belongs_to($rel_name,
            # $related_class, $cond, \%attrs)`. The target class is always position 1 —
            # read positionally, not as "the first string literal in the list": the
            # relation name may *also* be a quoted string (`belongs_to('customer', 'App
            # ::Schema::Result::Customer', ...)`, valid DBIx::Class, not just the bareword
            # form `customer => ...`), and taking the first string literal encountered
            # would then pick the *name*, not the target — a real bug found in review.
            target_arg = args[0]
            if target_arg.type == "list_expression":
                items = target_arg.named_children
                if len(items) >= 2 and items[1].type == "string_literal":
                    target = _plain_string_literal_text(items[1], source)
                    if target:
                        rec.dbic_relations.append((target, line))

    # --- subs ----------------------------------------------------------------

    def _handle_sub(
        self, node: TSNode, module_id: str, ctx: _Ctx, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        name_node = _first_named_of_type(node, "bareword")
        if name_node is None:
            return
        raw_name = _text(name_node, source)
        line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        if "::" in raw_name:
            # `sub Shop::Elsewhere::baz {}` / `sub main::top {}` — a fully-qualified sub
            # name declares straight into *that* package, regardless of which package is
            # lexically current where the `sub` statement sits. Real, if uncommon, Perl
            # (legacy code reopening a package without a `package` block) — found wrong in
            # review: the old code kept the lexically-current package as owner and left
            # the literal `::` inside the function name, producing a wrong `CONTAINS` edge
            # and a dotted id with `::` embedded in it (D3 violated either way).
            pkg_part, _, name = raw_name.rpartition("::")
            owner_id = f"perl:{_to_dotted(pkg_part)}"
            self._types.setdefault(owner_id, _TypeRec(type_id=owner_id))
            batch.add_node(Node(owner_id, NodeKind.TYPE, pkg_part, "perl"))
            batch.add_edge(Edge(module_id, owner_id, EdgeKind.CONTAINS, Provenance(rel, line)))
            sub_owner_type_id: str | None = owner_id
        else:
            name = raw_name
            owner_id = ctx.package_id or module_id
            sub_owner_type_id = ctx.package_id

        fid = f"{owner_id}.{name}"
        batch.add_node(Node(fid, NodeKind.FUNCTION, name, "perl", Provenance(rel, line, end_line)))
        batch.add_edge(Edge(owner_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        if sub_owner_type_id is not None:
            rec = self._types.setdefault(sub_owner_type_id, _TypeRec(type_id=sub_owner_type_id))
            rec.methods[name] = fid

        body = _first_named_of_type(node, "block")
        if body is not None:
            self._subs.append(
                _SubRec(
                    caller_id=fid,
                    body=body,
                    owner_type_id=sub_owner_type_id,
                    use_func_map=ctx.use_func_map,
                    bare_use_targets=ctx.bare_use_targets,
                    rel=rel,
                )
            )
            # P3 (§3.3): a Mojolicious full-app route chain can appear in any sub — a route
            # plugin/helper method (`MyApp::Routes::install`) is a real pattern, not just
            # `startup` — so the scan itself runs broadly. But the *controller-target*
            # `EXPOSES` resolution (`->to('orders#index')` -> `App.Controller.Orders.index`)
            # is gated on ``name`` being literally ``startup`` (see ``scan_mojo_full_app``):
            # Mojolicious's own required, unambiguous entry-point name for the real app
            # class. Resolving that namespace from *whichever* sub happens to call ->to()
            # guessed the app's own name wrong for exactly the helper-method case — found
            # in review — since a plugin package's own name isn't the app's namespace.
            app_package = sub_owner_type_id[len("perl:") :] if sub_owner_type_id is not None else None
            scan_mojo_full_app(body, app_package, name, source, rel, batch)

    # --- P2: CALLS -------------------------------------------------------------

    def _scan_calls_in(
        self,
        sub: _SubRec,
        batch: FactBatch,
        types: dict[str, _TypeRec],
        declared: frozenset[str],
    ) -> None:
        owner = types.get(sub.owner_type_id) if sub.owner_type_id else None
        # P3, §3.2 row 7: a file-local typed-receiver map (`my $log = Shop::Log->new`),
        # collected once up front so the shape scan below doesn't care about source order.
        local_types = _collect_local_constructor_types(sub.body)
        stack = list(sub.body.named_children)
        while stack:
            n = stack.pop()
            if n.type == "method_call_expression":
                self._handle_method_call(n, sub, owner, local_types, batch, types)
            elif n.type == "function_call_expression":
                self._handle_function_call(n, sub, owner, batch, types, declared)
            # Descend regardless — a call can nest inside another call's arguments, and
            # method_call_expression's own children include the receiver/args to re-walk.
            stack.extend(n.named_children)

    def _handle_method_call(
        self,
        expr: TSNode,
        sub: _SubRec,
        owner: _TypeRec | None,
        local_types: dict[str, str],
        batch: FactBatch,
        types: dict[str, _TypeRec],
    ) -> None:
        children = expr.named_children
        if not children:
            return
        receiver = children[0]
        method_node = _first_named_of_type(expr, "method")
        if method_node is None:
            return  # a dynamic method (`$self->$m()`) has no `method`-typed child — excluded
        line = expr.start_point[0] + 1
        mtext = _bytes_text(method_node)

        if mtext.startswith("SUPER::"):
            if owner is None or not owner.bases:
                return  # row 2: skip when unresolved
            method = mtext[len("SUPER::") :]
            prov = Provenance(sub.rel, line)
            # Perl's default MRO is depth-first, left to right across @ISA: the first
            # parent that *declares* the method wins, not simply the first parent (found
            # in review: `use parent qw(A B)` + `SUPER::bmeth` resolved to A even though
            # only B declares `bmeth`). `use mro 'c3'` changes the linearisation; this
            # follows the default DFS order only.
            for base_id in owner.bases:
                base_rec = types.get(base_id)
                if base_rec is not None and method in base_rec.methods:
                    batch.add_edge(Edge(sub.caller_id, base_rec.methods[method], EdgeKind.CALLS, prov))
                    return
            if len(owner.bases) > 1:
                # Two or more parents and none of them declares it: the method is
                # inherited further up one of the chains, and which chain is not
                # readable from this repo. Skip rather than name a parent the call may
                # never reach — the row-3 Type backstop below is only honest when there
                # is exactly one candidate it could mean.
                return
            # Row 3's own backstop, not a fabricated `<base>.<method>` id: the base
            # package doesn't declare `method` itself — inherited further up its own
            # chain (a `Mojo::Base`-style implicit constructor is the common real case)
            # or the base is genuinely third-party. Either way the base *is* verified
            # (D5 resolved it literally), so the call is to the type, not to a guessed
            # method that has no backstop.
            base_id = owner.bases[0]
            base_rec = types.get(base_id)
            base_name = base_id.rsplit(".", 1)[-1]
            batch.add_node(Node(base_id, NodeKind.TYPE, base_name, "perl", external=base_rec is None))
            batch.add_edge(Edge(sub.caller_id, base_id, EdgeKind.CALLS, prov))
            return

        if "::" in mtext:
            return  # an unexpected qualified method shape — never seen in practice, skip

        receiver_varname = _bytes_text(_first_named_of_type(receiver, "varname") or receiver)
        if receiver.type == "scalar" and receiver_varname in _INVOCANT_NAMES:
            self._resolve_sibling_or_field(sub, owner, mtext, line, batch)
            return
        if receiver.type == "func0op_call_expression" and _bytes_text(receiver) == "__PACKAGE__":
            self._resolve_sibling_or_field(sub, owner, mtext, line, batch)
            return
        if receiver.type == "func1op_call_expression" and _bytes_text(receiver) == "shift":
            self._resolve_sibling_or_field(sub, owner, mtext, line, batch)
            return
        receiver_text = _bytes_text(receiver)
        if receiver.type == "bareword" and "::" in receiver_text:
            # Row 3: `Shop::Log->new` — a qualified bareword receiver.
            target_type_id = f"perl:{_to_dotted(receiver_text)}"
            target_rec = types.get(target_type_id)
            prov = Provenance(sub.rel, line)
            if target_rec is not None and mtext in target_rec.methods:
                batch.add_edge(Edge(sub.caller_id, target_rec.methods[mtext], EdgeKind.CALLS, prov))
            else:
                batch.add_node(Node(target_type_id, NodeKind.TYPE, receiver_text, "perl", external=True))
                batch.add_edge(Edge(sub.caller_id, target_type_id, EdgeKind.CALLS, prov))
            return
        # Row 7 (P3): a receiver holding a literal same-sub constructor
        # (`my $log = Shop::Log->new; $log->write`) resolves through the assignment — but
        # only when the target type actually declares the method; unlike row 3's `->new`
        # fallback, there is no "call the type" backstop for an arbitrary method name.
        if receiver.type == "scalar":
            type_id = local_types.get(receiver_varname)
            if type_id is not None:
                target_rec = types.get(type_id)
                if target_rec is not None and mtext in target_rec.methods:
                    prov = Provenance(sub.rel, line)
                    batch.add_edge(Edge(sub.caller_id, target_rec.methods[mtext], EdgeKind.CALLS, prov))
        # Any other receiver shape (an untyped parameter, a chained call, …) is permanently
        # unresolvable — never guessed here.

    def _resolve_sibling_or_field(
        self, sub: _SubRec, owner: _TypeRec | None, name: str, line: int, batch: FactBatch
    ) -> None:
        if owner is None:
            return
        target = owner.methods.get(name) or owner.fields.get(name)
        if target is not None:
            batch.add_edge(Edge(sub.caller_id, target, EdgeKind.CALLS, Provenance(sub.rel, line)))

    def _handle_function_call(
        self,
        expr: TSNode,
        sub: _SubRec,
        owner: _TypeRec | None,
        batch: FactBatch,
        types: dict[str, _TypeRec],
        declared: frozenset[str],
    ) -> None:
        fn_node = _first_named_of_type(expr, "function")
        if fn_node is None:
            return
        fn_text = _bytes_text(fn_node)
        if fn_text.startswith("&"):
            # Row 6: `&f`/`&f()` is never emitted — the old calling convention, excluded as
            # a class regardless of qualification. `&Shop::Util::fmt()` parses with the same
            # `&`-prefixed `function` node as bare `&f`, and used to fall through into row
            # 4's qualified-call branch anyway (the "::" check doesn't see the sigil), which
            # embedded the literal `&` into the emitted id (`perl:&Shop.Util.fmt`) instead of
            # being excluded like every other ampersand-form call — found in review.
            return
        line = expr.start_point[0] + 1

        if "::" in fn_text:
            # Row 4: `Shop::Util::fmt(...)` — the qualification is the verification.
            pkg, _, func = fn_text.rpartition("::")
            target = f"perl:{_to_dotted(pkg)}.{func}"
            batch.add_node(Node(target, NodeKind.FUNCTION, func, "perl", external=True))
            batch.add_edge(Edge(sub.caller_id, target, EdgeKind.CALLS, Provenance(sub.rel, line)))
            return

        # Row 5: bare f() — same-package sub, else explicit `use X qw(f)`, else D10.
        if owner is not None and fn_text in owner.methods:
            batch.add_edge(
                Edge(sub.caller_id, owner.methods[fn_text], EdgeKind.CALLS, Provenance(sub.rel, line))
            )
            return
        if fn_text in sub.use_func_map:
            target = sub.use_func_map[fn_text]
            batch.add_node(Node(target, NodeKind.FUNCTION, fn_text, "perl", external=True))
            batch.add_edge(Edge(sub.caller_id, target, EdgeKind.CALLS, Provenance(sub.rel, line)))
            return

        # D10: a literal `@EXPORT` of a first-party package pulled in by a bare `use X;`
        # with no list. Ambiguous (2+ candidates) is never guessed between. `@EXPORT` is
        # only ever read by `Exporter`'s own `import()` — a package that sets `@EXPORT`
        # but never inherits `Exporter` (`our @ISA = qw(Exporter);`, `use parent
        # 'Exporter'`, ...) has no `import()` reading it at all, `use X;` silently does
        # nothing, and the "exported" sub is never actually reachable unqualified. Found
        # in review: the corpus's own fixture had exactly this gap (`@EXPORT` set, no
        # `Exporter` base), so the labelled edge wasn't true of the code either.
        export_candidates = [
            f"{tid}.{fn_text}"
            for tid in sub.bare_use_targets
            if fn_text in types.get(tid, _TypeRec(type_id=tid)).exports
            and fn_text in types.get(tid, _TypeRec(type_id=tid)).methods
            and "perl:Exporter" in types.get(tid, _TypeRec(type_id=tid)).bases
        ]
        if len(export_candidates) == 1:
            resolve_or_drop(
                batch,
                sub.caller_id,
                export_candidates,
                EdgeKind.CALLS,
                Provenance(sub.rel, line),
                declared=declared,
            )
        # Otherwise: skip. Never a guess. (A bare `f()` does NOT search `@ISA` — that's
        # method-dispatch-only in real Perl, found wrong in review: `@ISA` is consulted
        # for `$self->m()`/`Class->m()`, never for an unqualified `f()`, which either
        # names a sub in the current package/an explicit import, or Perl raises
        # "Undefined subroutine" at runtime. D10's own second sub-step fabricated exactly
        # that edge; removed rather than kept as a documented gap, since nothing about it
        # was ever true of the language.)

    # --- shared emitters -------------------------------------------------------

    @staticmethod
    def _emit_implements(
        owner_id: str,
        rec: _TypeRec,
        target_name: str,
        rel: str,
        line: int,
        batch: FactBatch,
        *,
        is_base: bool = True,
    ) -> None:
        """``is_base=False`` is a consumed role (D4, ``with 'Role'``) — real Perl never
        dispatches ``SUPER::`` through a role (roles flatten their methods into the
        consuming class at composition time; ``SUPER::``/method resolution only ever
        walks the real ``@ISA`` chain), so a role must not land in ``rec.bases`` the way
        real inheritance does — found wrong in review: `with` and `extends` both fed the
        same list, so a class consuming a role *and* extending a real parent could have
        ``SUPER::m()`` resolve through the role instead of (or ahead of) the real parent.
        """
        if not target_name:
            return
        tid = f"perl:{_to_dotted(target_name)}"
        batch.add_node(Node(tid, NodeKind.TYPE, target_name, "perl", external=True))
        batch.add_edge(Edge(owner_id, tid, EdgeKind.IMPLEMENTS, Provenance(rel, line)))
        if is_base:
            rec.bases.append(tid)

    def _emit_field(self, owner_id: str, name: str, rel: str, line: int, batch: FactBatch) -> None:
        clean = _strip_sigil(name)
        if not clean:
            return
        fid = f"{owner_id}.{clean}"
        batch.add_node(Node(fid, NodeKind.FIELD, clean, "perl", Provenance(rel, line)))
        batch.add_edge(Edge(owner_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        rec = self._types.setdefault(owner_id, _TypeRec(type_id=owner_id))
        rec.fields[clean] = fid


def _bytes_text(node: TSNode | None) -> str:
    """Like ``_text``, but for the P2 scan where the source bytes aren't threaded through —
    tree-sitter nodes carry their own ``.text`` once parsed, so no separate ``source`` arg
    is needed here.
    """
    if node is None:
        return ""
    return node.text.decode("utf-8", "replace").strip() if node.text is not None else ""


def _collect_local_constructor_types(body: TSNode) -> dict[str, str]:
    """P3, §3.2 row 7: ``my $log = Shop::Log->new;`` anywhere in this sub body — a
    ``{varname: type_id}`` map, order-independent (Perl reassignment mid-function isn't
    tracked; "file-local" per the roadmap, not full control-flow analysis).
    """
    out: dict[str, str] = {}
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type == "assignment_expression":
            children = n.named_children
            if len(children) >= 2:
                lhs, rhs = children[0], children[1]
                if lhs.type == "variable_declaration" and rhs.type == "method_call_expression":
                    scalar = _first_named_of_type(lhs, "scalar")
                    varname_node = _first_named_of_type(scalar, "varname") if scalar is not None else None
                    receiver = rhs.named_children[0] if rhs.named_children else None
                    method_node = _first_named_of_type(rhs, "method")
                    if (
                        varname_node is not None
                        and receiver is not None
                        and receiver.type == "bareword"
                        and "::" in _bytes_text(receiver)
                        and method_node is not None
                        and _bytes_text(method_node) == "new"
                    ):
                        out[_bytes_text(varname_node)] = f"perl:{_to_dotted(_bytes_text(receiver))}"
        stack.extend(n.named_children)
    return out


def _perl_language(raw: Any) -> Any:
    from tree_sitter import Language

    try:
        return Language(raw)
    except OverflowError:
        # Windows only (see the module docstring): tree_sitter_perl.language() returns a
        # bare pointer-sized int rather than a PyCapsule, and tree-sitter's Windows binding
        # parses a bare int via a 32-bit `unsigned long` format code — a real 64-bit pointer
        # overflows it. Wrap it in a capsule ourselves rather than make Perl extraction
        # unusable for every Windows-based contributor over a third-party binding quirk.
        import ctypes

        pythonapi = ctypes.pythonapi
        pythonapi.PyCapsule_New.restype = ctypes.py_object
        pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        capsule = pythonapi.PyCapsule_New(ctypes.c_void_p(raw), b"tree_sitter.Language", None)
        return Language(capsule)


def _perl_parser() -> Any:
    try:
        import tree_sitter_perl
        from tree_sitter import Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Perl extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-perl>=2.0.0'"
        ) from exc
    language = _perl_language(tree_sitter_perl.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["PerlExtractor"]
