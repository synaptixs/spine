"""Typed-receiver calls for the Java and C# front-ends: ``recv.m()`` lands on the method its
declared type means (B21).

Both front-ends used to drop every call through a variable: Java resolved only a capitalized
receiver (a static call), and C# only a sibling call inside one type. Measured before this
existed, on real code: 14,984 such calls in mysql-connector-j (Spine emitted 334 of them, by
coincidence of line), and 2,427 in a .NET service (0 emitted — 99% of them ``_service.Do()``
through an interface-typed DI field). ``blast_radius`` answered "0 callers" for most methods.

**A call is judged once every declaration in the repository is known**, never per file:
whether a type name means an in-repo type, which type, and whether that type declares the
member are all questions one file cannot answer. So a front-end records a
:class:`DeferredCall` per receiver call while it walks, and :func:`resolve_calls` settles them
in the front-end's ``finalize``. What a front-end supplies is language-specific and stays in it:
the receiver's declared type as a :class:`TypeRef` — candidate type ids **grouped by that
language's lookup precedence** (enclosing types, then the namespace or package, then imports).

The rules, precision-first — a wrong edge is worse than a missing one:

- **The type must be declared in this repository.** A receiver typed ``ILogger<T>`` gets no edge
  (D11): an edge to an invented external member is how the Kotlin front-end once fabricated calls.
- **The first precedence group with a declared match decides; two matches in one group refuse.**
  Two ``using`` directives that both bring a ``Handler`` into scope are ambiguous to the compiler
  too. A name the language binds explicitly — a Java single-type import, a C# ``using`` alias —
  **ends** the lookup: ``import java.sql.DatabaseMetaData`` means the JDK type even when the
  repository declares its own ``DatabaseMetaData``, so it gets no edge, never the in-repo one.
- **The member is found on the nearest in-repo type that declares it** (D4): the receiver's own
  type, then its base-class chain, then its interfaces level by level — a class's method wins
  over an interface default, as in both languages. An override always sits nearer than what it
  overrides; two unrelated interfaces at one level both declaring it is an ambiguity, and refuses.
- **Nothing is guessed past an external supertype.** A type with a base this repository does not
  declare (``ControllerBase``, ``java.util.AbstractList``) may inherit any member or field from it,
  so once a walk reaches one without an answer it refuses: ``User.FindFirst()`` inside a controller
  is the framework's ``User`` property, not the in-repo ``User`` class.
  An interface-typed receiver lands on the interface's member (D2) — true to the source.
  ``pkg.store.FactStore.interface_callers_of`` is what carries a change to an implementation to
  those callers.
- **Scope is lexical** (:class:`Scope`): a local applies inside its own block, from its declaration
  on (Java) or through the whole block (C#, where using it earlier does not compile); two bindings
  that both apply refuse. A name bound without a type we can read refuses: a lambda parameter,
  ``var x = Call()``, a ``foreach (var x …)``, a query variable, a type parameter.
  ``var x = new T(...)`` has its type written in the initializer, so it resolves (D3).
- **A field is looked up where the language looks it up**: the type itself (every partial
  declaration of it), then its in-repo supertypes nearest first (an inherited field), then — for
  a Java inner class — the enclosing types (D13).

What is out of scope refuses by omission: call chains (``a.b().c()`` needs return types),
extension methods (D10), ``?.`` conditional access, generic type parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind, Provenance

#: In a candidate group, marks a name the language bound explicitly: if nothing in the group is
#: declared here, the lookup ends there — the type is external — instead of falling through.
STOP = "!"

#: A binding whose type is not written (a lambda parameter, ``var x = Call()``): it shadows, and
#: a call through it refuses.
UNREADABLE = object()


@dataclass(frozen=True)
class TypeRef:
    """A written type name, as the candidate ids it could denote, in the compiler's order.

    ``nested`` is tried first as a member type of each ``enclosing`` type (innermost first) — its
    own, then one its supertypes declare. Then ``groups``, in order; the first with a declared
    match decides. Last, ``simple`` under the compilation unit's ``using_prefixes`` together with
    the ``project``'s ``global using``s — one precedence level, so one group.
    """

    groups: tuple[tuple[str, ...], ...]
    simple: str = ""
    using_prefixes: tuple[str, ...] = ()
    enclosing: tuple[str, ...] = ()
    nested: str = ""
    project: str = ""


@dataclass(frozen=True)
class DeferredCall:
    """One ``recv.member()`` call, held until every declaration is known.

    Exactly one of ``receiver`` (the type is known at the site: a local, a parameter, a static
    call on a type name) or ``field_of`` (``recv`` is a field or property of this type, its
    supertypes or its enclosing types) is set. ``static`` is tried when ``field_of`` finds no
    field of that name: C#'s ``Helper.Format(x)``. With ``chain_head``, ``recv`` is only the head
    of a dotted receiver (``Status.Kind.Parse()``): if it is a field the call goes through a member
    chain this pass does not follow, so only the static reading can produce an edge.
    """

    caller: str
    member: str
    rel: str
    line: int
    receiver: TypeRef | None = None
    field_of: str | None = None
    field_name: str = ""
    static: TypeRef | None = None
    chain_head: bool = False


class Scope:
    """Every name one method binds, with where each binding is in force.

    A binding covers ``[start, end)`` bytes of the source (its block) from ``decl`` on. Java puts a
    local in scope from its declaration; C# for its whole block — using it earlier is an error
    there, so ``before_decl_refuses`` turns such a site into a refusal rather than a field lookup.
    """

    def __init__(self, *, before_decl_refuses: bool) -> None:
        self._bindings: dict[str, list[tuple[int, int, int, object]]] = {}
        self._before_decl_refuses = before_decl_refuses

    def bind(self, name: str, ref: object, start: int, end: int, decl: int) -> None:
        if name:
            self._bindings.setdefault(name, []).append((start, end, decl, ref))

    def lookup(self, name: str, at: int) -> object | None:
        """``None`` — not a local here (try fields); a :class:`TypeRef`; or :data:`UNREADABLE`."""
        hits = []
        for start, end, decl, ref in self._bindings.get(name, ()):
            if not start <= at < end:
                continue
            if decl > at:
                if self._before_decl_refuses:
                    return UNREADABLE
                continue
            hits.append(ref)
        if not hits:
            return None
        return hits[0] if len(hits) == 1 else UNREADABLE


@dataclass
class ReceiverState:
    """What one front-end accumulates across a walk; cleared by :func:`resolve_calls`."""

    language: str
    calls: list[DeferredCall] = field(default_factory=list)
    fields: dict[str, dict[str, TypeRef | None]] = field(default_factory=dict)  # type -> name -> type
    outer: dict[str, str] = field(default_factory=dict)  # nested type id -> lexically enclosing type id
    interfaces: set[str] = field(default_factory=set)  # type ids declared as interfaces
    # type id -> its `<T, U>`; its keys are every type this front-end walked (and so knows the fields of)
    type_params: dict[str, frozenset[str]] = field(default_factory=dict)
    global_prefixes: dict[str, set[str]] = field(default_factory=dict)  # project -> C# `global using`s
    # (type id, provisional base id) -> how the base name was written, for `resolve_bases`
    base_refs: dict[tuple[str, str], TypeRef | None] = field(default_factory=dict)

    def add_field(self, type_id: str, name: str, ref: TypeRef | None) -> None:
        """Record a field's declared type. Two declarations that disagree (partial classes that
        both declare it, which C# forbids) make it unreadable rather than first-wins."""
        known = self.fields.setdefault(type_id, {})
        known[name] = ref if name not in known or known[name] == ref else None

    def add_base(self, type_id: str, provisional: str, ref: TypeRef | None) -> None:
        """Like ``add_field``: partial declarations that write one base differently disagree."""
        key = (type_id, provisional)
        self.base_refs[key] = ref if key not in self.base_refs or self.base_refs[key] == ref else None

    def clear(self) -> None:
        self.calls.clear()
        self.fields.clear()
        self.outer.clear()
        self.interfaces.clear()
        self.type_params.clear()
        self.global_prefixes.clear()
        self.base_refs.clear()


