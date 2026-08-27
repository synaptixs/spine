"""Cross-repository joins — the first edges that leave a repository.

Every other edge in the PKG comes from one file's tree. These come from **two repositories at
once**, and they are the only edges in the graph that no single parser could have produced.
That makes this the least certain code here, and it is built accordingly.

## Declared topology, inferred edges

A join is declared in ``.spine/repos.yaml``::

    joins:
      - kind: http
        consumer: web
        provider: billing
        base: /v1

That declaration does **not** create an edge. It answers the one question no amount of evidence
can settle — *which repository is even a candidate* — and leaves the rest to the facts. Matching
``POST /v1/orders/42`` against ``POST /v1/orders/{id}`` is still resolution, still done from
extracted endpoints, and still refused when it is not certain.

## Precision first, and what that costs

Held to the ``CALLS`` standard, not the structure standard. A missing cross-repo edge sends a
human looking; a fabricated one asserts that a service calls an endpoint nobody serves, and
every surface downstream renders it in the same confident tone as a fact from a parse tree.

So this matches **exactly**, then by template, and refuses anything else:

1. **Exact** — ``POST /v1/orders`` against ``POST /v1/orders``, after the consumer's path has the
   provider's ``base`` applied.
2. **Template** — ``POST /v1/orders/42`` against ``POST /v1/orders/{id}``, segment by segment,
   where a ``{param}`` segment matches exactly one literal segment. Never across a ``/``, so
   ``{id}`` cannot swallow ``42/refund``.
3. **Ambiguous → nothing.** Two provider endpoints matching one call is a judgement no evidence
   settles, so neither edge is emitted and the call stays unjoined. Recall pays; precision does
   not.

The consequence is deliberate: **recall well under 1.00, and published that way.**

## Unjoined calls are the honest denominator

A forgotten ``joins:`` entry is quiet in a way a forgotten ``repos:`` entry is not — missing
cross-repo edges look exactly like two services that are not coupled, which reads as health. So
every call this pass could not place is returned, counted and reportable by
``pkg joins --check``. An absence becomes a number.

## Where this runs, and where it must not

**Only from the multi-repo path.** Never inside ``RepoCodeExtractor.extract`` and never beside
``import_link``/``doc_link``, which run on every extraction. With one repository there is no
cross-repo work to do, but this matcher would still fuzzy-match *within* it and create edges the
exact-match client deliberately declined to create — the same invention risk through a back
door. ``--repos`` is the opt-in to a graph with weaker guarantees, and the two must stay
distinguishable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.scoping import scope_id

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from orchestrator.pkg.python_client import PendingCall
    from orchestrator.pkg.repos import Join


@dataclass(frozen=True)
class UnjoinedCall:
    """A call this pass could not place, and why — the material for `--check`."""

    repo: str
    verb: str
    path: str
    caller: str
    where: str
    reason: str  # "no-declared-provider" | "no-matching-endpoint" | "ambiguous"

    def __str__(self) -> str:
        return f"{self.where}  {self.repo}: {self.verb} {self.path}  ({self.reason})"


@dataclass(frozen=True)
class JoinReport:
    """What was joined, what was not, and per declared join so a dead one is visible."""

    joined: int
    unjoined: tuple[UnjoinedCall, ...]
    per_join: tuple[tuple[str, int], ...]

    @property
    def examined(self) -> int:
        return self.joined + len(self.unjoined)

    @property
    def recall(self) -> float | None:
        """Share of cross-repo call candidates that were placed. ``None`` when there were none.

        Precision is ~1.00 by construction — nothing can join to an undeclared repository — so
        this is the number worth reading, and the number worth publishing when it is low.
        """
        return self.joined / self.examined if self.examined else None


def _endpoint_index(batch: FactBatch) -> dict[str, list[tuple[str, str]]]:
    """``repo -> [(name, node_id)]`` for every endpoint, from the scoped merged graph."""
    from orchestrator.pkg.scoping import unscope_id

    index: dict[str, list[tuple[str, str]]] = {}
    for node in batch.nodes:
        if node.kind is not NodeKind.ENDPOINT:
            continue
        repo, _ = unscope_id(node.id)
        index.setdefault(repo, []).append((node.name, node.id))
    return index


def _with_base(path: str, base: str) -> str:
    """The consumer's path as the provider would spell it."""
    if not base or path.startswith(base + "/") or path == base:
        return path
    return f"{base}{path}" if path.startswith("/") else f"{base}/{path}"


