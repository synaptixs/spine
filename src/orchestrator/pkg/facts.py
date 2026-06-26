"""Universal fact schema for the Product Knowledge Graph (PKG) — Layer 1.

This is the *language-agnostic* vocabulary every code extractor emits into. A
Python ``class``, a Go ``struct``, a TypeScript ``interface`` all normalise to a
single ``Type`` node; a method / ``func`` / arrow-function all become a
``Function``. Only the per-language *front-end* differs — the facts, the store,
and every agent query stay the same (see ``extractor.LanguageExtractor``).

Every grounded node carries ``Provenance`` (``file:line``) so any answer can be
traced back to source. Nodes referenced but not defined in the scanned tree
(imported symbols, builtins) are marked ``external=True`` and have no provenance.
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


class EdgeKind(str, Enum):
    """Universal edge vocabulary."""

    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"  # module→type, type→method
    CALLS = "CALLS"
    IMPLEMENTS = "IMPLEMENTS"  # subclass / interface impl
    READS = "READS"
    WRITES = "WRITES"
    EXPOSES = "EXPOSES"  # route→handler
    REFERENCES = "REFERENCES"  # entity→entity foreign key


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from, to line precision."""

    file: str
    line: int
    end_line: int | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


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


__all__ = ["Edge", "EdgeKind", "FactBatch", "Node", "NodeKind", "Provenance"]
