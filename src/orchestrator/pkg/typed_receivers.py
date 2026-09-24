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
  too.
- **The member is found on the nearest in-repo type that declares it** (D4): the receiver's own
  type, else its supertypes level by level. An override always sits nearer than what it
  overrides; two supertypes at the same level both declaring it is an ambiguity, and refuses.
  An interface-typed receiver lands on the interface's member (D2) — true to the source.
  ``pkg.store.FactStore.interface_callers_of`` is what carries a change to an implementation to
  those callers.
- **A name bound without a type we can read refuses**: a lambda parameter, ``var x = Call()``, a
  ``foreach (var x …)``, two locals of one name with different types. ``var x = new T(...)`` has
  its type written in the initializer, so it resolves (D3).
- **A field is looked up where the language looks it up**: the type itself (every partial
  declaration of it), then its in-repo supertypes nearest first (an inherited field), then — for
  a Java inner class — the enclosing types (D13).

What is out of scope refuses by omission: call chains (``a.b().c()`` needs return types),
extension methods (D10), ``?.`` conditional access, generic type parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind, Provenance


@dataclass(frozen=True)
class TypeRef:
    """A written type name, as the candidate ids it could denote.

    ``groups`` are tried in order; the first with a declared match decides. ``simple`` and
    ``using_prefixes`` form one last group, merged with the repository's global prefixes (C#'s
    ``global using``) — they share a precedence level, so they must share a group.
    """

    groups: tuple[tuple[str, ...], ...]
    simple: str = ""
    using_prefixes: tuple[str, ...] = ()
    # Tried before ``groups``: ``nested`` as a member type of each enclosing type, innermost
    # first — its own, then one its supertypes declare (Java and C# both put an inherited
    # member type in scope: `SessionEventListener` inside a class implementing `Session`).
    enclosing: tuple[str, ...] = ()
    nested: str = ""


@dataclass(frozen=True)
class DeferredCall:
    """One ``recv.member()`` call, held until every declaration is known.

    Exactly one of ``receiver`` (the type is known at the site: a local, a parameter, a static
    call on a type name) or ``field_of`` (``recv`` is a field or property of this type, its
    supertypes or its enclosing types) is set. ``static`` is tried when ``field_of`` finds no
    field of that name: C#'s ``Helper.Format(x)``.
    """

    caller: str
    member: str
    rel: str
    line: int
    receiver: TypeRef | None = None
    field_of: str | None = None
    field_name: str = ""
    static: TypeRef | None = None


@dataclass
class ReceiverState:
    """What one front-end accumulates across a walk; cleared by :func:`resolve_calls`."""

    language: str
    calls: list[DeferredCall] = field(default_factory=list)
    fields: dict[str, dict[str, TypeRef | None]] = field(default_factory=dict)  # type -> name -> type
    outer: dict[str, str] = field(default_factory=dict)  # nested type id -> lexically enclosing type id
    global_prefixes: set[str] = field(default_factory=set)  # C# `global using`
    # (type id, provisional base id) -> how the base name was written, for `resolve_bases`
    base_refs: dict[tuple[str, str], TypeRef] = field(default_factory=dict)

    def add_field(self, type_id: str, name: str, ref: TypeRef | None) -> None:
        """Record a field's declared type. Two declarations that disagree (partial classes that
        both declare it, which C# forbids) make it unreadable rather than first-wins."""
        known = self.fields.setdefault(type_id, {})
        known[name] = ref if name not in known or known[name] == ref else None

    def clear(self) -> None:
        self.calls.clear()
        self.fields.clear()
        self.outer.clear()
        self.global_prefixes.clear()
        self.base_refs.clear()


