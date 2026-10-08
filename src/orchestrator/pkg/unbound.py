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
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: The languages whose front-end records its refused calls. For any other, "nothing recorded" would
#: mean "not looked at", and a tool must say that — never report it as zero.
TRACKED_LANGUAGES = frozenset({"python"})

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
    try:
        text = ast.unparse(node)
    except RecursionError:
        # `ast.parse` is iterative but `ast.unparse` recurses, so a long concatenation or call chain
        # parses and then cannot be printed. Evidence is optional; a crashed extraction is not.
        return "…"
    return text if len(text) <= MAX_RECEIVER_CHARS else text[: MAX_RECEIVER_CHARS - 1] + "…"


def sort_key(call: UnboundCall) -> tuple[str, str, int, str, str]:
    """A total order, so the same graph always lists the same calls in the same order."""
    return (call.member, call.rel, call.line, call.caller, call.receiver)


# ---- the index a tool reads --------------------------------------------------------------------

#: Sites kept per name. The count beside them stays exact, so the list says "top 50 of M" and never
#: lets a clipped view imply completeness (invariant 7); it also bounds what is persisted.
MAX_SITES = 50

#: Bumped when the persisted shape changes. (The cache fingerprint already invalidates on any edit
#: under ``pkg/``; this guards a reader against a payload it does not understand.)
FORMAT_VERSION = 1


@dataclass(frozen=True)
class UnboundEntry:
    """Everything recorded under one name: how many, and the first :data:`MAX_SITES` in order."""

    count: int
    sites: tuple[UnboundCall, ...]


@dataclass(frozen=True)
class UnboundIndex:
    """The unbound calls of one repository (or a merged set), by name.

    An empty index is a real answer — "tracked, and nothing was refused". Telling a caller that a
    language is *not* tracked is a different statement and is made by the caller, per target.
    """

    entries: dict[str, UnboundEntry]

    @classmethod
    def from_calls(cls, calls: Iterable[UnboundCall]) -> UnboundIndex:
        by_member: dict[str, list[UnboundCall]] = {}
        for call in calls:
            by_member.setdefault(call.member, []).append(call)
        return cls(
            {
                member: UnboundEntry(len(group), tuple(sorted(group, key=sort_key)[:MAX_SITES]))
                for member, group in sorted(by_member.items())
            }
        )

    def entry(self, member: str) -> UnboundEntry | None:
        return self.entries.get(member)

    def total(self) -> int:
        return sum(e.count for e in self.entries.values())

    def scoped(self, repo: str) -> UnboundIndex:
        """The same index with every caller id scoped to ``repo``, so it names the nodes of a
        merged multi-repo graph (``py:billing@app.use.f``), which is where the lookup happens."""
        from orchestrator.pkg.scoping import scope_id

        return UnboundIndex(
            {
                member: UnboundEntry(
                    e.count,
                    tuple(
                        UnboundCall(scope_id(c.caller, repo), c.member, c.rel, c.line, c.receiver)
                        for c in e.sites
                    ),
                )
                for member, e in self.entries.items()
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "members": {
                member: {
                    "count": e.count,
                    "sites": [[c.caller, c.rel, c.line, c.receiver] for c in e.sites],
                }
                for member, e in sorted(self.entries.items())
            },
        }

    @classmethod
    def from_dict(cls, raw: Any) -> UnboundIndex | None:
        """The index a payload describes, or ``None`` when it is not one this version wrote."""
        try:
            if raw["version"] != FORMAT_VERSION:
                return None
            return cls({str(member): _entry(member, e) for member, e in raw["members"].items()})
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


def _entry(member: Any, raw: Any) -> UnboundEntry:
    """One persisted entry. A count below the sites it carries cannot have been written by
    :meth:`UnboundIndex.to_dict`, so it is a damaged file, not an answer."""
    sites = tuple(
        UnboundCall(str(caller), str(member), str(rel), int(line), str(receiver))
        for caller, rel, line, receiver in raw["sites"]
    )
    count = int(raw["count"])
    if count < len(sites):
        raise ValueError("count below its sites")
    return UnboundEntry(count, sites)
