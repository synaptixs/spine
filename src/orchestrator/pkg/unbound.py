"""Calls the graph could not bind to a receiver type — a side list, never facts (B62).

A language front-end emits a ``CALLS`` edge only when it can prove what the receiver is (a
declared parameter type, a constructor-assigned local, ``self``), because a wrong edge is worse
than a missing one (SSPN-48). A call it refuses leaves no trace, so ``blast_radius`` could say
"5 callers" and not "and some calls named ``get`` were not traced". This module is the record
of those calls.

They are **not** nodes or edges and must not become either. An edge to a name nobody declared would
fail ``pkg verify``, show up in ``FactStore.touches``, move the strict accuracy gate and break the
RDF export; a list on :class:`~orchestrator.pkg.facts.FactBatch` is dropped by ``merge`` and
``scope_batch``. They live on the extractor, beside the other side-channels
(``unresolved_calls`` for HTTP joins, ``unresolved_member_calls`` for C/C++), and a count is a hint to
look, not an answer: the same name is also a ``str.find`` or a ``dict.get`` somewhere else.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

#: Longest receiver text kept: enough to tell ``store`` from ``stmt``, not a source excerpt.
MAX_RECEIVER_CHARS = 40


@dataclass(frozen=True)
class UnboundCall:
    """One attribute call a front-end refused to bind: ``<receiver>.<member>(…)`` at ``rel:line``
    inside ``caller``. ``receiver`` is the receiver's source text, shortened — the evidence a reader
    needs to judge whether this could be a call to the symbol they asked about."""

    caller: str
    member: str
    rel: str
    line: int
    receiver: str


def receiver_text(node: ast.expr) -> str:
    """The receiver's source text, shortened to :data:`MAX_RECEIVER_CHARS`."""
    text = ast.unparse(node)
    return text if len(text) <= MAX_RECEIVER_CHARS else text[: MAX_RECEIVER_CHARS - 1] + "…"


def sort_key(call: UnboundCall) -> tuple[str, str, int, str, str]:
    """A total order, so the same graph always lists the same calls in the same order."""
    return (call.member, call.rel, call.line, call.caller, call.receiver)
