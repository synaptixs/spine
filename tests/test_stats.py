"""Tests for orchestrator.pkg.stats module."""

from __future__ import annotations

from orchestrator.pkg.facts import (
    Edge,
    EdgeKind,
    FactBatch,
    Node,
    NodeKind,
)
from orchestrator.pkg.stats import (
    FunctionCallFrequency,
    GraphStats,
    median_call_count,
    summarise,
    summarise_store,
)
from orchestrator.pkg.store import FactStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(node_id: str, name: str, kind: NodeKind) -> Node:
    return Node(id=node_id, name=name, kind=kind)


def _make_edge(src: str, dst: str, kind: EdgeKind) -> Edge:
    return Edge(src=src, dst=dst, kind=kind)


def _make_batch(
    nodes: list[Node] | None = None,
    edges: list[Edge] | None = None,
) -> FactBatch:
    batch = FactBatch()
    for n in nodes or []:
        batch.add_node(n)
    for e in edges or []:
        batch.add_edge(e)
    return batch


# ---------------------------------------------------------------------------
# FunctionCallFrequency dataclass
# ---------------------------------------------------------------------------


class TestFunctionCallFrequency:
    def test_fields(self) -> None:
        fcf = FunctionCallFrequency(node_id="n1", name="foo", call_count=3)
        assert fcf.node_id == "n1"
        assert fcf.name == "foo"
        assert fcf.call_count == 3


# ---------------------------------------------------------------------------
# GraphStats dataclass defaults
# ---------------------------------------------------------------------------


class TestGraphStatsDefaults:
    def test_empty_defaults(self) -> None:
        gs = GraphStats()
        assert gs.node_counts == {}
        assert gs.edge_counts == {}
        assert gs.total_nodes == 0
        assert gs.total_edges == 0
        assert gs.top_called_functions == []


# ---------------------------------------------------------------------------
# summarise – empty batch
# ---------------------------------------------------------------------------


class TestSummariseEmptyBatch:
    def test_empty_batch_returns_graph_stats(self) -> None:
        batch = _make_batch()
        stats = summarise(batch)
        assert isinstance(stats, GraphStats)

    def test_empty_batch_zero_totals(self) -> None:
        stats = summarise(_make_batch())
        assert stats.total_nodes == 0
        assert stats.total_edges == 0

    def test_empty_batch_no_top_called(self) -> None:
        stats = summarise(_make_batch())
        assert stats.top_called_functions == []


# ---------------------------------------------------------------------------
# summarise – node counts
# ---------------------------------------------------------------------------


class TestSummariseNodeCounts:
    def _build_stats(self) -> GraphStats:
        nodes = [
            _make_node("fn1", "alpha", NodeKind.FUNCTION),
            _make_node("fn2", "beta", NodeKind.FUNCTION),
            _make_node("mod1", "my_module", NodeKind.MODULE),
        ]
        return summarise(_make_batch(nodes=nodes))

    def test_total_nodes(self) -> None:
        assert self._build_stats().total_nodes == 3

    def test_function_node_count(self) -> None:
        stats = self._build_stats()
        assert stats.node_counts.get(NodeKind.FUNCTION) == 2

    def test_module_node_count(self) -> None:
        stats = self._build_stats()
        assert stats.node_counts.get(NodeKind.MODULE) == 1

    def test_absent_kind_not_in_counts(self) -> None:
        stats = self._build_stats()
        # Find a NodeKind that was not added (neither FUNCTION nor MODULE)
        absent = next(
            (k for k in NodeKind if k not in (NodeKind.FUNCTION, NodeKind.MODULE)),
            None,
        )
        if absent is not None:
            assert stats.node_counts.get(absent, 0) == 0


# ---------------------------------------------------------------------------
# summarise – edge counts
# ---------------------------------------------------------------------------


class TestSummariseEdgeCounts:
    def _build_stats(self) -> GraphStats:
        nodes = [
            _make_node("fn1", "caller", NodeKind.FUNCTION),
            _make_node("fn2", "callee", NodeKind.FUNCTION),
            _make_node("mod1", "m", NodeKind.MODULE),
        ]
        edges = [
            _make_edge("fn1", "fn2", EdgeKind.CALLS),
            _make_edge("mod1", "fn1", EdgeKind.CONTAINS),
            _make_edge("mod1", "fn2", EdgeKind.CONTAINS),
        ]
        return summarise(_make_batch(nodes=nodes, edges=edges))

    def test_total_edges(self) -> None:
        assert self._build_stats().total_edges == 3

    def test_calls_edge_count(self) -> None:
        assert self._build_stats().edge_counts.get(EdgeKind.CALLS) == 1

    def test_contains_edge_count(self) -> None:
        assert self._build_stats().edge_counts.get(EdgeKind.CONTAINS) == 2


# ---------------------------------------------------------------------------
# summarise – top_called_functions
# ---------------------------------------------------------------------------


