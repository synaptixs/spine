"""Shared whole-repo name-resolution helper for a front-end's ``finalize()`` pass.

Once every file in a repo is extracted, a few front-ends need to repoint or drop a
per-file *guess* with knowledge no single file has: C# repoints a guessed base type,
PHP repoints a guessed ``new X()`` target, and Perl's D10 (perl-support-roadmap.md §3.2)
resolves a bare call through a literal ``@EXPORT`` or ``@ISA`` chain. Same shape, three call
sites — try each candidate target id against what actually *grounded* in the final batch,
in priority order; the first match wins; no match means **drop**, never invent.

Perl's D10 is the first front-end written against this (perl-support-roadmap.md §8.5); C#
and PHP keep their own inline versions for now and migrate here when next touched, per the
same section's exit note. Deliberately minimal: this is the mechanical "check what grounded,
apply a drop policy" piece, not the language-specific "what are the candidates" logic, which
stays in each front-end (it knows its own call shapes; this doesn't need to).
"""

from __future__ import annotations

from collections.abc import Iterable

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Provenance


def declared_ids(batch: FactBatch) -> frozenset[str]:
    """Every id with a grounded (non-external) node in the batch."""
    return frozenset(n.id for n in batch.nodes if n.grounded)


def resolve_or_drop(
    batch: FactBatch,
    src: str,
    candidates: Iterable[str],
    kind: EdgeKind,
    provenance: Provenance,
    *,
    declared: frozenset[str] | None = None,
) -> bool:
    """Add ``src --kind--> candidate`` for the first candidate already grounded in
    ``batch``, in the order given; add nothing if none grounded. Returns whether an edge
    was added.

    Never invents an external placeholder for the candidates that missed — a method-shaped
    target has no backstop (docs/reviewing/language-frontend-checklist.md: "a guessed
    method id has no backstop and must not be emitted unverified"). The caller decides what
    counts as a candidate and in what order; this only decides whether one already exists.

    ``declared`` lets a caller that resolves *many* candidates across one ``finalize()``
    pass compute ``declared_ids(batch)`` once and reuse it, rather than this function
    rescanning the whole (and only growing) node list on every single call — found in
    review: Perl's own D10 pass rebuilt it per bare-call resolution attempt, genuinely
    quadratic in repo size (800 classes × 25 subs measured at ~49.5s). Left as an optional
    keyword, defaulting to the original per-call recompute, so an existing or future caller
    that (unlike Perl's finalize pass) adds grounded nodes mid-resolution keeps seeing them
    without opting in to anything.
    """
    if declared is None:
        declared = declared_ids(batch)
    for candidate in candidates:
        if candidate in declared:
            batch.add_edge(Edge(src, candidate, kind, provenance))
            return True
    return False


__all__ = ["declared_ids", "resolve_or_drop"]