class TypeIndex:
    """Declared types, their members and supertypes — the whole-repository facts every question
    above needs. Public so a front-end's other whole-repo passes (C# DI) resolve a written type
    exactly as a receiver's is resolved."""

    def __init__(self, batch: FactBatch, state: ReceiverState) -> None:
        self.state = state
        nodes = {n.id: n for n in batch.nodes}
        self.declared = {i for i, n in nodes.items() if n.kind is NodeKind.TYPE and n.grounded}
        self.members: dict[str, set[str]] = {}
        self.supers: dict[str, list[str]] = {}
        self.open: set[str] = set()  # a declared type with a base this repository does not declare
        for e in batch.edges:
            if e.kind is EdgeKind.CONTAINS and e.src in self.declared:
                child = nodes.get(e.dst)
                if child is not None and child.kind is NodeKind.FUNCTION and child.grounded:
                    self.members.setdefault(e.src, set()).add(child.name)
            elif e.kind is EdgeKind.IMPLEMENTS and e.src in self.declared:
                if e.dst in self.declared:
                    self.supers.setdefault(e.src, []).append(e.dst)
                else:
                    self.open.add(e.src)
        for base in self.supers.values():
            base.sort()
        self._levels_memo: dict[str, list[list[str]]] = {}
        self._ancestors_memo: dict[str, frozenset[str]] = {}
        self._type_memo: dict[TypeRef, str | None] = {}

    def type_of(self, ref: TypeRef | None) -> str | None:
        if ref is None:
            return None
        if ref not in self._type_memo:
            self._type_memo[ref] = self._type_of(ref)
        return self._type_memo[ref]

    def _type_of(self, ref: TypeRef) -> str | None:
        groups: list[tuple[str, ...]] = []
        for outer in ref.enclosing if ref.nested else ():
            groups.append((f"{outer}.{ref.nested}",))
            # C# inherits nested types from base classes only; Java from interfaces too.
            inherited = sorted(
                a
                for a in self._ancestors(outer)
                if self.state.language != "csharp" or a not in self.state.interfaces
            )
            groups.append(tuple(f"{a}.{ref.nested}" for a in inherited))
        groups.extend(ref.groups)
        if ref.simple:
            prefixes = sorted({*ref.using_prefixes, *self.state.global_prefixes.get(ref.project, ())})
            groups.append(tuple(f"{self.state.language}:{p}.{ref.simple}" for p in prefixes))
        for group in groups:
            hits = {c for c in group if c in self.declared}
            if len(hits) == 1:
                return hits.pop()
            if hits or STOP in group:
                return None  # ambiguous at the compiler's level, or explicitly an external type
        return None

    def _levels(self, type_id: str) -> list[list[str]]:
        """``type_id`` and its in-repo supertypes, one list per inheritance level, cycle-safe."""
        if type_id not in self._levels_memo:
            levels, seen, frontier = [], {type_id}, [type_id]
            while frontier:
                levels.append(frontier)
                nxt = sorted({s for t in frontier for s in self.supers.get(t, ()) if s not in seen})
                seen.update(nxt)
                frontier = nxt
            self._levels_memo[type_id] = levels
        return self._levels_memo[type_id]

    def _ancestors(self, type_id: str) -> frozenset[str]:
        if type_id not in self._ancestors_memo:
            self._ancestors_memo[type_id] = frozenset(t for level in self._levels(type_id)[1:] for t in level)
        return self._ancestors_memo[type_id]

    def is_subtype(self, sub: str, sup: str) -> bool:
        return sup in self._ancestors(sub)

    def member_owner(self, type_id: str, member: str) -> str | None:
        """The nearest in-repo type that declares ``member``: ``type_id``, its base-class chain,
        then its interfaces level by level. Refuses past an external supertype, which may declare
        it, and on two unrelated interfaces at one level."""
        chain, cur = [], type_id
        while cur not in chain:
            chain.append(cur)
            if member in self.members.get(cur, ()):
                return cur
            if cur in self.open:
                return None  # an external base may declare (or implement) it
            classes = [s for s in self.supers.get(cur, ()) if s not in self.state.interfaces]
            if len(classes) != 1:
                break
            cur = classes[0]
        seen = set(chain)
        frontier = sorted({s for t in chain for s in self.supers.get(t, ()) if s not in seen})
        while frontier:
            seen.update(frontier)
            owners = [t for t in frontier if member in self.members.get(t, ())]
            # An owner that is itself a supertype of another owner is overridden by it.
            owners = [o for o in owners if not any(o != p and self.is_subtype(p, o) for p in owners)]
            if len(owners) == 1:
                return owners[0]
            if owners or any(t in self.open for t in frontier):
                return None
            frontier = sorted({s for t in frontier for s in self.supers.get(t, ()) if s not in seen})
        return None

    def field_type(self, type_id: str, name: str) -> tuple[bool, str | None]:
        """``(found, type)`` for a field: the type and its supertypes nearest first, then the
        lexically enclosing types. ``found`` without a type means it exists — or may, behind an
        external base — but cannot be read, so nothing falls back to a static or outer reading."""
        seen: set[str] = set()
        current: str | None = type_id
        while current is not None and current not in seen:
            seen.add(current)
            for level in self._levels(current):
                refs = [self.state.fields[t][name] for t in level if name in self.state.fields.get(t, {})]
                if len(refs) == 1:
                    return True, self.type_of(refs[0])
                # A type with an external base, or one another front-end declared (a Kotlin class
                # in Java's `java:` space), may hold this field without our having recorded it.
                if refs or any(t in self.open or t not in self.state.type_params for t in level):
                    return True, None
            current = self.state.outer.get(current)
        return False, None


