"""In-memory query layer over a ``FactBatch`` — the grounded-retrieval surface.

This is the minimum an agent needs to ask the two questions that justify the
PKG: *"what calls X?"* and *"what does changing X touch?"* — and get answers
that point back to ``file:line``. v0 is in-memory; the same query shape later
backs a Postgres/graph store without changing callers.
"""

from __future__ import annotations

from collections import Counter
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

    def exposers_of(self, node_id: str) -> list[Node]:
        """Endpoints that route to this node — the callers that aren't in the language.

        Nothing in the source *calls* an HTTP handler; the framework does, at runtime,
        through a decorator or an attribute. So a handler's inbound ``CALLS`` set is
        genuinely empty, and reading that as "nothing depends on this" is the most
        dangerous answer the graph can give: the dependents of ``GET /v1/runs`` are its
        clients, outside the repo entirely.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.EXPOSES and e.dst == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def impact_of(self, node_id: str, *, max_depth: int = 4) -> list[tuple[Node, int]]:
        """Transitive blast radius — every symbol that (transitively) calls this
        one, in BFS order with its hop distance. The "what breaks if I change X?"
        question the agent asks before touching a symbol.

        ``EXPOSES`` counts as an inbound edge here, so a route handler reports the
        endpoints it serves rather than nothing at all. It is followed in the same walk
        as ``CALLS``, which keeps the transitive property honest: an endpoint shows up
        in the blast radius of everything its handler calls, at the right hop distance,
        not only when you ask about the handler itself.
        """
        from collections import deque

        seen = {node_id}
        out: list[tuple[Node, int]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            nid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            inbound = [site.caller for site in self.callers_of(nid)] + self.exposers_of(nid)
            for node in inbound:
                if node.id not in seen:
                    seen.add(node.id)
                    out.append((node, depth + 1))
                    queue.append((node.id, depth + 1))
        return out

    def impact_across(
        self,
        node_id: str,
        *,
        kinds: tuple[EdgeKind, ...] = (
            EdgeKind.CALLS,
            EdgeKind.IMPORTS,
            EdgeKind.REFERENCES,
            EdgeKind.EXPOSES,
        ),
        max_depth: int = 4,
    ) -> list[tuple[Node, int]]:
        """Cross-layer transitive blast radius — every node that (transitively)
        depends on ``node_id`` via any of ``kinds``, in BFS order with hop
        distance.

        Where ``impact_of`` follows CALLS and EXPOSES (the code layer plus its HTTP
        surface), this unions the *reverse* direction of several edge kinds — CALLS
        (callers), IMPORTS (importers), REFERENCES (data-layer dependents), EXPOSES
        (the endpoint that routes to a handler) — so a change traces across layers:
        change an entity → who references it → who imports that module → … A single
        reverse index over the requested kinds backs the walk (the per-node accessors
        would rescan every edge each hop).

        ``EXPOSES`` is in the default set because leaving it out is what let a public
        API change score as zero-impact. A caller that wants the old, code-only
        reading passes ``kinds`` explicitly — ``sdlc/coverage.py`` already does.
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

    def implementors_of(self, type_id: str) -> list[Node]:
        """Types that extend or implement this one (incoming ``IMPLEMENTS`` edges).

        "What implements this interface?" is a core comprehension question, and the
        answer is a lookup the graph has always been able to serve — 42 edges sit in
        click's graph alone. The counterpart to :meth:`implements_of`; render both and
        a reader can walk a hierarchy in either direction.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.IMPLEMENTS and e.dst == type_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def implements_of(self, type_id: str) -> list[Node]:
        """Base types this one extends or implements (outgoing ``IMPLEMENTS`` edges)."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.IMPLEMENTS and e.src == type_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def docs_for(self, symbol_id: str) -> list[Node]:
        """The ``Doc`` pages that describe a symbol (incoming ``MENTIONS`` edges) — "which docs
        talk about this?" (empty until docs are ingested; see ``pkg.doc_link``)."""
        ids = [e.src for e in self._edges if e.kind is EdgeKind.MENTIONS and e.dst == symbol_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def mentions_of(self, doc_id: str) -> list[Node]:
        """The code symbols a ``Doc`` page names (outgoing ``MENTIONS`` edges)."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.MENTIONS and e.src == doc_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def summary(self) -> dict[str, int]:
        """Counts of what was extracted, including edges **per kind**.

        One total is not enough to notice a front-end that stopped emitting something.
        ``edges: 31073`` reads identically whether the call graph resolved or collapsed to
        zero while imports doubled — and a kind that silently goes missing is exactly the
        completeness failure ``pkg verify`` exists for, arriving from the other direction:
        the edge is not dangling, it was simply never emitted. A per-kind line makes
        ``REFERENCES: 0`` on a repo with entities something you can see.

        Kinds with no edges are included rather than omitted. "Zero" is the answer worth
        reading; a missing key looks like a field that was never asked about.
        """
        grounded = sum(1 for n in self._nodes.values() if n.grounded)
        out = {
            "nodes": len(self._nodes),
            "grounded_nodes": grounded,
            "external_nodes": len(self._nodes) - grounded,
            "edges": len(self._edges),
        }
        counts = Counter(e.kind.value for e in self._edges)
        out.update({f"edges_{kind.value.lower()}": counts.get(kind.value, 0) for kind in EdgeKind})
        return out


__all__ = ["CallSite", "FactStore"]