class TestSummariseTopCalledFunctions:
    def _build_stats(self, top_n: int = 10) -> GraphStats:
        nodes = [
            _make_node("fn1", "popular", NodeKind.FUNCTION),
            _make_node("fn2", "middle", NodeKind.FUNCTION),
            _make_node("fn3", "rare", NodeKind.FUNCTION),
            _make_node("c1", "caller1", NodeKind.FUNCTION),
            _make_node("c2", "caller2", NodeKind.FUNCTION),
            _make_node("c3", "caller3", NodeKind.FUNCTION),
        ]
        edges = [
            # fn1 called 3 times
            _make_edge("c1", "fn1", EdgeKind.CALLS),
            _make_edge("c2", "fn1", EdgeKind.CALLS),
            _make_edge("c3", "fn1", EdgeKind.CALLS),
            # fn2 called 2 times
            _make_edge("c1", "fn2", EdgeKind.CALLS),
            _make_edge("c2", "fn2", EdgeKind.CALLS),
            # fn3 called 1 time
            _make_edge("c1", "fn3", EdgeKind.CALLS),
        ]
        return summarise(_make_batch(nodes=nodes, edges=edges), top_n=top_n)

    def test_top_called_is_list(self) -> None:
        assert isinstance(self._build_stats().top_called_functions, list)

    def test_top_called_length(self) -> None:
        stats = self._build_stats()
        assert len(stats.top_called_functions) == 3

    def test_top_called_ordered_descending(self) -> None:
        top = self._build_stats().top_called_functions
        counts = [f.call_count for f in top]
        assert counts == sorted(counts, reverse=True)

    def test_top_called_first_is_most_popular(self) -> None:
        top = self._build_stats().top_called_functions
        assert top[0].name == "popular"
        assert top[0].call_count == 3

    def test_top_n_limits_results(self) -> None:
        stats = self._build_stats(top_n=2)
        assert len(stats.top_called_functions) <= 2

    def test_top_n_zero_returns_empty(self) -> None:
        stats = self._build_stats(top_n=0)
        assert stats.top_called_functions == []

    def test_functions_with_no_callers_excluded(self) -> None:
        nodes = [
            _make_node("fn_uncalled", "lonely", NodeKind.FUNCTION),
        ]
        stats = summarise(_make_batch(nodes=nodes))
        assert stats.top_called_functions == []

    def test_function_call_frequency_fields(self) -> None:
        top = self._build_stats().top_called_functions
        first = top[0]
        assert first.node_id == "fn1"
        assert first.name == "popular"
        assert first.call_count == 3


# ---------------------------------------------------------------------------
# summarise_store
# ---------------------------------------------------------------------------


class TestSummariseStore:
    def test_returns_graph_stats(self) -> None:
        batch = _make_batch()
        store = FactStore(batch)
        result = summarise_store(store)
        assert isinstance(result, GraphStats)

    def test_consistent_with_summarise(self) -> None:
        nodes = [
            _make_node("fn1", "foo", NodeKind.FUNCTION),
            _make_node("fn2", "bar", NodeKind.FUNCTION),
        ]
        edges = [
            _make_edge("fn1", "fn2", EdgeKind.CALLS),
        ]
        batch = _make_batch(nodes=nodes, edges=edges)
        stats_a = summarise(batch)
        store = FactStore(batch)
        stats_b = summarise_store(store)

        assert stats_a.total_nodes == stats_b.total_nodes
        assert stats_a.total_edges == stats_b.total_edges
        assert stats_a.node_counts == stats_b.node_counts
        assert stats_a.edge_counts == stats_b.edge_counts

    def test_top_n_forwarded(self) -> None:
        nodes = [
            _make_node("fn1", "a", NodeKind.FUNCTION),
            _make_node("fn2", "b", NodeKind.FUNCTION),
            _make_node("c1", "c", NodeKind.FUNCTION),
        ]
        edges = [
            _make_edge("c1", "fn1", EdgeKind.CALLS),
            _make_edge("c1", "fn2", EdgeKind.CALLS),
        ]
        batch = _make_batch(nodes=nodes, edges=edges)
        store = FactStore(batch)
        stats = summarise_store(store, top_n=1)
        assert len(stats.top_called_functions) <= 1


# ---------------------------------------------------------------------------
# median_call_count
# ---------------------------------------------------------------------------


class TestMedianCallCount:
    def _fcf(self, count: int) -> FunctionCallFrequency:
        return FunctionCallFrequency(node_id="x", name="x", call_count=count)

    def test_empty_list_returns_zero(self) -> None:
        assert median_call_count([]) == 0.0

    def test_single_element(self) -> None:
        assert median_call_count([self._fcf(7)]) == 7.0

    def test_odd_number_of_elements(self) -> None:
        funcs = [self._fcf(c) for c in [1, 3, 5]]
        assert median_call_count(funcs) == 3.0

    def test_even_number_of_elements(self) -> None:
        funcs = [self._fcf(c) for c in [1, 3, 5, 7]]
        assert median_call_count(funcs) == 4.0

    def test_unsorted_input(self) -> None:
        funcs = [self._fcf(c) for c in [5, 1, 3]]
        assert median_call_count(funcs) == 3.0

    def test_does_not_mutate_input(self) -> None:
        funcs = [self._fcf(c) for c in [5, 1, 3]]
        original_order = [f.call_count for f in funcs]
        median_call_count(funcs)
        assert [f.call_count for f in funcs] == original_order

    def test_returns_float(self) -> None:
        result = median_call_count([self._fcf(4)])
        assert isinstance(result, float)

    def test_even_returns_average_of_middle_two(self) -> None:
        funcs = [self._fcf(c) for c in [2, 4]]
        assert median_call_count(funcs) == 3.0

    def test_two_identical_elements(self) -> None:
        funcs = [self._fcf(6), self._fcf(6)]
        assert median_call_count(funcs) == 6.0

    def test_large_list(self) -> None:
        counts = list(range(1, 101))  # 1..100, median = 50.5
        funcs = [self._fcf(c) for c in counts]
        assert median_call_count(funcs) == 50.5
