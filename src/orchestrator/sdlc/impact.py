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

from collections.abc import Iterable
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
    # Endpoints routing to this symbol. Separate from ``callers`` because they are not
    # call sites — nothing in the language calls a handler — but they rank the same way:
    # a public route is a reason to be careful, not a reason to sort the symbol last.
    exposed: int = 0
    #: How many of ``callers`` are product code by the graph's fact (``Node.test``). The build
    #: document says "nothing in the product depends on what changes" only when every importer is
    #: a test *and* no symbol it lists is called from product code (B160).
    product_callers: int = 0


@dataclass(frozen=True)
class ModuleImpact:
    """One module the design touches, with who depends on it."""

    ref: str  # the path/name as the design wrote it
    module: str  # resolved module node name
    where: str  # provenance file
    importers: int
    importer_names: tuple[str, ...]
    hotspots: tuple[SymbolImpact, ...]
    # How many source files the resolved module node owns. 1 for a file-keyed front-end
    # (Python, TypeScript); more for a namespace-, package- or directory-keyed one (C#, Java,
    # PHP, Go), where ``importers`` is a fact about the whole namespace and must not be read
    # as a fact about ``ref``.
    spans: int = 1
    #: The names in ``importer_names`` that are test code by the graph's fact (``Node.test``),
    #: so the build document can say "only tests import this" without re-deriving test-ness
    #: from a module name. A name is listed only when every importer carrying it is a test.
    test_importer_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlastRadius:
    modules: tuple[ModuleImpact, ...]
    unresolved: tuple[str, ...]  # design refs that matched no module
    call_graph_available: bool
    grounded: bool  # the graph had any grounded nodes at all
    #: The front-ends that actually produced these modules, sorted.
    #:
    #: Carried because the only other source of a language downstream is `--language`, a
    #: *codegen* flag — and a statement about how complete the call graph is belongs to the
    #: extractor that built it, not to the language we are about to write. On a C# repository
    #: planned without the flag, that mismatch published Python's measured recall as though it
    #: described a graph Python had no part in.
    languages: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.modules and not self.unresolved


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def _module_nodes(store: FactStore) -> list[Node]:
    """MODULE nodes, grounded first (so a real module wins an ambiguous match)."""
    mods = [n for n in store.nodes if n.kind is NodeKind.MODULE]
    return sorted(mods, key=lambda n: (not n.grounded, n.id))


def _file_index(store: FactStore) -> tuple[list[tuple[str, Node]], dict[str, int]]:
    """Every grounded source file → the MODULE node that owns it, plus files-per-module.

    A namespace-keyed front-end (C#, Java, PHP; Go by directory) emits **one** MODULE node per
    namespace, whose provenance is whichever file was walked first. Resolving a design path
    against MODULE provenance alone therefore resolves exactly one file per namespace and
    reports every sibling as absent — on NSS-1209, three of five correct files came back
    "hallucinated" two sections after their own symbols had been printed with line numbers.

    Every grounded node knows its file, and CONTAINS knows its module. That pair is the fact
    the unverified-references check always claimed to test: *is this path in the graph?*
    Sorted so resolution is byte-stable across extractions.
    """
    by_file: dict[str, Node] = {}
    for node in store.nodes:
        if not node.grounded or node.provenance is None or not node.provenance.file:
            continue
        owner = store.owning_module(node.id)
        if owner is not None:
            by_file.setdefault(node.provenance.file, owner)
    spans: dict[str, int] = {}
    for owner in by_file.values():
        spans[owner.id] = spans.get(owner.id, 0) + 1
    return sorted(by_file.items()), spans


def _match_module(
    ref: str, modules: list[Node], by_file: list[tuple[str, Node]] | None = None
) -> Node | None:
    """Resolve a design's file/module reference to a MODULE node, best-effort.

    Candidates are every MODULE node by its own provenance file (grounded-first, so a real
    module wins an ambiguous exact match) and every grounded file by its owning module
    (``by_file``, from :func:`_file_index`) — so a file that shares its module node with
    siblings still resolves to that module. Four passes: exact provenance path; exact node
    name; path suffix; bare basename. The last two follow ``source_paths.resolve``'s rules —
    two distinct owners is a guess and resolves to nothing, and a ref that names a directory
    is never matched on basename — or a NEW ``Payments/Client.cs`` would resolve to
    ``Legacy/Client.cs`` and the honest "unverified" answer would go silent.
    """
    from orchestrator.sdlc.source_paths import normalise

    ref_n = normalise(ref)
    if not ref_n:
        return None
    base = _basename(ref_n)
    candidates: list[tuple[str, Node]] = [
        ((n.provenance.file if n.provenance else "") or "", n) for n in modules
    ]
    candidates = [(f, n) for f, n in candidates if f] + list(by_file or ())

    for f, n in candidates:
        if f == ref_n:
            return n
    for n in modules:
        if n.name == ref_n or n.name == base:
            return n
    suffix = _unique_owner(n for f, n in candidates if f.endswith("/" + ref_n))
    if suffix is not None:
        return suffix
    if "/" not in ref_n:
        return _unique_owner(n for f, n in candidates if _basename(f) == base)
    return None


