"""Areas — the coarse component grouping, shared by `state` and the episteme.

An **area** is a component one zoom level above a module: the first two segments of a
module's path or dotted namespace (``src/smf/smf-sm.c`` → ``src/smf``; ``App.Api.Users``
→ ``App.Api``). A **zone** is one level coarser still — the first segment (``src``,
``App``).

This lives in one place on purpose. ``state``'s architecture flowchart and the episteme's
area pages both group by area, and if they each derived it their own way they would show
different architectures for the same commit — and a reader who noticed would stop trusting
both. One definition, two renderers.

The subtle rule, learned the hard way (see ``AreaIndex.area_of``): a node's area comes from
the module it *lives in*, resolved by walking CONTAINS upward — never from its bare symbol
id.
"""

from __future__ import annotations

from orchestrator.pkg.facts import Node, NodeKind
from orchestrator.pkg.store import FactStore


def area_of_name(name: str) -> str:
    """Group a module name into its area (a coarse component).

    Slash-path modules (C/C++ translation units, e.g. ``src/smf/smf-sm.c``) group by
    their first two path segments (``src/smf``); dotted namespaces (Python/Java/C#)
    group by the first two dotted segments (``App.Api``).
    """
    if "/" in name:
        return "/".join(name.split("/")[:2])
    return ".".join(name.split(".")[:2])


def zone_of(area: str) -> str:
    """The architectural zone an area belongs to — its first path/namespace segment
    (``src/smf`` → ``src``; ``App.Api`` → ``App``)."""
    return area.split("/")[0].split(".")[0]


class AreaIndex:
    """Resolves any node to the area it lives in, from one CONTAINS pass."""

    def __init__(self, store: FactStore) -> None:
        self._store = store
        self._parent_of = store.parents_index()

    def owning_module(self, node_id: str) -> Node | None:
        """Walk CONTAINS upward to the module that owns this node, if any."""
        cur, seen = node_id, {node_id}
        while cur in self._parent_of:
            parent = self._store.node(self._parent_of[cur])
            if parent is None or parent.id in seen:
                break
            if parent.kind is NodeKind.MODULE:
                return parent
            seen.add(parent.id)
            cur = parent.id
        return None

    def module_of(self, node: Node) -> Node | None:
        """The module a node belongs to — itself, if it already is one."""
        if node.kind is NodeKind.MODULE:
            return node
        return self.owning_module(node.id)

    def area_of(self, node: Node) -> str:
        """The component a node lives in.

        Its owning module's name (dotted namespace / file path). When that can't be
        resolved — e.g. a C++ method whose class is declared in a ``.h`` parsed as C, so
        no ``cpp:`` type node owns it — fall back to the source file it's defined in, and
        **never** to the bare symbol id: C/C++ ids are symbols (``cpp:HSL2RGB``), not
        locations, so id-grouping would make every function its own area (and, via
        ``zone_of``, its own zone), flooding any layout with thousands of one-function
        entries.
        """
        mod = self.owning_module(node.id)
        name = mod.name if mod is not None else None
        if name is None and node.provenance is not None:
            name = node.provenance.file
        return area_of_name(name) if name else area_of_name(node.id.split(":", 1)[-1])


__all__ = ["AreaIndex", "area_of_name", "zone_of"]
