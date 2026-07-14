"""Bounded, module-level overview of a Product Knowledge Graph (UI B4).

A repo's PKG can be large (10k+ nodes, 60k+ edges), so shipping the raw graph to
a browser is a non-starter. This aggregates it to a **module (file) level** view
that renders cheaply and reads at a glance: which modules are biggest, how they
depend on each other, the node/edge-kind mix, and the highest-degree symbols.
Everything is capped (``max_*``) and the totals record what was elided, so the UI
can honestly say "top N of M" rather than implying it showed everything.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from orchestrator.pkg.facts import FactBatch, Node


def _module_of(node: Node | None) -> str | None:
    return node.provenance.file if node and node.provenance else None


def build_overview(
    batch: FactBatch, *, max_modules: int = 40, max_module_edges: int = 60, max_symbols: int = 25
) -> dict[str, Any]:
    """Aggregate a ``FactBatch`` into a bounded, JSON-serialisable overview."""
    nodes = list(batch.nodes)
    edges = list(batch.edges)
    node_by_id = {n.id: n for n in nodes}

    kinds: dict[str, int] = defaultdict(int)
    mod_kind: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for n in nodes:
        kinds[n.kind.value] += 1
        f = _module_of(n)
        if f:
            mod_kind[f][n.kind.value] += 1

    edge_kinds: dict[str, int] = defaultdict(int)
    degree: dict[str, int] = defaultdict(int)
    mod_edge: dict[tuple[str, str, str], int] = defaultdict(int)
    for e in edges:
        edge_kinds[e.kind.value] += 1
        degree[e.src] += 1
        degree[e.dst] += 1
        sf = _module_of(node_by_id.get(e.src))
        df = _module_of(node_by_id.get(e.dst))
        if sf and df and sf != df:
            mod_edge[(sf, df, e.kind.value)] += 1

    # Sort on typed tuples first, then project to dicts (keeps mypy happy about
    # the mixed-value dicts, and the sort keys are unambiguously ints).
    mod_ranked = sorted(((f, sum(k.values()), dict(k)) for f, k in mod_kind.items()), key=lambda t: -t[1])
    modules = [{"module": f, "nodes": n, "by_kind": bk} for f, n, bk in mod_ranked]
    edge_ranked = sorted(((sf, df, k, c) for (sf, df, k), c in mod_edge.items()), key=lambda t: -t[3])
    module_edges = [{"src": sf, "dst": df, "kind": k, "count": c} for sf, df, k, c in edge_ranked]
    top_symbols = sorted((n for n in nodes if n.grounded), key=lambda n: -degree[n.id])[:max_symbols]

    return {
        "summary": {
            "nodes": len(nodes),
            "grounded_nodes": sum(1 for n in nodes if n.grounded),
            "external_nodes": sum(1 for n in nodes if n.external),
            "edges": len(edges),
        },
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "edge_kinds": dict(sorted(edge_kinds.items(), key=lambda kv: -kv[1])),
        "totals": {"modules": len(modules), "module_edges": len(module_edges)},
        "modules": modules[:max_modules],
        "module_edges": module_edges[:max_module_edges],
        "top_symbols": [
            {
                "id": n.id,
                "name": n.name,
                "kind": n.kind.value,
                "module": _module_of(n),
                "degree": degree[n.id],
            }
            for n in top_symbols
        ],
        "truncated": {
            "modules": len(modules) > max_modules,
            "module_edges": len(module_edges) > max_module_edges,
        },
    }


__all__ = ["build_overview"]