def _template_matches(call_path: str, route_path: str) -> bool:
    """``/v1/orders/42`` against ``/v1/orders/{id}``, one literal segment per parameter.

    Segment-wise rather than by regex over the whole path, so a ``{param}`` can never swallow a
    ``/`` — ``/v1/orders/{id}`` must not match ``/v1/orders/42/refund``, which is a different
    endpoint and quite possibly a different handler.
    """
    call_parts, route_parts = call_path.split("/"), route_path.split("/")
    if len(call_parts) != len(route_parts):
        return False
    for got, want in zip(call_parts, route_parts, strict=True):
        if want.startswith("{") and want.endswith("}"):
            if not got:
                return False
            continue
        if got != want:
            return False
    return True


def link_joins(
    batch: FactBatch,
    joins: Sequence[Join],
    unresolved: Mapping[str, Sequence[PendingCall]],
) -> tuple[FactBatch, JoinReport]:
    """Add ``CONSUMES`` edges across declared repository boundaries.

    ``unresolved`` is the side-channel from each repo's extractor — calls that matched no
    endpoint *in their own repository*. Those, and only those, are candidates: a call already
    joined at home is not looking for a provider elsewhere.
    """
    # Repointing joins run first and rebuild the batch; the HTTP pass then adds edges to the
    # final one. Order matters only in that direction — a repoint after an add would have to
    # know about edges it did not create.
    counts_extra: dict[str, int] = {}
    for join in joins:
        if join.kind == "data":
            batch, counts_extra[str(join)] = _link_data(batch, join)
        elif join.kind == "package":
            batch, counts_extra[str(join)] = _link_package(batch, join)

    endpoints = _endpoint_index(batch)
    providers: dict[str, list[Join]] = {}
    for join in joins:
        if join.kind == "http":
            providers.setdefault(join.consumer, []).append(join)

    joined = 0
    unjoined: list[UnjoinedCall] = []
    counts: dict[str, int] = {str(j): 0 for j in joins}

    for repo in sorted(unresolved):
        for call in unresolved[repo]:
            declared = providers.get(repo, [])
            if not declared:
                unjoined.append(_unjoined(repo, call, "no-declared-provider"))
                continue

            hits: list[tuple[str, str]] = []  # (endpoint_id, join_label)
            for join in declared:
                wanted = _with_base(call.path, join.base)
                for name, node_id in endpoints.get(join.provider, []):
                    verb, _, route = name.partition(" ")
                    if verb != call.verb:
                        continue
                    if route == wanted or _template_matches(wanted, route):
                        hits.append((node_id, str(join)))

            unique = {node_id for node_id, _ in hits}
            if not unique:
                unjoined.append(_unjoined(repo, call, "no-matching-endpoint"))
                continue
            if len(unique) > 1:
                # Two providers could serve this. Evidence does not settle it, so neither edge
                # is emitted — the same refusal `_resolve_call` makes for an unresolved name.
                unjoined.append(_unjoined(repo, call, "ambiguous"))
                continue

            node_id, label = hits[0]
            batch.add_edge(Edge(scope_id(call.caller_id, repo), node_id, EdgeKind.CONSUMES, call.provenance))
            counts[label] = counts.get(label, 0) + 1
            joined += 1

    counts.update(counts_extra)
    return batch, JoinReport(
        joined=joined + sum(counts_extra.values()),
        unjoined=tuple(unjoined),
        per_join=tuple(sorted(counts.items())),
    )


# ---- repointing: the shape both remaining joiners share ----------------------


def _repoint(batch: FactBatch, remap: dict[str, str], kinds: frozenset[EdgeKind]) -> tuple[FactBatch, int]:
    """Rebuild ``batch`` with edges of ``kinds`` moved from a node to its counterpart.

    A rebuild rather than a mutation, and for the same reason ``import_link`` rebuilds: the old
    node has to *go*. Leaving it would keep answering "who writes this table" with half the
    truth — which is the defect the join exists to close, still present and now harder to see
    because a cross-repo edge exists beside it.

    Only nodes left with nothing pointing at them are dropped. A repo owning a table or module
    nobody else references is ordinary, and must survive untouched.
    """
    if not remap:
        return batch, 0

    moved = 0
    edges: list[Edge] = []
    for edge in batch.edges:
        if edge.dst not in remap:
            edges.append(edge)
            continue
        if edge.kind in kinds:
            edges.append(Edge(edge.src, remap[edge.dst], edge.kind, edge.provenance))
            moved += 1
            continue
        # Every other edge into a remapped node is **dropped**, and that is forced rather than
        # chosen: the node is going away, so keeping an edge to it would dangle. It is also
        # correct on its own terms — once `reporting`'s `invoices` entity collapses onto
        # `billing`'s, `reporting`'s module does not contain an `invoices` entity any more.
        # Repointing that CONTAINS instead would assert that billing's module contains a node
        # declared in another repository, which is false.
    if not moved:
        return batch, 0

    result = FactBatch()
    for node in batch.nodes:
        if node.id in remap:
            continue  # collapsed onto its counterpart
        result.add_node(node)
    for edge in edges:
        result.add_edge(edge)
    return result, moved


