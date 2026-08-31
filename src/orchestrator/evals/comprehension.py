"""G6 comprehension metrics — is the graph's *answer* right, not just its shape.

Everything gated before this asked *"is the graph correct?"* against fixtures we wrote. This
asks whether a fact opens to the place it claims, on real repositories nobody here controls.

**Provenance validity is not what the spec expected.** The G6 record proposed
``GroundingVerifier.stale_findings`` as "most of it". It cannot be: staleness compares a graph
against the source it was extracted from, so on a freshly-extracted tree it is **zero by
construction** — a metric that reports a constant regardless of the code is not a measurement.

What is checkable, and what this scores: **does the recorded line actually name the symbol?**
Every grounded fact carries ``file:line`` and the product's central claim is that a reader can
open it. That is falsifiable per fact, deterministic, needs no labelling, and it moves — an
off-by-one in a front-end, a decorator mis-attribution, or a line recorded against the wrong
file all show up here.

**Scored only for kinds named by a token at the site.** Measured on this repository while the
metric was being written:

===========  ==========  ==================================================================
Kind         Rate        Why it is in or out
===========  ==========  ==================================================================
Function     1.000       in — the name appears at the definition
Type         1.000       in
Field        1.000       in
Module       0.151       **out** — named by a dotted path, never text on line 1
Endpoint     0.014       **out** — named ``GET /v1/x``, not a token in the source
Entity       0.000       **out** — named for the table it maps, not the line it sits on
===========  ==========  ==================================================================

Scoring the bottom three would measure a naming convention and call it provenance. They are
excluded by name and counted separately, because "excluded" and "passed" must not look alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from orchestrator.pkg.facts import FactBatch, NodeKind

#: Kinds whose name is expected to appear at their recorded line. See the module docstring —
#: this list is the metric's definition, and widening it without checking would turn a naming
#: convention into a fake regression.
ANCHORED_KINDS: frozenset[NodeKind] = frozenset({NodeKind.FUNCTION, NodeKind.TYPE, NodeKind.FIELD})


@dataclass(frozen=True)
class ProvenanceReport:
    """How many anchored facts open to a line that names them."""

    anchored: int
    resolved: int
    excluded: int
    unreadable: int

    @property
    def measured(self) -> bool:
        """False when there was nothing of an anchored kind — a zero that says nothing."""
        return self.anchored > 0

    @property
    def rate(self) -> Fraction | None:
        """Exact, from the integer counts. Floats are for display only."""
        return Fraction(self.resolved, self.anchored) if self.anchored else None


def score_provenance(batch: FactBatch, root: Path | str) -> ProvenanceReport:
    """Fraction of anchored facts whose recorded line contains their name.

    Deterministic and no-LLM. Reads each file once; a file that cannot be read counts as
    ``unreadable`` rather than as a failure, so a permissions problem is legible instead of
    arriving as a provenance regression.
    """
    root_path = Path(root)
    anchored = resolved = excluded = unreadable = 0
    cache: dict[str, list[str] | None] = {}

    for node in batch.nodes:
        if not node.grounded or node.provenance is None:
            continue
        if node.kind not in ANCHORED_KINDS:
            excluded += 1
            continue
        anchored += 1
        rel = node.provenance.file
        if rel not in cache:
            path = root_path / rel
            try:
                cache[rel] = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[rel] = None
        lines = cache[rel]
        if lines is None:
            unreadable += 1
            continue
        index = node.provenance.line - 1
        # The final segment: ids are qualified (`py:pkg.mod.Class.method`) and the source line
        # carries the bare name. Comparing the qualified form would fail every language.
        leaf = node.name.rsplit(".", 1)[-1]
        if 0 <= index < len(lines) and leaf in lines[index]:
            resolved += 1

    return ProvenanceReport(anchored=anchored, resolved=resolved, excluded=excluded, unreadable=unreadable)


__all__ = ["ANCHORED_KINDS", "ProvenanceReport", "score_provenance"]
