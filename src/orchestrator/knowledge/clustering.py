"""Deterministic community detection over the area coupling graph (G5 phase 2).

Areas today are grouped by **zone**, which :func:`areas.zone_of` derives from the first
segments of a module's *name*. That answers "what did someone call this?". It does not answer
"what actually clusters?" — and where the two disagree, the disagreement is the interesting
part: a module sitting in a zone it has no structural relationship with is a naming decision
the dependency graph does not support.

This module answers the second question, from the ``coupling`` graph alone.

**Why label propagation, and why this variant.** The spec rules out Leiden/Louvain *as-is*
because their reference implementations shuffle node order and seed from a RNG — two runs on
an identical commit give different communities, and a picture that redraws differently for the
same input cannot be diffed or reviewed (invariant #3). Label propagation has the same defect
in its textbook form and the same fix, but the fix is small enough to be obviously correct:

* nodes are visited in sorted name order, never shuffled;
* initial labels are assigned by sorted position, not randomly;
* a node adopts the neighbouring label with the greatest summed edge weight, and **ties break
  toward the lexicographically smallest label** rather than arbitrarily;
* iteration is capped, so a pathological oscillation terminates instead of hanging;
* communities are finally **renumbered by their smallest member name**, so ids depend on the
  graph and not on the order in which the algorithm happened to discover them.

That last step is what makes the output byte-stable. Without it, adding one unrelated node can
renumber every community and produce a diff that looks like an architectural change and isn't.

The graph is treated as **undirected** for clustering: ``a → b`` and ``b → a`` are the same
evidence that two areas belong together. Direction still matters for the *picture* — it is
what makes a dependency arrow point somewhere — but not for membership.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

MAX_ITERATIONS = 20
"""Cap on propagation passes. Convergence is typically 3-5; the cap bounds the pathological
case where two labels trade places forever. Reaching it is not an error — the partition at
that point is still deterministic, just not necessarily stable."""


def _undirected(edges: Mapping[tuple[str, str], int]) -> dict[str, dict[str, int]]:
    """Symmetric weighted adjacency. Self-loops are dropped — they carry no grouping signal."""
    adj: dict[str, dict[str, int]] = defaultdict(dict)
    for (src, dst), weight in edges.items():
        if src == dst:
            continue
        adj[src][dst] = adj[src].get(dst, 0) + weight
        adj[dst][src] = adj[dst].get(src, 0) + weight
    return adj


def significant_edges(
    edges: Mapping[tuple[str, str], int],
) -> tuple[dict[tuple[str, str], int], float]:
    """Drop weak couplings before clustering, and say what the cut was.

    Returns ``(kept, threshold)``. **The caller is expected to report the threshold and the
    drop count** — this is a bounded view of the coupling graph and invariant #7 says a
    bounded view never implies completeness.

    The cut is the **mean edge weight**, which is a property of the graph rather than a tuned
    constant: coupling weights are heavily skewed (a handful of areas import each other
    constantly, a long tail touch once), so the mean sits above the median and separates the
    two populations without being fitted to any one repo.

    Measured on this repo, why it is worth doing at all: clustering the raw graph puts 29 of
    40 production areas into a single community at modularity 0.245 — a partition that
    technically exists and explains nothing. One incidental import is not evidence that two
    areas belong together, but there are enough of them to fuse everything into a blob. At the
    mean cut it becomes communities of 11 / 9 / 2 at 0.363.
    """
    if not edges:
        return ({}, 0.0)
    weights = list(edges.values())
    threshold = sum(weights) / len(weights)
    return ({pair: w for pair, w in edges.items() if w >= threshold}, threshold)


def detect_communities(
    edges: Mapping[tuple[str, str], int],
    *,
    nodes: list[str] | None = None,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, int]:
    """Group nodes into communities. Same graph in → same numbering out, always.

    ``edges`` maps ``(from, to)`` to a weight. ``nodes`` optionally names the full node set so
    that nodes with no edges still get a community of their own; without it, only nodes that
    appear in an edge are grouped.

    Returns ``{node: community_id}``, ids being consecutive from 0 and ordered by each
    community's alphabetically-first member.
    """
    adj = _undirected(edges)
    universe = sorted(set(nodes) if nodes is not None else set(adj))
    if not universe:
        return {}

    # Seed labels by sorted position — deterministic, and distinct per node.
    label = {name: i for i, name in enumerate(universe)}

    for _ in range(max_iterations):
        changed = False
        for name in universe:  # sorted order, never shuffled
            neighbours = adj.get(name)
            if not neighbours:
                continue
            weight_by_label: dict[int, int] = defaultdict(int)
            for other, weight in neighbours.items():
                if other in label:
                    weight_by_label[label[other]] += weight
            if not weight_by_label:
                continue
            # Greatest total weight wins; ties break to the smallest label id, which is
            # itself derived from sorted node order — so the whole chain is name-determined.
            best = min(weight_by_label.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best != label[name]:
                label[name] = best
                changed = True
        if not changed:
            break

    # Renumber by each community's alphabetically-first member, so ids describe the graph
    # rather than the discovery order. This is what keeps successive reports diffable.
    members: dict[int, list[str]] = defaultdict(list)
    for name in universe:
        members[label[name]].append(name)
    order = sorted(members.values(), key=lambda group: min(group))
    return {name: cid for cid, group in enumerate(order) for name in sorted(group)}


def communities_by_id(assignment: Mapping[str, int]) -> list[list[str]]:
    """``{node: cid}`` → a list indexed by community id, each a sorted member list."""
    grouped: dict[int, list[str]] = defaultdict(list)
    for name, cid in assignment.items():
        grouped[cid].append(name)
    return [sorted(grouped[cid]) for cid in sorted(grouped)]


def modularity(edges: Mapping[tuple[str, str], int], assignment: Mapping[str, int]) -> float:
    """Newman modularity of a partition — how much better than chance the grouping is.

    Roughly: >0.3 is meaningful structure, ~0 means the partition says nothing. Reported so a
    reader can tell a real clustering from an arbitrary one, rather than trusting the picture.
    Computed on the undirected projection, matching what :func:`detect_communities` clusters.
    """
    adj = _undirected(edges)
    two_m = sum(w for nbrs in adj.values() for w in nbrs.values())
    if two_m == 0:
        return 0.0

    # Per-community form: Q = Σ_c [ Σ_in(c)/2m − (Σ_tot(c)/2m)² ].
    #
    # The pairwise form is easy to get wrong here: the null-model term k_i·k_j/2m has to be
    # summed over *every* pair in a community, not only the adjacent ones. Summing it over
    # adjacent pairs alone reports strong modularity for the single-community partition,
    # which by definition must score exactly 0 — caught by
    # ``test_modularity_distinguishes_real_structure_from_a_flat_partition``.
    inside: dict[int, float] = defaultdict(float)
    total_degree: dict[int, float] = defaultdict(float)
    for node, nbrs in adj.items():
        cid = assignment.get(node)
        if cid is None:
            continue
        total_degree[cid] += sum(nbrs.values())
        for other, weight in nbrs.items():
            if assignment.get(other) == cid:
                inside[cid] += weight  # each undirected edge is seen from both ends

    return sum(inside[cid] / two_m - (total_degree[cid] / two_m) ** 2 for cid in total_degree)


__all__ = [
    "MAX_ITERATIONS",
    "communities_by_id",
    "detect_communities",
    "modularity",
    "significant_edges",
]
