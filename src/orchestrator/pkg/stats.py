"""Fact-graph statistics summary for the orchestrator.pkg package.

Provides :func:`summarise` which returns a :class:`GraphStats` dataclass
that counts nodes / edges by kind and surfaces the most-called functions.
No source re-parsing; operates purely on existing :class:`FactBatch` /
:class:`FactStore` data structures.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.store import CallSite, FactStore


@dataclass
class FunctionCallFrequency:
    """A function node and the number of distinct call-sites targeting it."""

    node_id: str
    name: str
    call_count: int


@dataclass
class GraphStats:
    """Aggregated statistics over an extracted fact graph.

    Attributes
    ----------
    node_counts:
        Mapping from :class:`~orchestrator.pkg.facts.NodeKind` to the number
        of nodes of that kind present in the graph.
    edge_counts:
        Mapping from :class:`~orchestrator.pkg.facts.EdgeKind` to the number
        of edges of that kind present in the graph.
    total_nodes:
        Convenience sum of all node counts.
    total_edges:
        Convenience sum of all edge counts.
    top_called_functions:
        List of :class:`FunctionCallFrequency` objects ordered descending by
        call count; only functions that have at least one caller are included.
    """

    node_counts: dict[NodeKind, int] = field(default_factory=dict)
    edge_counts: dict[EdgeKind, int] = field(default_factory=dict)
    total_nodes: int = 0
    total_edges: int = 0
    top_called_functions: list[FunctionCallFrequency] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover
        lines: list[str] = ["=== Fact-Graph Statistics ==="]

        lines.append(f"\nTotal nodes : {self.total_nodes}")
        for nkind in NodeKind:
            count = self.node_counts.get(nkind, 0)
            if count:
                lines.append(f"  {nkind.value:<12}: {count}")

        lines.append(f"\nTotal edges : {self.total_edges}")
        for ekind in EdgeKind:
            count = self.edge_counts.get(ekind, 0)
            if count:
                lines.append(f"  {ekind.value:<12}: {count}")

        if self.top_called_functions:
            lines.append("\nMost-called functions:")
            for rank, fcf in enumerate(self.top_called_functions, start=1):
                lines.append(f"  {rank:>3}. {fcf.name} ({fcf.node_id}) \u2014 {fcf.call_count} call(s)")

        return "\n".join(lines)


def summarise(
    batch: FactBatch,
    *,
    top_n: int = 10,
) -> GraphStats:
    """Generate a :class:`GraphStats` summary from a :class:`FactBatch`.

    Parameters
    ----------
    batch:
        The mutable fact collection produced by an extractor pass.
    top_n:
        How many most-called functions to include in
        :attr:`GraphStats.top_called_functions`.  Defaults to ``10``.

    Returns
    -------
    GraphStats
        A fully populated statistics object; no source files are read.
    """
    store = FactStore(batch)
    return summarise_store(store, top_n=top_n)


def summarise_store(
    store: FactStore,
    *,
    top_n: int = 10,
) -> GraphStats:
    """Generate a :class:`GraphStats` summary from an already-built :class:`FactStore`.

    Parameters
    ----------
    store:
        A read-only indexed view over extracted facts.
    top_n:
        How many most-called functions to include in
        :attr:`GraphStats.top_called_functions`.  Defaults to ``10``.

    Returns
    -------
    GraphStats
        A fully populated statistics object.
    """
    # --- node counts ---------------------------------------------------------
    node_counter: Counter[NodeKind] = Counter()
    for node in store.nodes:
        node_counter[node.kind] += 1

    node_counts: dict[NodeKind, int] = dict(node_counter)
    total_nodes: int = sum(node_counter.values())

    # --- edge counts ---------------------------------------------------------
    # FactStore exposes edges indirectly; we reconstruct via callers_of and the
    # public .nodes list.  However FactStore._edges is a private attribute, so
    # we derive edge statistics from the FactBatch directly if available.
    # Since summarise_store may be called with a FactStore that was *not* built
    # here, we use the standard public surface: iterate all nodes and query
    # relevant edge types through the public API.
    #
    # For a complete edge tally we rebuild a FactStore-aware counter by
    # inspecting callers_of for every function node (CALLS edges) and deriving
    # the remaining edge kinds from the FactStore's internal list which, while
    # private, is the only authoritative source — consistent with the BROWNFIELD
    # rule that we must not re-parse source or add a new schema.
    edge_counter: Counter[EdgeKind] = Counter()
    for edge in store._edges:  # noqa: SLF001  # FactStore has no public edges iterator
        edge_counter[edge.kind] += 1

    edge_counts: dict[EdgeKind, int] = dict(edge_counter)
    total_edges: int = sum(edge_counter.values())

    # --- most-called functions -----------------------------------------------
    call_counts: Counter[str] = Counter()
    function_nodes = [n for n in store.nodes if n.kind is NodeKind.FUNCTION]
    for fn_node in function_nodes:
        callers: list[CallSite] = store.callers_of(fn_node.id)
        if callers:
            call_counts[fn_node.id] = len(callers)

    top_called: list[FunctionCallFrequency] = []
    for node_id, count in call_counts.most_common(top_n):
        fn = store.node(node_id)
        name = fn.name if fn is not None else node_id
        top_called.append(FunctionCallFrequency(node_id=node_id, name=name, call_count=count))

    return GraphStats(
        node_counts=node_counts,
        edge_counts=edge_counts,
        total_nodes=total_nodes,
        total_edges=total_edges,
        top_called_functions=top_called,
    )


def median_call_count(functions: list[FunctionCallFrequency]) -> float:
    """Return the median ``call_count`` from *functions*.

    Parameters
    ----------
    functions:
        A list of :class:`FunctionCallFrequency` objects.  The list is not
        mutated.

    Returns
    -------
    float
        Median of the ``call_count`` values, or ``0.0`` for an empty list.
    """
    if not functions:
        return 0.0
    counts: list[int] = sorted(f.call_count for f in functions)
    mid: int = len(counts) // 2
    if len(counts) % 2 == 1:
        return float(counts[mid])
    return (counts[mid - 1] + counts[mid]) / 2.0
