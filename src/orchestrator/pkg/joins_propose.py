"""Propose the join topology from evidence, so nobody authors it from scratch.

The join set is a small graph, and it gets what every other graph here gets: **derived from
facts, ratified by a human.** Nobody hand-writes ``episteme/`` either.

The evidence already exists once several repositories are extracted. Each repo's extractor
records the HTTP calls that matched no endpoint *of its own* — the side-channel in
``python_client.emit`` — and every repo's endpoints are nodes. A candidate join is simply a
consumer whose unmatched calls line up with some provider's routes.

**Every proposal carries the number of edges it would create**, because a join producing zero is
noise and must not be offered. That is the trigger-count-at-proposal-time discipline from
``constitution-roadmap.md``, and it works here where it would not work there: the evidence is
deterministic path overlap, not prose read by a model.

This module proposes only. It writes no config and declares nothing — a topology Spine invented
and then enforced would be a rule nobody agreed to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.join_link import _template_matches, _with_base
from orchestrator.pkg.scoping import unscope_id

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from orchestrator.pkg.facts import FactBatch
    from orchestrator.pkg.python_client import PendingCall


@dataclass(frozen=True)
class Candidate:
    """A join that the facts support, with the evidence a human needs to accept or reject it."""

    kind: str
    consumer: str
    provider: str
    base: str
    edges: int  # how many CONSUMES edges declaring this would create — the trigger count
    examples: tuple[str, ...] = field(default_factory=tuple)

    def as_yaml(self) -> str:
        """The block to paste into `.spine/repos.yaml`, with its evidence as comments."""
        noun = {
            "http": "call(s) match endpoints in",
            "data": "table name(s) shared with",
            "package": "import(s) defined by",
        }
        lines = [f"  # {self.edges} {noun[self.kind]} `{self.provider}`, from `{self.consumer}`"]
        lines += [f"  #   {ex}" for ex in self.examples]
        lines += [
            f"  - kind: {self.kind}",
            f"    consumer: {self.consumer}",
            f"    provider: {self.provider}",
        ]
        if self.base:
            lines.append(f"    base: {self.base}")
        return "\n".join(lines)


#: Bases worth trying. A proposal is only as good as the prefixes it guesses, and an unbounded
#: search would invent a base for any two paths that share a character. Empty first, so an exact
#: match is never explained by a prefix it did not need.
_CANDIDATE_BASES = ("", "/api", "/v1", "/api/v1")


def propose(
    batch: FactBatch,
    unresolved: Mapping[str, Sequence[PendingCall]],
    *,
    min_edges: int = 1,
) -> tuple[Candidate, ...]:
    """Candidate joins, strongest first. Never proposes a join that would create nothing."""
    return tuple(
        sorted(
            [*_http(batch, unresolved, min_edges), *_data(batch, min_edges), *_package(batch, min_edges)],
            key=lambda c: (-c.edges, c.kind, c.consumer, c.provider, c.base),
        )
    )


def _data(batch: FactBatch, min_edges: int) -> list[Candidate]:
    """Two repositories with a table of the same name.

    Weaker evidence than an HTTP path overlap and it is labelled that way: `users` is a table
    name two unrelated systems can both have, and only a human knows whether they are the same
    physical table. The count is what makes the difference legible — one shared name is a
    coincidence, six is a schema.
    """
    from orchestrator.pkg.scoping import unscope_id

    tables: dict[str, dict[str, str]] = {}
    for node in batch.nodes:
        if node.kind is NodeKind.ENTITY:
            owner, _ = unscope_id(node.id)
            tables.setdefault(owner, {})[node.name.lower()] = node.id

    out: list[Candidate] = []
    for consumer in sorted(tables):
        for provider in sorted(tables):
            if consumer >= provider:
                continue  # one direction only; the pair is symmetric, the declaration is not
            shared = sorted(tables[consumer].keys() & tables[provider].keys())
            if len(shared) >= min_edges:
                out.append(
                    Candidate(
                        kind="data",
                        consumer=consumer,
                        provider=provider,
                        base="",
                        edges=len(shared),
                        examples=tuple(f"shared table: {t}" for t in shared[:3]),
                    )
                )
    return out


def _package(batch: FactBatch, min_edges: int) -> list[Candidate]:
    """A repository importing something another declared repository actually defines.

    The strongest evidence of the three: an external placeholder whose name is exactly a
    first-party symbol somewhere else in the system is not a coincidence.
    """
    from orchestrator.pkg.scoping import unscope_id

    first_party: dict[str, str] = {}
    for node in batch.nodes:
        if not node.external:
            owner, unscoped = unscope_id(node.id)
            first_party[unscoped.partition(":")[2]] = owner

    external = {n.id for n in batch.nodes if n.external}
    hits: dict[tuple[str, str], list[str]] = {}
    for edge in batch.edges:
        if edge.kind is not EdgeKind.IMPORTS or edge.dst not in external:
            continue
        consumer, _ = unscope_id(edge.src)
        name = edge.dst.partition(":")[2]
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            found = first_party.get(".".join(parts[:i]))
            if found and found != consumer:
                hits.setdefault((consumer, found), []).append(name)
                break

    return [
        Candidate(
            kind="package",
            consumer=consumer,
            provider=provider,
            base="",
            edges=len(names),
            examples=tuple(f"imports: {n}" for n in sorted(set(names))[:3]),
        )
        for (consumer, provider), names in sorted(hits.items())
        if len(names) >= min_edges
    ]


def _http(
    batch: FactBatch, unresolved: Mapping[str, Sequence[PendingCall]], min_edges: int
) -> list[Candidate]:
    endpoints: dict[str, list[tuple[str, str]]] = {}
    for node in batch.nodes:
        if node.kind is NodeKind.ENDPOINT:
            repo, _ = unscope_id(node.id)
            endpoints.setdefault(repo, []).append((node.name, node.id))

    out: list[Candidate] = []
    for consumer in sorted(unresolved):
        calls = unresolved[consumer]
        if not calls:
            continue
        for provider in sorted(endpoints):
            if provider == consumer:
                continue  # an intra-repo match is the front-end's job, not a join
            best: tuple[int, str, list[str]] | None = None
            for base in _CANDIDATE_BASES:
                hits, examples = _count(calls, endpoints[provider], base)
                if best is None or hits > best[0]:
                    best = (hits, base, examples)
            if best and best[0] >= min_edges:
                out.append(
                    Candidate(
                        kind="http",
                        consumer=consumer,
                        provider=provider,
                        base=best[1],
                        edges=best[0],
                        examples=tuple(best[2][:3]),
                    )
                )
    return out


def _count(
    calls: Sequence[PendingCall], routes: Sequence[tuple[str, str]], base: str
) -> tuple[int, list[str]]:
    """How many of ``calls`` a provider would serve under ``base``, and a few examples."""
    hits = 0
    examples: list[str] = []
    for call in calls:
        wanted = _with_base(call.path, base)
        matched = {
            node_id
            for name, node_id in routes
            if name.partition(" ")[0] == call.verb
            and (name.partition(" ")[2] == wanted or _template_matches(wanted, name.partition(" ")[2]))
        }
        # One match only: two providers for one call is exactly the ambiguity the joiner
        # refuses, so it must not be counted as evidence *for* a join either.
        if len(matched) == 1:
            hits += 1
            if len(examples) < 3:
                where = f"{call.provenance}  " if call.provenance else ""
                examples.append(f"{where}{call.verb} {call.path}")
    return hits, examples


def render(candidates: Sequence[Candidate]) -> str:
    """A `joins:` block to review, or an honest note that the facts support none."""
    if not candidates:
        return (
            "# No join candidates. Either these repositories genuinely do not call each other,\n"
            "# or the calls are built from values the extractor cannot read (an f-string path is\n"
            "# collected as no call at all). `pkg joins --check` lists what went unplaced.\n"
        )
    return "joins:\n" + "\n\n".join(c.as_yaml() for c in candidates) + "\n"


__all__ = ["Candidate", "propose", "render"]
