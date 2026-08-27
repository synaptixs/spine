"""Universal fact schema for the Product Knowledge Graph (PKG) — Layer 1.

This is the *language-agnostic* vocabulary every code extractor emits into. A
Python ``class``, a Go ``struct``, a TypeScript ``interface`` all normalise to a
single ``Type`` node; a method / ``func`` / arrow-function all become a
``Function``. Only the per-language *front-end* differs — the facts, the store,
and every agent query stay the same (see ``extractor.LanguageExtractor``).

Every grounded node carries ``Provenance`` (``file:line``) so any answer can be
traced back to source. Nodes referenced but not defined in the scanned tree
(imported symbols, builtins) are marked ``external=True`` and have no provenance.

**Intent is the one node kind that is not an artefact.** Every other kind is something you can
point at in a file. An ``Intent`` is a reason — the ticket or requirement a symbol serves — and
its evidence is git history, not source text. It is added here as vocabulary only: nothing
emits ``Intent`` or ``SERVES`` yet, so every graph is byte-identical until the scanner lands.
That split is deliberate — this module has 46 non-test importers, and a vocabulary change that
arrives together with the facts it enables leaves a downstream failure with two possible causes.

**Media (G3) reuses ``DOC`` — it does not get its own node kind.** An extracted
image/audio/video transcript enters the graph as a ``Doc`` whose ``source_file``
is the media file (``.png``/``.mp4``/…). This is a deliberate Phase-0 decision:
every doc surface — ``docs_for``, coverage, drift, and ``MENTIONS`` binding —
then works on media unchanged, and ``facts.py`` needs no schema change beyond
this note. The determinism contract is preserved *outside* the graph build: model
inference runs only in the opt-in ``orchestrator media extract`` command, which
writes a committed, content-addressed transcript artifact; ``understand``/``state``
read that plain-JSON artifact exactly like any other doc and never run a model.
See :mod:`orchestrator.pkg.media` for the artifact format and reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeKind(str, Enum):
    """Universal node vocabulary (a small, task-driven schema; grow as needed)."""

    MODULE = "Module"
    TYPE = "Type"  # class / struct / interface / enum
    FUNCTION = "Function"  # function / method / procedure
    FIELD = "Field"  # attribute / property / column
    ENDPOINT = "Endpoint"  # HTTP route / RPC
    ENTITY = "Entity"  # ORM model / data entity
    DOC = "Doc"  # a documentation page (README, design doc, ADR, …) — and media transcripts (G3)
    # The only node kind that is not a physical artefact. Everything above is something you
    # can point at in a file; an Intent is a *reason* — the ticket, requirement or capability
    # a symbol exists to serve. Added by the intent layer (phase 7 of the accuracy roadmap)
    # so the graph can answer "what is this for", not only "what calls it".
    INTENT = "Intent"


class EdgeKind(str, Enum):
    """Universal edge vocabulary."""

    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"  # module→type, type→method
    CALLS = "CALLS"
    IMPLEMENTS = "IMPLEMENTS"  # subclass / interface impl
    READS = "READS"
    WRITES = "WRITES"
    EXPOSES = "EXPOSES"  # route→handler
    CONSUMES = "CONSUMES"  # caller→the endpoint it calls (the other half of EXPOSES)
    REFERENCES = "REFERENCES"  # entity→entity foreign key
    MENTIONS = "MENTIONS"  # doc→the code symbol it describes
    # symbol→the Intent it was built for. The second edge kind carrying meaning rather than
    # mechanism, and the only one whose evidence is git history rather than source text.
    SERVES = "SERVES"


# The kinds that are *symbols* — a named thing inside a file — as opposed to a container
# (`Module`), a document (`Doc`), or a reason (`Intent`). Named here because more than one
# pass needs "everything you could point at in source", and spelling the members out at each
# site both duplicates the concept and makes `pkg capabilities` read a *filter* as an *emit*.
SYMBOL_KINDS = frozenset({NodeKind.TYPE, NodeKind.FUNCTION, NodeKind.FIELD, NodeKind.ENDPOINT})


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from, to line precision.

    ``repo`` names the repository a fact came from, and is empty for the single-repo case that
    is everything today. It exists so a merged multi-repo graph can say *which* checkout a
    ``file:line`` belongs to — see :mod:`orchestrator.pkg.scoping`.

    **``__str__`` deliberately does not include it, and must not start.** Six production call
    sites parse this string back with ``split(":", 1)[0]`` to recover the file path::

        sdlc/design.py            sdlc/builddoc.py (x2)
        sdlc/autorun.py           sdlc/criteria_binding.py
                                  sdlc/evidence.py

    Adding a segment makes every one of them return the *repo name* where a path is expected —
    silently, with no exception raised. Two of those are the ones that would hurt:
    ``evidence.py`` is the Evidence artifact's own file accessor, and ``criteria_binding.py``
    decides whether an acceptance criterion is bound to a landing site. A criterion binding
    against a repo name binds against nothing and passes, which is exactly the guarantee the
    binding exists to provide. ``tests/pkg/test_identity_contract.py`` pins this.

    Use :meth:`qualified` where the repository matters. It is for display and for keys that are
    known to be repo-aware — never for anything that will be split back apart.
    """

    file: str
    line: int
    end_line: int | None = None
    repo: str = ""

    def __str__(self) -> str:
        # Contract: exactly one ':' separator, file first. See the class docstring.
        return f"{self.file}:{self.line}"

    def qualified(self) -> str:
        """``repo:file:line`` when the repo is known, else ``file:line``."""
        return f"{self.repo}:{self}" if self.repo else str(self)


@dataclass(frozen=True)
class Node:
    """A code entity. ``id`` is a stable, language-prefixed key (``py:pkg.mod.Cls``)."""

    id: str
    kind: NodeKind
    name: str
    language: str = ""
    provenance: Provenance | None = None
    external: bool = False

    @property
    def grounded(self) -> bool:
        return self.provenance is not None and not self.external


@dataclass(frozen=True)
class Edge:
    """A directed relation between two node ids."""

    src: str
    dst: str
    kind: EdgeKind
    provenance: Provenance | None = None

    def key(self) -> tuple[str, str, str, str]:
        return (self.src, self.dst, self.kind.value, str(self.provenance))


@dataclass
class FactBatch:
    """A mutable collection of facts that de-duplicates as it grows.

    Node de-dup prefers the most-informative record: a later *grounded* node
    upgrades an earlier ``external`` placeholder for the same id.
    """

    _nodes: dict[str, Node] = field(default_factory=dict)
    _edges: dict[tuple[str, str, str, str], Edge] = field(default_factory=dict)

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def add_node(self, node: Node) -> None:
        existing = self._nodes.get(node.id)
        if existing is None or (node.grounded and not existing.grounded):
            self._nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        self._edges.setdefault(edge.key(), edge)

    def merge(self, other: FactBatch) -> None:
        for n in other.nodes:
            self.add_node(n)
        for e in other.edges:
            self.add_edge(e)

    def counts(self) -> dict[str, int]:
        by_kind: dict[str, int] = {}
        for n in self._nodes.values():
            by_kind[n.kind.value] = by_kind.get(n.kind.value, 0) + 1
        for e in self._edges.values():
            by_kind[e.kind.value] = by_kind.get(e.kind.value, 0) + 1
        return by_kind


__all__ = ["SYMBOL_KINDS", "Edge", "EdgeKind", "FactBatch", "Node", "NodeKind", "Provenance"]
