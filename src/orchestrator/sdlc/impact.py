"""Blast-radius + reference checks for the design stage — grounded, no LLM.

The design stage decides *what to build*; this module grounds two further
questions against the Product Knowledge Graph, deterministically:

* **Blast radius** — for each module a design says it will touch, who imports it
  (module-level dependents) and which of its symbols have the most callers
  (the risky-to-change hotspots). This is the "what does changing X touch?"
  the graph already answers (``FactStore.importers_of`` / ``callers_of`` /
  ``impact_of``), moved *before* code is written rather than caught in review.
* **Unverified references** — modules a design names that don't exist in the
  graph, so a hallucinated path is flagged for confirmation instead of silently
  scaffolded. Suppressed on an ungrounded (greenfield) repo, where "absent" is
  expected of everything.

Module-level impact works for every front-end that emits ``IMPORTS`` (all of
them); symbol-level hotspots need ``CALLS`` (absent for TypeScript/Java today),
so their absence is reported honestly rather than implied to be zero impact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import EdgeKind, Node, NodeKind

_MAX_IMPORTER_NAMES = 8
_MAX_SYMBOLS_PER_MODULE = 5


@dataclass(frozen=True)
class SymbolImpact:
    """An existing symbol in a touched module, ranked by how many call it."""

    name: str
    where: str  # "file:line"
    callers: int
    transitive: int  # transitive callers (BFS over the reverse call graph)


@dataclass(frozen=True)
class ModuleImpact:
    """One module the design touches, with who depends on it."""

    ref: str  # the path/name as the design wrote it
    module: str  # resolved module node name
    where: str  # provenance file
    importers: int
    importer_names: tuple[str, ...]
    hotspots: tuple[SymbolImpact, ...]


@dataclass(frozen=True)
class BlastRadius:
    modules: tuple[ModuleImpact, ...]
    unresolved: tuple[str, ...]  # design refs that matched no module
    call_graph_available: bool
    grounded: bool  # the graph had any grounded nodes at all

    @property
    def empty(self) -> bool:
        return not self.modules and not self.unresolved


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def _module_nodes(store: FactStore) -> list[Node]:
    """MODULE nodes, grounded first (so a real module wins an ambiguous match)."""
    mods = [n for n in store.nodes if n.kind is NodeKind.MODULE]
    return sorted(mods, key=lambda n: (not n.grounded, n.id))


def _match_module(ref: str, modules: list[Node]) -> Node | None:
    """Resolve a design's file/module reference to a MODULE node, best-effort.

    Tries, in order: exact provenance path or path suffix, exact node name,
    then basename. ``modules`` is pre-sorted grounded-first, so the first hit at
    each precedence level prefers real code.
    """
    ref_n = ref.replace("\\", "/").strip().lstrip("./")
    if not ref_n:
        return None
    base = _basename(ref_n)
    for n in modules:
        f = (n.provenance.file if n.provenance else "") or ""
        if f and (f == ref_n or f.endswith("/" + ref_n)):
            return n
    for n in modules:
        if n.name == ref_n or n.name == base:
            return n
    for n in modules:
        f = (n.provenance.file if n.provenance else "") or ""
        if f and _basename(f) == base:
            return n
    return None


def _hotspots(store: FactStore, module: Node, *, limit: int) -> list[SymbolImpact]:
    """The most-called top-level symbols of a module (risky to change)."""
    out: list[SymbolImpact] = []
    for child in store.children_of(module.id):
        if child.kind not in (NodeKind.FUNCTION, NodeKind.TYPE):
            continue
        callers = store.callers_of(child.id)
        if not callers:
            continue
        out.append(
            SymbolImpact(
                name=child.name,
                where=str(child.provenance) if child.provenance else "",
                callers=len(callers),
                transitive=len(store.impact_of(child.id)),
            )
        )
    out.sort(key=lambda s: (-s.callers, -s.transitive, s.name))
    return out[:limit]


def blast_radius(
    store: FactStore, files: list[str], *, max_symbols: int = _MAX_SYMBOLS_PER_MODULE
) -> BlastRadius:
    """Compute the blast radius of touching ``files`` against the graph."""
    grounded = store.summary().get("grounded_nodes", 0) > 0
    call_graph = bool(store.edges_of_kind(EdgeKind.CALLS))
    modules = _module_nodes(store)

    mods: list[ModuleImpact] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for raw in files:
        ref = str(raw).strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        node = _match_module(ref, modules)
        if node is None:
            unresolved.append(ref)
            continue
        importers = store.importers_of(node.id)
        names = tuple(sorted({i.name for i in importers}))[:_MAX_IMPORTER_NAMES]
        hotspots = tuple(_hotspots(store, node, limit=max_symbols)) if call_graph else ()
        mods.append(
            ModuleImpact(
                ref=ref,
                module=node.name,
                where=(node.provenance.file if node.provenance else "") or "",
                importers=len(importers),
                importer_names=names,
                hotspots=hotspots,
            )
        )
    return BlastRadius(tuple(mods), tuple(unresolved), call_graph, grounded)


def unverified_references(br: BlastRadius) -> list[str]:
    """Design-named paths absent from the graph (possible hallucinations).

    Suppressed when the graph is ungrounded/greenfield — there, everything is
    legitimately absent and flagging it all would be noise.
    """
    return [] if not br.grounded else list(br.unresolved)


def to_dict(br: BlastRadius) -> dict[str, Any]:
    """Serialisable form persisted into ``design.json``."""
    return {
        "call_graph_available": br.call_graph_available,
        "grounded": br.grounded,
        "modules": [
            {
                "ref": m.ref,
                "module": m.module,
                "where": m.where,
                "importers": m.importers,
                "importer_names": list(m.importer_names),
                "hotspots": [
                    {"name": s.name, "where": s.where, "callers": s.callers, "transitive": s.transitive}
                    for s in m.hotspots
                ],
            }
            for m in br.modules
        ],
        "unverified_references": unverified_references(br),
    }


def render_md(bd: dict[str, Any]) -> str:
    """Render the persisted blast-radius dict as design.md sections (may be empty)."""
    if not bd or not bd.get("grounded"):
        return ""
    mods = bd.get("modules") or []
    unverified = bd.get("unverified_references") or []
    if not mods and not unverified:
        return ""

    lines: list[str] = []
    if mods:
        lines.append("\n## Blast radius")
        lines.append("_Grounded in the knowledge graph — touching these modules affects their dependents._\n")
        for m in mods:
            imp = f"imported by {m['importers']} module(s)"
            if m.get("importer_names"):
                imp += ": " + ", ".join(m["importer_names"])
            lines.append(f"- `{m['ref']}` — {imp}")
            for s in m.get("hotspots") or []:
                extra = f", {s['transitive']} transitive" if s["transitive"] > s["callers"] else ""
                where = f" — {s['where']}" if s.get("where") else ""
                lines.append(f"    - high fan-in: `{s['name']}` ({s['callers']} caller(s){extra}){where}")
        if not bd.get("call_graph_available"):
            lines.append(
                "\n_Call graph unavailable for this language — module-level impact only, "
                "symbol-level hotspots omitted (not zero impact)._"
            )
    if unverified:
        lines.append("\n## ⚠ Unverified references")
        lines.append(
            "_Named in the design but absent from the knowledge graph — confirm each is a "
            "new file, not a hallucinated path:_"
        )
        lines.extend(f"- `{r}`" for r in unverified)
    return "\n".join(lines) + "\n"


__all__ = [
    "BlastRadius",
    "ModuleImpact",
    "SymbolImpact",
    "blast_radius",
    "render_md",
    "to_dict",
    "unverified_references",
]
