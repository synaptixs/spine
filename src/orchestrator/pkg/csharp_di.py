"""ASP.NET Core dependency-injection registrations → ``PROVIDES`` (B21, D6/D12).

``services.AddScoped<IMailer, SmtpMailer>()`` wires ``SmtpMailer`` behind ``IMailer``, and every
consumer is then handed ``IMailer``: the typed-receiver pass lands their calls on
``IMailer.Send`` (D2), and nothing calls ``SmtpMailer.Send`` directly. So "what breaks if I change
``SmtpMailer``" was answered with nothing. ``PROVIDES`` is the edge Kotlin's Hilt/Dagger bindings
introduced for exactly this (``kotlin_di.py``): **this declaration makes this type available for
injection**, source the implementation, target the interface — and ``FactStore`` already follows
it outbound to the interface's callers.

Measured before this existed: 66 two-type registrations in one .NET service, 18 in another, and
not one ``PROVIDES`` edge.

**Only the two-type generic form** — ``Add{Scoped,Transient,Singleton}<I, T>()`` and its
``TryAdd*`` twins — produces an edge (D12). A single-type ``AddTransient<Worker>()`` binds a
concrete type as itself, so there is nothing to connect. A factory ``AddScoped<IAudit>(sp => new
DbAudit())`` returns whatever the lambda builds; reading ``new DbAudit`` out of it would be the
first guess in the chain, so it is a known gap (corpus ``csharp/di_bindings``). Both type
arguments are resolved the way any written type is (``using``, namespace chain) and must name
types this repository declares — a registration of a framework type is not a first-party binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Provenance
from orchestrator.pkg.typed_receivers import ReceiverState, TypeIndex, TypeRef

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: The registration methods whose ``<Interface, Implementation>`` form binds one to the other.
REGISTRATIONS = frozenset(
    f"{prefix}{life}" for prefix in ("Add", "TryAdd") for life in ("Scoped", "Transient", "Singleton")
)


@dataclass(frozen=True)
class Binding:
    """One two-type registration, held until every declaration in the repository is known."""

    implementation: TypeRef
    interface: TypeRef
    rel: str
    line: int


def type_arguments(invocation: TSNode) -> tuple[str, list[TSNode]] | None:
    """``("AddScoped", [IMailer, SmtpMailer])`` for ``x.AddScoped<IMailer, SmtpMailer>(...)``."""
    fn = invocation.child_by_field_name("function")
    if fn is None or fn.type != "member_access_expression":
        return None
    name = fn.child_by_field_name("name")
    if name is None or name.type != "generic_name":
        return None
    head = next((c for c in name.named_children if c.type == "identifier"), None)
    args = next((c for c in name.named_children if c.type == "type_argument_list"), None)
    if head is None or args is None:
        return None
    return head.text.decode("utf-8", "replace") if head.text else "", list(args.named_children)


def emit_provides(batch: FactBatch, bindings: list[Binding], state: ReceiverState) -> None:
    """Add ``implementation PROVIDES interface`` for every binding whose two types are declared."""
    if not bindings:
        return
    index = TypeIndex(batch, state)
    for b in bindings:
        impl, iface = index.type_of(b.implementation), index.type_of(b.interface)
        if impl is not None and iface is not None and impl != iface:
            batch.add_edge(Edge(impl, iface, EdgeKind.PROVIDES, Provenance(b.rel, b.line)))
    bindings.clear()


__all__ = ["REGISTRATIONS", "Binding", "emit_provides", "type_arguments"]
