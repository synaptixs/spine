"""In-memory query layer over a ``FactBatch`` — the grounded-retrieval surface.

This is the minimum an agent needs to ask the two questions that justify the
PKG: *"what calls X?"* and *"what does changing X touch?"* — and get answers
that point back to ``file:line``. v0 is in-memory; the same query shape later
backs a Postgres/graph store without changing callers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind


@dataclass(frozen=True)
class CallSite:
    """A caller and the line where the call happens."""

    caller: Node
    at: str  # "file:line"


@dataclass(frozen=True)
class InterfaceCallSite:
    """A caller reached through an interface: it calls ``via`` — the member this one
    implements or overrides — and so *may* reach this implementation. Never certain: another
    implementation could be the one behind the interface at run time."""

    caller: Node
    at: str  # "file:line"
    via: str  # the supertype member the call lands on


class FactStore:
    """Indexed, read-only view over extracted facts."""

    def __init__(self, batch: FactBatch) -> None:
        self._nodes: dict[str, Node] = {n.id: n for n in batch.nodes}
        self._edges: list[Edge] = batch.edges
        self._parent_index: dict[str, str] | None = None
        self._super_index: dict[str, set[str]] | None = None

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

    def interface_callers_of(self, node_id: str) -> list[InterfaceCallSite]:
        """Callers of the members this method implements or overrides (B21, D9/D15).

        A call through an interface lands on the interface's member — ``_service.DoThing()``
        on ``IService.DoThing`` — so nothing calls ``Service.DoThing`` by name and
        ``callers_of`` on it is empty: in a DI-heavy .NET service, the blast radius of almost
        every implementation. This walks up from the method's own type through ``IMPLEMENTS``
        and ``PROVIDES``, every in-repo level, cycle-safe, and returns the callers of each
        same-named member found there, each tagged with the member it went through.

        In the *impact* direction this is safe to over-report: any caller holding an
        ``IService`` may reach ``Service``. It is never folded into ``callers_of`` — a caller
        that *might* break must not read as one that *will*.
        """
        node = self._nodes.get(node_id)
        if node is None or node.kind is not NodeKind.FUNCTION:
            return []
        owner = self._parents().get(node_id)
        if owner is None:
            return []
        out: list[InterfaceCallSite] = []
        seen, frontier = {owner}, [owner]
        while frontier:
            nxt: list[str] = []
            for type_id in frontier:
                for s in sorted(self._supertypes().get(type_id, ())):
                    if s in seen or s not in self._nodes or not self._nodes[s].grounded:
                        continue
                    seen.add(s)
                    nxt.append(s)
                    # Overloads share one id, so one member can be CONTAINS-linked once per
                    # overload; each is visited once, or its callers would be counted per overload.
                    members = {
                        m.id
                        for m in self.children_of(s)
                        if m.kind is NodeKind.FUNCTION and m.name == node.name
                    }
                    for member_id in sorted(members):
                        out.extend(
                            InterfaceCallSite(cs.caller, cs.at, member_id)
                            for cs in self.callers_of(member_id)
                        )
            frontier = nxt
        return sorted(set(out), key=lambda c: (c.via, c.caller.id, c.at))

    def _parents(self) -> dict[str, str]:
        if self._parent_index is None:
            self._parent_index = self.parents_index()
        return self._parent_index

    def _supertypes(self) -> dict[str, set[str]]:
        if self._super_index is None:
            index: dict[str, set[str]] = {}
            for e in self._edges:
                if e.kind in (EdgeKind.IMPLEMENTS, EdgeKind.PROVIDES):
                    index.setdefault(e.src, set()).add(e.dst)
            self._super_index = index
        return self._super_index

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

    def injection_reach_of(self, node_id: str) -> list[Node]:
        """Where a DI-bound implementation's dependents actually live (D15).

        ``PROVIDES`` is the one edge here that has to be followed **outbound**, and
        the reason is the direction dependency injection runs in. Asking "what
        breaks if I change ``OfflineFirstTopicsRepository``" finds nothing inbound:
        nothing calls the implementation, because every call site was handed the
        *interface*. The dependents are one hop the other way — the implementation
        provides ``TopicsRepository``, and it is that interface's methods that the
        screens and use-cases call.

        So this returns the provided type **and its members**, which is what lets the
        ordinary inbound ``CALLS`` walk continue from there and reach the real
        dependents. Measured on the validation app: without it, the blast radius of
        a repository implementation is empty; with it, it reaches the use-case, the
        view model and the sync worker that use it.

        Two providers for one type both appear — Dagger qualifiers are recorded but
        never resolved, so the honest answer is both rather than a guess.
        """
        provided = [e.dst for e in self._edges if e.kind is EdgeKind.PROVIDES and e.src == node_id]
        reached: list[Node] = []
        for target in provided:
            if target in self._nodes:
                reached.append(self._nodes[target])
            reached.extend(self.children_of(target))
        return reached

    def consumers_of(self, node_id: str) -> list[Node]:
        """What calls this endpoint — the other half of :meth:`exposers_of`.

        ``exposers_of`` said the dependents of ``GET /v1/runs`` were "its clients, outside
        the repo entirely". For a repo that ships both halves — a CLI and the service it
        talks to — they are not outside it, and the graph now knows: the client function
        holding a literal path is joined to the endpoint it calls.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.CONSUMES and e.dst == node_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def endpoints_called_by(self, node_id: str) -> list[Node]:
        """The endpoints this symbol calls — the forward direction of ``CONSUMES``."""
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.CONSUMES and e.src == node_id]
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

        ``CONSUMES`` continues that walk one hop further, to the client. Changing a
        handler reaches the endpoint it serves and then the code that calls it — which is
        the whole point of the join, and useless if only the first hop is followed.

        ``PROVIDES`` is followed **outbound**, via :meth:`injection_reach_of`, because
        dependency injection inverts the direction: nothing calls an implementation, so
        its dependents are only reachable through the interface it is bound to. See that
        method for why the walk expands to the interface's members rather than stopping
        at the interface itself.
        """
        from collections import deque

        seen = {node_id}
        out: list[tuple[Node, int]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            nid, depth = queue.popleft()
            if depth >= max_depth:
                continue
            inbound = (
                [site.caller for site in self.callers_of(nid)]
                + [site.caller for site in self.interface_callers_of(nid)]
                + self.exposers_of(nid)
                + self.consumers_of(nid)
                + self.injection_reach_of(nid)
            )
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
            EdgeKind.CONSUMES,
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
        API change score as zero-impact. ``CONSUMES`` is there for the same reason one
        hop later: with EXPOSES alone the walk stops at the endpoint, and the client that
        would actually break is still missing. A caller that wants the old, code-only
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

    def intents_for(self, symbol_id: str) -> list[Node]:
        """The tickets a symbol was last changed for (outgoing ``SERVES`` edges).

        Empty unless ``pkg.intent_link`` has run — it is opt-in, because it costs a `git blame`
        pass and buys nothing on a repository whose commits carry no issue keys. Empty is
        therefore *"not scanned or not attributed"*, never *"no prior work"*, and a caller that
        renders it must not let a blank read as the second.
        """
        ids = [e.dst for e in self._edges if e.kind is EdgeKind.SERVES and e.src == symbol_id]
        return [self._nodes[i] for i in ids if i in self._nodes]

    def symbols_serving(self, intent_id: str) -> list[Node]:
        """The symbols last changed for a ticket (incoming ``SERVES`` edges).

        The reverse of :meth:`intents_for`: "what did SSPN-49 touch?" — the blast radius of a
        past ticket, read off history rather than guessed.
        """
        ids = [e.src for e in self._edges if e.kind is EdgeKind.SERVES and e.dst == intent_id]
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
