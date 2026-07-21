"""In-memory query layer over a ``FactBatch`` — the grounded-retrieval surface.

This is the minimum an agent needs to ask the two questions that justify the
PKG: *"what calls X?"* and *"what does changing X touch?"* — and get answers
that point back to ``file:line``. v0 is in-memory; the same query shape later
backs a Postgres/graph store without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node


@dataclass(frozen=True)
class CallSite:
    """A caller and the line where the call happens."""

    caller: Node
    at: str  # "file:line"


class FactStore:
    """Indexed, read-only view over extracted facts."""

    def __init__(self, batch: FactBatch) -> None:
        self._nodes: dict[str, Node] = {n.id: n for n in batch.nodes}
        self._edges: list[Edge] = batch.edges

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def find(self, name: str) -> list[Node]:
        """Nodes whose short name matches (case-insensitive), grounded first."""
        hits = [n for n in self._nodes.values() if n.name.lower() == name.lower()]
        return sorted(hits, key=lambda n: (not n.grounded, n.id))

    def callers_of(self, node_id: str) -> list[CallSite]:
        """Who calls this node — with the call-site line."""
        out: list[CallSite] = []
        for e in self._edges:
            if e.kind is EdgeKind.CALLS and e.dst == node_id:
                caller = self._nodes.get(e.src)
                if caller is not None:
                    out.append(CallSite(caller, str(e.provenance)))
        return out

    def callees_of(self, node_id: str) -> list[Node]:
        """What this node calls."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.CALLS and e.src == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def children_of(self, node_id: str) -> list[Node]:
        """Direct CONTAINS children (module→types/functions, type→methods)."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.CONTAINS and e.src == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def edges_of_kind(self, kind: EdgeKind) -> list[Edge]:
        """Every edge of one kind, for callers that aggregate the whole graph.

        The per-node queries above answer "what touches X"; this answers "what does the
        graph look like", without each caller rescanning every edge per node.
        """
        return [e for e in self._edges if e.kind is kind]

    def parents_index(self) -> dict[str, str]:
        """child id → parent id, from every CONTAINS edge, in one pass.

        ``children_of`` only walks *down*. Resolving what a symbol belongs to means
        walking *up*, and doing that per-node would rescan every edge each time — so
        callers that need the upward direction build this index once.
        """
        return {e.dst: e.src for e in self._edges if e.kind is EdgeKind.CONTAINS}

    def imports_of(self, node_id: str) -> list[Node]:
        """What this module imports (IMPORTS out-edges) — the module-level ``callees_of``."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.IMPORTS and e.src == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def importers_of(self, node_id: str) -> list[Node]:
        """What imports this module (IMPORTS in-edges) — the module-level ``callers_of``.

        The other half of ``imports_of``: every dependency edge has to be answerable
        from both ends, or a reader can walk down the graph but never back up.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.IMPORTS and e.dst == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def touches(self, node_id: str) -> list[Node]:
        """Blast radius: every node directly connected to this one, either direction."""
        related: set[str] = set()
        for e in self._edges:
            if e.src == node_id:
                related.add(e.dst)
            elif e.dst == node_id:
                related.add(e.src)
        return [self._nodes[i] for i in sorted(related) if i in self._nodes]

    def impact_of(self, node_id: str, *, max_depth: int = 4) -> list[tuple[Node, int]]:
        """Transitive blast radius — every symbol that (transitively) calls this
        one, in BFS order with its hop distance. The "what breaks if I change X?"
        question the agent asks before touching a symbol.
        """
        from collections import deque

        seen = {node_id}
        out: list[tuple[Node, int]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            nid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for site in self.callers_of(nid):
                cid = site.caller.id
                if cid not in seen:
                    seen.add(cid)
                    out.append((site.caller, depth + 1))
                    queue.append((cid, depth + 1))
        return out

    def impact_across(
        self,
        node_id: str,
        *,
        kinds: tuple[EdgeKind, ...] = (EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.REFERENCES),
        max_depth: int = 4,
    ) -> list[tuple[Node, int]]:
        """Cross-layer transitive blast radius — every node that (transitively)
        depends on ``node_id`` via any of ``kinds``, in BFS order with hop
        distance.

        Where ``impact_of`` follows only CALLS (the code layer), this unions the
        *reverse* direction of several edge kinds — CALLS (callers), IMPORTS
        (importers), REFERENCES (data-layer dependents) — so a change traces
        across layers: change an entity → who references it → who imports that
        module → … A single reverse index over the requested kinds backs the
        walk (the per-node accessors would rescan every edge each hop).
        """
        from collections import deque

        kindset = set(kinds)
        predecessors: dict[str, list[str]] = {}
        for e in self._edges:
            if e.kind in kindset:
                predecessors.setdefault(e.dst, []).append(e.src)

        seen = {node_id}
        out: list[tuple[Node, int]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            nid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for src in predecessors.get(nid, ()):
                if src not in seen:
                    seen.add(src)
                    node = self._nodes.get(src)
                    if node is not None:
                        out.append((node, depth + 1))
                        queue.append((src, depth + 1))
        return out

    def references_of(self, entity_id: str) -> list[Node]:
        """Entities this one points at via a foreign key (REFERENCES out-edges)."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.REFERENCES and e.src == entity_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def dependents_of(self, entity_id: str) -> list[Node]:
        """Entities that point at this one via a foreign key (REFERENCES in-edges) —
        the data-layer analogue of ``callers_of``: who depends on this table.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.REFERENCES and e.dst == entity_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def summary(self) -> dict[str, int]:
        grounded = sum(1 for n in self._nodes.values() if n.grounded)
        return {
            "nodes": len(self._nodes),
            "grounded_nodes": grounded,
            "external_nodes": len(self._nodes) - grounded,
            "edges": len(self._edges),
        }


__all__ = ["CallSite", "FactStore"]