class TypeIndex:
    """Declared types, their members and in-repo supertypes — the whole-repository facts every
    question above needs. Public so a front-end's other whole-repo passes (C# DI) resolve a
    written type exactly as a receiver's is resolved."""

    def __init__(self, batch: FactBatch, state: ReceiverState) -> None:
        self.state = state
        nodes = {n.id: n for n in batch.nodes}
        self.declared = {i for i, n in nodes.items() if n.kind is NodeKind.TYPE and n.grounded}
        self.members: dict[str, set[str]] = {}
        self.supers: dict[str, list[str]] = {}
        for e in batch.edges:
            if e.kind is EdgeKind.CONTAINS and e.src in self.declared:
                child = nodes.get(e.dst)
                if child is not None and child.kind is NodeKind.FUNCTION and child.grounded:
                    self.members.setdefault(e.src, set()).add(child.name)
            elif e.kind is EdgeKind.IMPLEMENTS and e.src in self.declared and e.dst in self.declared:
                self.supers.setdefault(e.src, []).append(e.dst)
        for base in self.supers.values():
            base.sort()

    def type_of(self, ref: TypeRef | None) -> str | None:
        if ref is None:
            return None
        groups: list[tuple[str, ...]] = []
        for outer in ref.enclosing if ref.nested else ():
            groups.append((f"{outer}.{ref.nested}",))
            groups.append(tuple(f"{a}.{ref.nested}" for a in sorted(self._ancestors(outer))))
        groups.extend(ref.groups)
        if ref.simple:
            prefixes = sorted({*ref.using_prefixes, *self.state.global_prefixes})
            groups.append(tuple(f"{self.state.language}:{p}.{ref.simple}" for p in prefixes))
        for group in groups:
            hits = {c for c in group if c in self.declared}
            if len(hits) == 1:
                return hits.pop()
            if hits:
                return None  # ambiguous at the level the compiler would decide it
        return None

    def _levels(self, type_id: str) -> list[list[str]]:
        """``type_id`` and its in-repo supertypes, one list per inheritance level, cycle-safe."""
        levels, seen, frontier = [], {type_id}, [type_id]
        while frontier:
            levels.append(frontier)
            nxt = sorted({s for t in frontier for s in self.supers.get(t, ()) if s not in seen})
            seen.update(nxt)
            frontier = nxt
        return levels

    def member_owner(self, type_id: str, member: str) -> str | None:
        """The nearest in-repo type, ``type_id`` or a supertype, that declares ``member``."""
        for level in self._levels(type_id):
            owners = [t for t in level if member in self.members.get(t, ())]
            # An owner that is itself a supertype of another owner is overridden by it:
            # `class X extends AbstractProtocol implements Protocol`, where AbstractProtocol
            # implements Protocol too, calls AbstractProtocol's — no ambiguity.
            owners = [o for o in owners if not any(o != p and o in self._ancestors(p) for p in owners)]
            if len(owners) == 1:
                return owners[0]
            if owners:
                return None  # two unrelated supertypes at one level both declare it
        return None

    def _ancestors(self, type_id: str) -> set[str]:
        return {t for level in self._levels(type_id)[1:] for t in level}

    def field_type(self, type_id: str, name: str) -> tuple[bool, str | None]:
        """``(found, type)`` for a field: the type and its supertypes nearest first, then the
        lexically enclosing types. ``found`` without a type means it exists but is unreadable."""
        seen: set[str] = set()
        current: str | None = type_id
        while current is not None and current not in seen:
            seen.add(current)
            for level in self._levels(current):
                refs = [self.state.fields[t][name] for t in level if name in self.state.fields.get(t, {})]
                if len(refs) == 1:
                    return True, self.type_of(refs[0])
                if refs:
                    return True, None
            current = self.state.outer.get(current)
        return False, None


def resolve_bases(batch: FactBatch, state: ReceiverState) -> FactBatch:
    """Repoint an ``IMPLEMENTS`` edge a per-file pass placed provisionally, when the base name as
    written resolves — through ``using`` directives, the namespace chain, enclosing types — to a
    type this repository declares. Anything else is returned untouched for the front-end's own
    fallback. Measured on a .NET service before this: 46 of 77 "external" bases were in-repo
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
        target = index.type_of(ref) if ref is not None and edge.dst not in index.declared else None
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


__all__ = ["DeferredCall", "ReceiverState", "TypeIndex", "TypeRef", "resolve_bases", "resolve_calls"]