# ---- data: two nodes, one physical table -------------------------------------


def _link_data(batch: FactBatch, join: Join) -> tuple[FactBatch, int]:
    """Collapse the consumer's tables onto the provider's, where both name the same one.

    Two repositories writing ``invoices`` produce two ``Entity`` nodes for one physical table,
    so *"who writes this table"* answers per repository and silently under-reports. The
    declaration names which repository owns the schema; the consumer's node is collapsed onto
    it — exactly what ``data_layer_link`` does when an ORM entity and a real SQL table describe
    the same thing.

    **Matched on name, and only where both sides already have the table.** Nothing is invented
    and nothing is renamed: a table the provider does not have keeps its own node, because a
    repository owning a table nobody else knows about is the normal case.
    """
    from orchestrator.pkg.scoping import unscope_id

    by_repo: dict[str, dict[str, str]] = {}
    for node in batch.nodes:
        if node.kind is NodeKind.ENTITY:
            owner, _ = unscope_id(node.id)
            by_repo.setdefault(owner, {})[node.name.lower()] = node.id

    mine = by_repo.get(join.consumer, {})
    theirs = by_repo.get(join.provider, {})
    remap = {mine[name]: theirs[name] for name in mine.keys() & theirs.keys()}
    return _repoint(batch, remap, frozenset({EdgeKind.READS, EdgeKind.WRITES, EdgeKind.REFERENCES}))


# ---- package: an import that crosses a repository ----------------------------


def _link_package(batch: FactBatch, join: Join) -> tuple[FactBatch, int]:
    """Repoint the consumer's *external* placeholders at the provider's real symbols.

    ``import_link`` already does this within a repository: a front-end records an import as
    text and creates an ``external`` placeholder, and a whole-repo pass repoints it at what it
    denotes. Across a boundary the placeholder denotes a symbol in **another declared
    repository**, and the same two rules apply, in the same order:

    1. **Exact id** — ``py:shared.money.to_cents`` is exactly the provider's function. Inside a
       repository this happens for free, because a grounded node upgrades the placeholder in
       ``FactBatch``'s dedup. Across repositories the scope makes the ids differ, so it has to
       be done deliberately.
    2. **Longest dotted prefix that is a provider module** — the re-export case, where
       ``shared.money.to_cents`` resolves to the module ``shared.money``.

    **``CALLS`` is repointed as well as ``IMPORTS``, and that is not optional.** The placeholder
    carries both: ``from shared.money import to_cents`` produces the import, and ``to_cents()``
    produces a call to the same node. Moving only the import would drop a real call edge on the
    floor when the placeholder is removed — turning a join that adds knowledge into one that
    quietly destroys it.

    Only ``external`` targets are considered. A resolved edge is already a fact, and repointing
    it would be a guess overriding one.
    """
    from orchestrator.pkg.scoping import unscope_id

    provider_any: dict[str, str] = {}
    provider_modules: dict[str, str] = {}
    for node in batch.nodes:
        if node.external:
            continue
        owner, unscoped = unscope_id(node.id)
        if owner != join.provider:
            continue
        name = unscoped.partition(":")[2]
        provider_any[name] = node.id
        if node.kind is NodeKind.MODULE:
            provider_modules[name] = node.id

    external = {n.id for n in batch.nodes if n.external}
    remap: dict[str, str] = {}
    for edge in batch.edges:
        if edge.dst not in remap and edge.dst in external and unscope_id(edge.src)[0] == join.consumer:
            name = edge.dst.partition(":")[2]
            if name in provider_any:
                remap[edge.dst] = provider_any[name]
                continue
            parts = name.split(".")
            target = next(
                (
                    provider_modules[".".join(parts[:i])]
                    for i in range(len(parts) - 1, 0, -1)
                    if ".".join(parts[:i]) in provider_modules
                ),
                None,
            )
            if target is not None:
                remap[edge.dst] = target
    return _repoint(batch, remap, frozenset({EdgeKind.IMPORTS, EdgeKind.CALLS}))


def _unjoined(repo: str, call: PendingCall, reason: str) -> UnjoinedCall:
    return UnjoinedCall(
        repo=repo,
        verb=call.verb,
        path=call.path,
        caller=call.caller_id,
        where=str(call.provenance) if call.provenance else "",
        reason=reason,
    )


__all__ = ["JoinReport", "UnjoinedCall", "link_joins"]
