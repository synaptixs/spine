"""Which files a Module spans: the one answer ``mark_tests`` and ``FactStore.owning_module`` share (B143).

Its own module, and a leaf, on purpose: the capability matrix credits a front-end with the node and
edge kinds of every ``orchestrator.pkg`` module it imports, so ``extractor`` importing this from
``store`` made the Python front-end claim ``MENTIONS``. This file names ``CONTAINS`` only.
"""

from __future__ import annotations

from collections.abc import Iterable

from orchestrator.pkg.facts import Edge, EdgeKind, Node


def module_member_files(nodes: Iterable[Node], edges: Iterable[Edge]) -> dict[str, set[str]]:
    """Node id → every file one of its ``CONTAINS`` edges to a grounded node is declared in.

    The files a package Module spans (B143). The edge's own file, not the child's: C/C++ ids are
    global by name (B142), so a function a test file defines can carry the provenance of the
    product header that declares it, and reading the child's file made fmt's ``test/base-test.cc``
    look like it spanned ``include/fmt/base.h``. An edge with no file falls back to the child's.
    ``external`` children are placeholders and span nothing.
    """
    by_id = {n.id: n for n in nodes}
    out: dict[str, set[str]] = {}
    for edge in edges:
        if edge.kind is not EdgeKind.CONTAINS:
            continue
        child = by_id.get(edge.dst)
        if child is None or child.external or child.provenance is None or not child.provenance.file:
            continue
        file = (
            edge.provenance.file
            if edge.provenance is not None and edge.provenance.file
            else child.provenance.file
        )
        out.setdefault(edge.src, set()).add(file)
    return out


__all__ = ["module_member_files"]