def _unique_owner(owners: Iterable[Node]) -> Node | None:
    """The one distinct node among ``owners``, or None — a second candidate is a guess."""
    distinct: dict[str, Node] = {}
    for n in owners:
        distinct.setdefault(n.id, n)
    return next(iter(distinct.values())) if len(distinct) == 1 else None


def _hotspots(store: FactStore, module: Node, *, limit: int) -> list[SymbolImpact]:
    """The most-called top-level symbols of a module (risky to change).

    A route handler has no in-language callers — the framework invokes it — so filtering
    on ``callers_of`` alone dropped every endpoint out of the blast radius entirely, and
    a change to public API scored as touching nothing. An inbound ``EXPOSES`` edge keeps
    the symbol in, with ``callers`` still counting *call sites* honestly (zero) and the
    endpoint showing up in the transitive count.
    """
    out: list[SymbolImpact] = []
    for child in store.children_of(module.id):
        if child.kind not in (NodeKind.FUNCTION, NodeKind.TYPE):
            continue
        callers = store.callers_of(child.id)
        exposers = store.exposers_of(child.id)
        if not callers and not exposers:
            continue
        out.append(
            SymbolImpact(
                name=child.name,
                where=str(child.provenance) if child.provenance else "",
                callers=len(callers),
                transitive=len(store.impact_of(child.id)),
                exposed=len(exposers),
                product_callers=sum(1 for c in callers if not c.caller.test),
            )
        )
    # Rank on inbound edges of either kind. Ranking on call sites alone sorted every
    # route handler to the bottom of the list and then truncated it away — present in
    # the data, absent from the report, which is the same wrong answer one step later.
    # Endpoints break ties ahead of equal-fan-in internals: at the same inbound count, a
    # public route is the riskier thing to change, and the list is truncated below.
    out.sort(key=lambda s: (-(s.callers + s.exposed), -s.exposed, -s.transitive, s.name))
    return out[:limit]


def blast_radius(
    store: FactStore, files: list[str], *, max_symbols: int = _MAX_SYMBOLS_PER_MODULE
) -> BlastRadius:
    """Compute the blast radius of touching ``files`` against the graph."""
    grounded = store.summary().get("grounded_nodes", 0) > 0
    call_graph = bool(store.edges_of_kind(EdgeKind.CALLS))
    modules = _module_nodes(store)
    by_file, spans = _file_index(store)

    mods: list[ModuleImpact] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    langs: set[str] = set()
    for raw in files:
        ref = str(raw).strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        node = _match_module(ref, modules, by_file)
        if node is None:
            unresolved.append(ref)
            continue
        if node.language:
            langs.add(node.language)
        importers = store.importers_of(node.id)
        names = tuple(sorted({i.name for i in importers}))[:_MAX_IMPORTER_NAMES]
        not_tests = {i.name for i in importers if not i.test}
        test_names = tuple(n for n in names if n not in not_tests)
        hotspots = tuple(_hotspots(store, node, limit=max_symbols)) if call_graph else ()
        mods.append(
            ModuleImpact(
                ref=ref,
                module=node.name,
                where=(node.provenance.file if node.provenance else "") or "",
                importers=len(importers),
                importer_names=names,
                hotspots=hotspots,
                spans=spans.get(node.id, 1),
                test_importer_names=test_names,
            )
        )
    return BlastRadius(tuple(mods), tuple(unresolved), call_graph, grounded, tuple(sorted(langs)))


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
        "languages": list(br.languages),
        "modules": [
            {
                "ref": m.ref,
                "module": m.module,
                "where": m.where,
                "importers": m.importers,
                "importer_names": list(m.importer_names),
                "test_importer_names": list(m.test_importer_names),
                "spans": m.spans,
                "hotspots": [
                    {
                        "name": s.name,
                        "where": s.where,
                        "callers": s.callers,
                        "transitive": s.transitive,
                        "exposed": s.exposed,
                        "product_callers": s.product_callers,
                    }
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
            # Name the subject of the count. "AzureServiceBusScheduler.cs — imported by 14"
            # was the namespace's fan-in pinned on whichever file resolved, and it made an
            # unrelated scheduler read as the hub of the change (NSS-1231).
            spans = int(m.get("spans") or 1)
            shared = f"module `{m['module']}` spans {spans} file(s); " if spans > 1 else ""
            lines.append(f"- `{m['ref']}` — {shared}{imp}")
            for s in m.get("hotspots") or []:
                extra = f", {s['transitive']} transitive" if s["transitive"] > s["callers"] else ""
                exposed = int(s.get("exposed") or 0)
                # Say it in words: a reader who sees "0 caller(s)" and nothing else
                # concludes the symbol is dead, which for a public route is backwards.
                route = f", serves {exposed} endpoint(s)" if exposed else ""
                where = f" — {s['where']}" if s.get("where") else ""
                lines.append(
                    f"    - high fan-in: `{s['name']}` ({s['callers']} caller(s){route}{extra}){where}"
                )
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