def resolve_bases(batch: FactBatch, state: ReceiverState) -> FactBatch:
    """Repoint an ``IMPLEMENTS`` edge a per-file pass placed provisionally, when the base name as
    written resolves — through ``using`` directives, the namespace chain, enclosing types, imports
    — to a type this repository declares. A base written as an explicitly external name stays
    external. Measured on a .NET service before this: 46 of 77 "external" bases were in-repo
    interfaces in a sibling namespace, so supertype walks and interface reach stopped at them.
    """
    if not state.base_refs:
        return batch
    index = TypeIndex(batch, state)
    changed = False
    result = FactBatch()
    for node in batch.nodes:
        result.add_node(node)
    for edge in batch.edges:
        ref = state.base_refs.get((edge.src, edge.dst)) if edge.kind is EdgeKind.IMPLEMENTS else None
        # The written name decides whenever it resolves — also when the per-file guess happens to
        # be declared: `class Impl implements Listener` inside a class with its own nested
        # `Listener` means that one, not the package's.
        target = index.type_of(ref) if ref is not None else None
        if target is not None and target != edge.dst:
            result.add_edge(Edge(edge.src, target, edge.kind, edge.provenance))
            changed = True
        else:
            result.add_edge(edge)
    return result if changed else batch


def resolve_calls(batch: FactBatch, state: ReceiverState) -> None:
    """Add a ``CALLS`` edge for every deferred call that resolves; then clear ``state``."""
    if not state.calls:
        state.clear()
        return
    index = TypeIndex(batch, state)
    for call in state.calls:
        receiver_type: str | None
        if call.field_of is not None:
            found, receiver_type = index.field_type(call.field_of, call.field_name)
            if found and call.chain_head:
                continue  # `field.member.m()` — a chain, not followed
            if not found:
                receiver_type = index.type_of(call.static)
        else:
            receiver_type = index.type_of(call.receiver)
        if receiver_type is None:
            continue
        owner = index.member_owner(receiver_type, call.member)
        if owner is not None:
            batch.add_edge(
                Edge(call.caller, f"{owner}.{call.member}", EdgeKind.CALLS, Provenance(call.rel, call.line))
            )
    state.clear()


__all__ = [
    "STOP",
    "UNREADABLE",
    "DeferredCall",
    "ReceiverState",
    "Scope",
    "TypeIndex",
    "TypeRef",
    "resolve_bases",
    "resolve_calls",
]
