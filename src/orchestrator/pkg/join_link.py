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

    return batch, JoinReport(
        joined=joined,
        unjoined=tuple(unjoined),
        per_join=tuple(sorted(counts.items())),
    )


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
