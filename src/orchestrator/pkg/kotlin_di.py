"""Hilt / Dagger bindings → ``PROVIDES``, the edge dependency injection needed.

P4 of docs/specs/kotlin-support-roadmap.md, D15.

**Why this needed a new edge kind rather than reusing ``IMPLEMENTS``.** In a
DI codebase the question "what breaks if I change ``OfflineFirstTopicsRepository``"
turns on *which* implementation is wired behind an interface, and no existing edge
carries that. ``IMPLEMENTS`` is already true of every implementation — the
validation app has three for ``TopicsRepository``, two of them test fakes — so
reusing it answers the question with every candidate rather than the wired one.
``@Provides`` has no implementation type at all, only a factory function, so
``IMPLEMENTS`` could not express it even in principle.

So ``EdgeKind.PROVIDES`` means exactly one thing: **this declaration makes this
type available for injection.**

* ``@Binds fun b(impl: OfflineRepo): Repo`` → ``OfflineRepo`` **PROVIDES** ``Repo``.
  The source is the *implementation*, because that is the thing a reader changes
  and the thing the edge exists to make reachable.
* ``@Provides fun provideRepo(...): Repo`` → the provider **function** PROVIDES
  ``Repo``. There is no implementation type to name; the factory is the provider.

**Qualifiers are recorded, never resolved.** Two providers for one type behind
different qualifiers (``@Named``, or a custom annotation) both get an edge, and
``blast_radius`` reports both. Picking one would mean modelling Dagger's
component graph, and picking wrong is worse than reporting two.

**Injection sites need no edge of their own.** An ``@Inject constructor(private
val repo: Repo)`` already produces a ``Field`` typed ``Repo`` (D6), and P2's
typed-receiver rule already resolves ``repo.getTopics()`` onto ``Repo.getTopics``.
The consumers of an interface are therefore *already* in the graph as ``CALLS``
edges onto its members — measured on the validation app, ``TopicsRepository``'s
methods have callers in three modules. What was missing was only the hop from the
implementation to the interface, which is what ``PROVIDES`` supplies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Provenance
from orchestrator.pkg.kotlin_names import (
    Annotation,
    annotations_of,
    bare_type,
    element_type,
    field_text,
    text,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: Annotations that declare a binding. `@Binds` names an implementation in its
#: parameter; `@Provides` builds the value itself.
_BINDS = "Binds"
_PROVIDES = "Provides"

#: Annotations Dagger treats as qualifiers. Recorded on provenance only (D15) —
#: two providers for one type behind different qualifiers both keep their edge.
_QUALIFIERS = frozenset({"Named", "Qualifier"})

#: Generic wrappers a `PROVIDES` target may be peeled through, **by resolved id**.
#: `Lazy<T>`/`Provider<T>` are indirection Dagger unwraps for you — a provider of
#: `Lazy<Repo>` makes `Repo` available, same as a plain `Repo` return. Everything
#: else generic — `Set<T>`, `List<T>`, `Flow<T>`, `Optional<T>` — is either a
#: distinct Dagger multibinding key or not a Dagger unwrap at all, so peeling to
#: the element would assert a binding key that does not exist (#393).
#:
#: Ids rather than simple names, because the name alone cannot tell three different
#: types apart: `kotlin.Lazy` needs **no import line at all** (it is a Kotlin default
#: import, the `by lazy` delegate) and is not a Dagger unwrap; a repository is free to
#: declare its own `class Provider<T>`; and a fully-qualified `dagger.Lazy<T>` used to
#: fail the check for the opposite reason, since its written name is not the bare one.
_UNWRAPPED = frozenset({"java:dagger.Lazy", "java:javax.inject.Provider", "java:jakarta.inject.Provider"})


def read_module(
    node: TSNode,
    type_id: str,
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> bool:
    """Emit ``PROVIDES`` for the bindings a Hilt/Dagger module declares.

    Returns whether this type was a module. A class with no ``@Module`` is not
    scanned: ``@Binds`` outside one is not a binding, and reading it anyway would
    invent wiring from a method signature that happens to look similar.
    """
    if _find(annotations_of(node, source), "Module") is None:
        return False
    found = False
    for body in (c for c in node.named_children if c.type in ("class_body", "enum_class_body")):
        for member in body.named_children:
            if member.type == "companion_object":
                # `@Provides` lives in a companion when the module is a class with
                # `@Binds` methods — both halves belong to the same module.
                for inner in (c for c in member.named_children if c.type == "class_body"):
                    found |= _read_members(inner, type_id, resolve, source, rel, batch)
            elif member.type == "function_declaration":
                found |= _read_binding(member, type_id, resolve, source, rel, batch)
    return found


def _read_members(
    body: TSNode, type_id: str, resolve: Any, source: bytes, rel: str, batch: FactBatch
) -> bool:
    found = False
    for member in body.named_children:
        if member.type == "function_declaration":
            found |= _read_binding(member, type_id, resolve, source, rel, batch)
    return found


def _read_binding(
    method: TSNode, type_id: str, resolve: Any, source: bytes, rel: str, batch: FactBatch
) -> bool:
    annotations = annotations_of(method, source)
    binding = _find(annotations, _BINDS) or _find(annotations, _PROVIDES)
    if binding is None:
        return False
    name = field_text(method, "name", source)
    if not name:
        return False
    provided = _returned_type(method, resolve, source)
    if not provided:
        return False  # a generic or unresolvable return type names no single provider

    if binding.name == _BINDS:
        # The implementation is the parameter. It is the better source for the edge
        # than the binding function, because it is what a reader changes.
        source_id = _first_parameter_type(method, resolve, source) or f"{type_id}.{name}"
    else:
        source_id = f"{type_id}.{name}"
    if source_id == provided:
        return False  # a self-binding says nothing
    # The edge only. Whether `provided` is a name the source *stated* — through an import,
    # which has already put a placeholder node there — or one `_resolve_type` assumed from
    # the enclosing package is not knowable here, and minting the node made the two
    # indistinguishable: a guessed target arrived pre-grounded, so `finalize`'s repoint
    # (which identifies a guess precisely by its having no node anywhere) could never see
    # it, and `@Provides fun x(): Clock` asserted a `Clock` class inside the DI module's
    # own package. Left dangling for exactly one pass; `finalize` either finds the
    # declaration or repoints to the bare name, and never leaves it dangling.
    batch.add_edge(Edge(source_id, provided, EdgeKind.PROVIDES, Provenance(rel, binding.line)))
    return True


def _returned_type(method: TSNode, resolve: Any, source: bytes) -> str:
    """The declared return type of a binding method, resolved to a node id.

    Read positionally: the grammar puts the return type in a ``user_type`` that
    follows ``function_value_parameters``, with no field name to ask for.
    """
    params = next((c for c in method.named_children if c.type == "function_value_parameters"), None)
    if params is None:
        return ""
    for child in method.named_children:
        if child.type == "user_type" and child.start_byte > params.end_byte:
            return _binding_key(text(child, source), resolve)
    return ""


def _binding_key(declared: str, resolve: Any) -> str:
    """The id a return type binds, or ``""`` when it binds nothing this can name.

    Unwraps **one level at a time**, re-asking the question at each one. The first
    version tested the allowlist on the outermost name and then called `element_type`,
    which peels every level in a loop — so an allowlisted outer wrapper opened the door
    to whatever was inside it, and `Provider<Set<Clock>>` reduced to `Clock`. That is an
    ordinary, correct Dagger shape whose key is `Set<Clock>`, and nothing injecting a
    plain `Clock` is satisfied by it (#393).

    Each layer is resolved before it is judged, so a wrapper is recognised by what it
    *is* rather than what it is called. `bare_type` keeps a written qualification, and
    the resolver passes a dotted name straight through, so `dagger.Lazy<T>` resolves as
    itself while a bare `Lazy` goes through this file's imports — which is what separates
    Dagger's from `kotlin.Lazy`, a default import that needs no import line.
    """
    current = declared
    while "<" in current:
        if (resolve(bare_type(current)) or "") not in _UNWRAPPED:
            # A distinct binding key: a multibinding (`Set<T>`, `List<T>`), a stream
            # (`Flow<T>`), or a type this repository declares itself. Assert nothing
            # rather than the wrong thing.
            return ""
        inner = current[current.index("<") + 1 : current.rindex(">")].strip()
        if not inner:
            return ""
        current = inner
    return resolve(bare_type(current)) or ""


def _first_parameter_type(method: TSNode, resolve: Any, source: bytes) -> str:
    """``@Binds fun b(impl: OfflineRepo): Repo`` → the id of ``OfflineRepo``."""
    params = next((c for c in method.named_children if c.type == "function_value_parameters"), None)
    if params is None:
        return ""
    for param in params.named_children:
        if param.type != "parameter":
            continue
        declared = next((text(c, source) for c in param.named_children if c.type == "user_type"), "")
        simple = element_type(declared).rsplit(".", 1)[-1]
        resolved = resolve(simple) if simple else None
        if resolved:
            return str(resolved)
    return ""


def qualifiers_of(method: TSNode, source: bytes) -> list[str]:
    """Qualifier annotation names on a binding, for provenance only (D15).

    Deliberately unused by the edge itself: two providers for one type with
    different qualifiers both keep their edge, and ``blast_radius`` says "2
    providers" rather than choosing. Choosing would mean modelling Dagger's
    component resolution, and choosing wrong is worse than reporting both.
    """
    return [a.name for a in annotations_of(method, source) if a.name in _QUALIFIERS]


def _find(annotations: list[Annotation], name: str) -> Annotation | None:
    return next((a for a in annotations if a.name == name), None)


__all__ = ["qualifiers_of", "read_module"]
