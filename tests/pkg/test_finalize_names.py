"""pkg.finalize_names — the shared whole-repo name-resolution helper (perl-support-roadmap.md
§8.5). Perl's D10 is the first front-end written against it; these tests cover the helper
itself, independent of any one language.
"""

from __future__ import annotations

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.finalize_names import declared_ids, resolve_or_drop


def test_declared_ids_excludes_external_placeholders() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m.Real", NodeKind.TYPE, "Real", "python", Provenance("m.py", 1)))
    batch.add_node(Node("py:m.Fake", NodeKind.TYPE, "Fake", "python", external=True))
    assert declared_ids(batch) == {"py:m.Real"}


def test_resolve_or_drop_picks_the_first_grounded_candidate() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m.Second", NodeKind.FUNCTION, "Second", "python", Provenance("m.py", 1)))
    added = resolve_or_drop(
        batch,
        "py:m.caller",
        ["py:m.First", "py:m.Second"],
        EdgeKind.CALLS,
        Provenance("m.py", 5),
    )
    assert added is True
    assert Edge("py:m.caller", "py:m.Second", EdgeKind.CALLS, Provenance("m.py", 5)) in batch.edges


def test_resolve_or_drop_never_invents_when_nothing_grounds() -> None:
    batch = FactBatch()
    added = resolve_or_drop(batch, "py:m.caller", ["py:m.Nowhere"], EdgeKind.CALLS, Provenance("m.py", 5))
    assert added is False
    assert list(batch.edges) == []
    assert list(batch.nodes) == []  # no external placeholder invented either


def test_resolve_or_drop_accepts_a_precomputed_declared_set() -> None:
    """A caller resolving many candidates across one `finalize()` pass can compute
    `declared_ids(batch)` once and pass it in, instead of `resolve_or_drop` rescanning
    the whole node list on every call — found in review: Perl's own D10 pass didn't, and
    was genuinely quadratic in repo size. The precomputed set must behave identically to
    the default per-call recompute for the same batch."""
    batch = FactBatch()
    batch.add_node(Node("py:m.Second", NodeKind.FUNCTION, "Second", "python", Provenance("m.py", 1)))
    declared = declared_ids(batch)
    added = resolve_or_drop(
        batch,
        "py:m.caller",
        ["py:m.First", "py:m.Second"],
        EdgeKind.CALLS,
        Provenance("m.py", 5),
        declared=declared,
    )
    assert added is True
    assert Edge("py:m.caller", "py:m.Second", EdgeKind.CALLS, Provenance("m.py", 5)) in batch.edges
