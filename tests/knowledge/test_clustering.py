"""Deterministic community detection.

The determinism tests carry the weight here. The algorithm is only usable because it is
reproducible — a clustering that shifts between runs on an identical commit produces a picture
that cannot be diffed, which is the whole reason Leiden/Louvain were ruled out as-is.
"""

from __future__ import annotations

from collections import Counter

from orchestrator.knowledge.clustering import (
    communities_by_id,
    detect_communities,
    modularity,
    significant_edges,
)


def _two_clusters() -> Counter[tuple[str, str]]:
    """Two triangles joined by a single weak edge — an unambiguous two-community graph."""
    c: Counter[tuple[str, str]] = Counter()
    for a, b in [("a1", "a2"), ("a2", "a3"), ("a3", "a1")]:
        c[(a, b)] = 10
    for a, b in [("b1", "b2"), ("b2", "b3"), ("b3", "b1")]:
        c[(a, b)] = 10
    c[("a1", "b1")] = 1  # the bridge
    return c


def test_finds_the_two_obvious_communities() -> None:
    groups = communities_by_id(detect_communities(_two_clusters()))
    assert groups == [["a1", "a2", "a3"], ["b1", "b2", "b3"]]


def test_is_identical_across_runs() -> None:
    first = detect_communities(_two_clusters())
    for _ in range(5):
        assert detect_communities(_two_clusters()) == first


def test_insertion_order_does_not_change_the_result() -> None:
    """A Counter built in a different order is the same graph and must cluster the same.

    This is the failure mode the sorted iteration exists to prevent: dict order leaking into
    the output would make the picture depend on extraction order rather than on the code.
    """
    forward = _two_clusters()
    reversed_order: Counter[tuple[str, str]] = Counter()
    for key in reversed(list(forward)):
        reversed_order[key] = forward[key]
    assert detect_communities(reversed_order) == detect_communities(forward)


def test_community_ids_are_ordered_by_first_member() -> None:
    """Ids describe the graph, not the discovery order — so they stay put across commits."""
    assignment = detect_communities(_two_clusters())
    assert assignment["a1"] == 0
    assert assignment["b1"] == 1


def test_an_unrelated_new_node_does_not_renumber_existing_communities() -> None:
    """The renumbering guarantee that makes successive reports diffable: adding `z` must not
    shuffle ids and make an unchanged architecture look like it moved."""
    before = detect_communities(_two_clusters())
    after_graph = _two_clusters()
    after_graph[("z1", "z2")] = 5
    after = detect_communities(after_graph)
    assert {n: after[n] for n in before} == before


def test_direction_does_not_affect_membership() -> None:
    """`a → b` and `b → a` are the same evidence that two areas belong together."""
    forward: Counter[tuple[str, str]] = Counter({("x", "y"): 3, ("y", "z"): 3})
    backward: Counter[tuple[str, str]] = Counter({("y", "x"): 3, ("z", "y"): 3})
    assert detect_communities(forward) == detect_communities(backward)


def test_isolated_nodes_get_their_own_community_when_named() -> None:
    graph: Counter[tuple[str, str]] = Counter({("x", "y"): 1})
    assignment = detect_communities(graph, nodes=["x", "y", "lonely"])
    assert assignment["lonely"] not in {assignment["x"], assignment["y"]}
    assert sorted(assignment) == ["lonely", "x", "y"]


def test_self_loops_are_ignored() -> None:
    """An area importing itself says nothing about which group it belongs to."""
    with_loop: Counter[tuple[str, str]] = Counter({("x", "x"): 99, ("x", "y"): 1})
    without: Counter[tuple[str, str]] = Counter({("x", "y"): 1})
    assert detect_communities(with_loop) == detect_communities(without)


def test_empty_graph_is_handled() -> None:
    assert detect_communities(Counter()) == {}
    assert modularity(Counter(), {}) == 0.0


def test_modularity_distinguishes_real_structure_from_a_flat_partition() -> None:
    """The number exists so a reader can tell a meaningful clustering from an arbitrary one."""
    graph = _two_clusters()
    good = modularity(graph, detect_communities(graph))
    everything_together = modularity(graph, dict.fromkeys(detect_communities(graph), 0))
    assert good > 0.3, "two triangles joined by one edge is strong structure"
    assert everything_together == 0.0, "one community explains nothing beyond chance"


def test_terminates_on_an_oscillating_graph() -> None:
    """A symmetric ring can trade labels forever; the iteration cap must stop it."""
    ring: Counter[tuple[str, str]] = Counter()
    names = [f"n{i}" for i in range(8)]
    for i, name in enumerate(names):
        ring[(name, names[(i + 1) % len(names)])] = 1
    assignment = detect_communities(ring, max_iterations=3)
    assert sorted(assignment) == sorted(names)
    assert assignment == detect_communities(ring, max_iterations=3)


def test_significant_edges_cuts_at_the_mean_and_reports_it() -> None:
    """The threshold comes from the graph, not a tuned constant — and is returned so the
    caller can say what was dropped (invariant #7: a bounded view never implies completeness)."""
    graph: Counter[tuple[str, str]] = Counter({("a", "b"): 10, ("c", "d"): 1, ("e", "f"): 1})
    kept, threshold = significant_edges(graph)
    assert threshold == 4.0
    assert kept == {("a", "b"): 10}


def test_significant_edges_handles_an_empty_graph() -> None:
    assert significant_edges(Counter()) == ({}, 0.0)


def test_significant_edges_keeps_a_uniform_graph_whole() -> None:
    """When every edge has the same weight there is no weak tail to cut, and the mean must
    not silently discard the entire graph."""
    graph: Counter[tuple[str, str]] = Counter({("a", "b"): 3, ("c", "d"): 3})
    kept, threshold = significant_edges(graph)
    assert threshold == 3.0
    assert kept == dict(graph)


def test_filtering_then_clustering_beats_the_raw_graph_on_a_blob() -> None:
    """The reason significant_edges exists: incidental single edges fuse real communities
    into one lump that is technically a partition and explains nothing."""
    graph: Counter[tuple[str, str]] = Counter()
    for a, b in [("a1", "a2"), ("a2", "a3"), ("a3", "a1")]:
        graph[(a, b)] = 20
    for a, b in [("b1", "b2"), ("b2", "b3"), ("b3", "b1")]:
        graph[(a, b)] = 20
    for pair in [("a1", "b1"), ("a2", "b2"), ("a3", "b3")]:
        graph[pair] = 1  # incidental cross-links

    raw = detect_communities(graph)
    kept, _ = significant_edges(graph)
    filtered = detect_communities(kept, nodes=sorted({n for e in graph for n in e}))

    assert modularity(kept, filtered) > modularity(graph, raw)
    assert communities_by_id(filtered) == [["a1", "a2", "a3"], ["b1", "b2", "b3"]]
