"""Current State — a team-facing snapshot synthesized from the PKG + profile.

Built *on top of* the Product Knowledge Graph (the engine): a deterministic,
recomputed-from-facts view of *what a project is today and how healthy it looks*,
rendered for two audiences:

- ``developer`` — architecture layers, areas, API surface, coupling, hotspots,
  test coverage, and prioritized recommendations.
- ``stakeholder`` — the same derivation in plain language, no jargon.

Language-agnostic: heuristics (controllers, layers, data-access) degrade gracefully
when a signal isn't present. Generated code (Designer/Reference/migrations) is
flagged and excluded from hotspots so it doesn't skew the picture. No LLM.

See ``docs/specs/current-state.md``. Results are a *view*, never authored — re-run to
refresh.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.knowledge.areas import area_of_name, zone_of
from orchestrator.knowledge.infrastructure import Infrastructure, detect_infrastructure
from orchestrator.pkg.doc_link import doc_drift, symbolish_drift
from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind

if TYPE_CHECKING:
    from orchestrator.catalog.profile import ProjectProfile

_GENERATED = (
    # C# / .NET
    ".designer.cs",
    "reference.cs",
    "connected services",
    ".g.cs",
    "assemblyinfo",
    "/migrations/",
    ".generated.",
    # Vendored / generated / build-output trees (language-agnostic conventions) — keep
    # third-party and generated code out of "what is THIS project" metrics. Matched as
    # lowercased path substrings, so they catch e.g. lib/.../external/cJSON.c, ipfw/objs/.
    "/external/",
    "/third_party/",
    "/third-party/",
    "/3rdparty/",
    "/vendor/",
    "/vendored/",
    "/subprojects/",
    "/contrib/",
    "/generated/",
    "/objs/",
    "/.deps/",
    "/node_modules/",
)
_FRAMEWORK_PREFIXES = ("System", "Microsoft", "java", "javax", "android")
_DATA_SHAPE_SUFFIXES = ("ViewModel", "Dto", "Entity", "Request", "Response", "Model", "Details", "Item")


# Area/zone grouping lives in `knowledge.areas` because the episteme's area pages group
# the same way. Two definitions would let `state` and the episteme show different
# architectures for the same commit; these aliases keep the local call sites readable.
_area = area_of_name
_zone = zone_of


def _is_generated_path(file: str) -> bool:
    # Normalize with a leading "/" so a `/vendor/`-style marker also matches a
    # vendored directory at the repo root (e.g. "subprojects/...", "third_party/...").
    f = "/" + file.lower().lstrip("/")
    return any(p in f for p in _GENERATED)


def _is_generated(node: Node) -> bool:
    return _is_generated_path(node.provenance.file if node.provenance else "")


def _is_framework(qualified: str) -> bool:
    return qualified.split(".")[0] in _FRAMEWORK_PREFIXES


def _is_interface(node: Node) -> bool:
    # C#/Java convention: IName. (A best-effort signal; harmless elsewhere.)
    return len(node.name) > 1 and node.name[0] == "I" and node.name[1].isupper()


def _layer(node: Node) -> str:
    name, nid = node.name, node.id.lower()
    if _is_interface(node):
        return "Interfaces / contracts"
    if name.endswith("Controller"):
        return "API · controllers"
    if name.endswith(("Repository", "Context", "Dao", "DbContext")) or ".data" in nid:
        return "Data access"
    if name.endswith(("Service", "Manager", "Handler", "Provider")) or ".biz" in nid or ".service" in nid:
        return "Business logic"
    if name.endswith(_DATA_SHAPE_SUFFIXES):
        return "Data shapes (VM/DTO/model)"
    if name in ("Program", "Startup") or name.endswith(("Middleware", "Filter")):
        return "App wiring / infra"
    return "Other"


@dataclass
class CurrentState:
    """Computed metrics for a repository's current state."""

    languages: list[str]
    framework: str | None
    test_runner: str | None
    counts: dict[str, int]
    namespaces: int
    areas: int
    layers: Counter[str]
    area_types: Counter[str]
    area_funcs: Counter[str]
    controllers: int
    endpoints: int
    busiest_controllers: list[tuple[str, int]]
    coupling: Counter[tuple[str, str]]
    external_deps: Counter[str]
    interfaces: int
    hotspots: list[tuple[str, int, str]]
    size_dist: Counter[str]
    tested_areas: int
    untested_top: list[tuple[str, int]]
    dup_names: list[tuple[str, int]]
    data_access: list[str]
    generated: int
    has_calls: bool
    call_hotspots: list[tuple[str, int]] = field(default_factory=list)
    auth_surface: list[str] = field(default_factory=list)
    recent_areas: list[tuple[str, int]] = field(default_factory=list)
    recommendations: list[tuple[str, str]] = field(default_factory=list)
    infrastructure: Infrastructure | None = None
    entry_points: list[str] = field(default_factory=list)
    modules: int = 0
    # Documentation (doc-ingestion): Doc nodes, symbol coverage, and drift.
    docs: int = 0
    documented_symbols: int = 0
    coverable_symbols: int = 0
    doc_drift_total: int = 0
    doc_drift_top: list[tuple[str, str]] = field(default_factory=list)  # (claim, doc)


_AUTH_KW = ("auth", "login", "permission", "role", "identity", "token", "jwt", "session", "secur")

# The symbol-vs-path drift filter is shared with the review-path finding (pkg.verifier), so it
# lives in pkg.doc_link. Aliased here for the local call site (and the existing test import).
_symbolish_drift = symbolish_drift


def _recent_areas(root: Path | None) -> list[tuple[str, int]]:
    """Top areas by recent churn, from `git log` (empty if no git/history)."""
    if root is None:
        return []
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--name-only", "--pretty=format:", "-n", "60"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    exts = (".cs", ".py", ".java", ".ts", ".tsx", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh")
    changed = Counter(
        "/".join(line.strip().split("/")[:2])
        for line in out.stdout.splitlines()
        if line.strip().endswith(exts) and not _is_generated_path(line.strip())
    )
    return changed.most_common(6)


def compute_current_state(
    batch: FactBatch, profile: ProjectProfile, *, root: Path | None = None
) -> CurrentState:
    """Synthesize the current-state metrics from PKG facts + the project profile."""
    nodes = [n for n in batch.nodes if not n.external]
    by_id = {n.id: n for n in nodes}
    counts = {k.value: 0 for k in NodeKind}
    for n in nodes:
        counts[n.kind.value] += 1

    contains = [e for e in batch.edges if e.kind is EdgeKind.CONTAINS]
    members: Counter[str] = Counter(
        e.src for e in contains if by_id.get(e.src) and by_id[e.src].kind is NodeKind.TYPE
    )
    types = [n for n in nodes if n.kind is NodeKind.TYPE]
    mods = [n for n in nodes if n.kind is NodeKind.MODULE]
    internal = {m.name.split(".")[0] for m in mods}

    layers = Counter(_layer(n) for n in types if not _is_generated(n))

    area_types: Counter[str] = Counter()
    for e in contains:
        s, d = by_id.get(e.src), by_id.get(e.dst)
        if s and s.kind is NodeKind.MODULE and d and d.kind is NodeKind.TYPE and not _is_generated(d):
            area_types[_area(s.name)] += 1
    # A function's area is the component it *lives in* (its owning module's path or
    # namespace), resolved by walking CONTAINS upward — not its bare symbol id. C/C++
    # function ids are symbols (`cpp:HSL2RGB`, `cpp:A::A`, `c:widget_score`), so
    # deriving the area from the id would make every function its own component (and,
    # via `_zone`, its own zone) — flooding the layout with thousands of one-fn entries.
    parent_of = {e.dst: e.src for e in contains}

    def _owning_module_name(nid: str) -> str | None:
        cur, seen = nid, set()
        while cur in parent_of and cur not in seen:
            seen.add(cur)
            p = by_id.get(parent_of[cur])
            if p is None:
                break
            if p.kind is NodeKind.MODULE:
                return p.name
            cur = p.id
        return None

    def _area_of(n: Node) -> str:
        # The component a node lives in: its owning module's name (dotted namespace /
        # file path). When that can't be resolved — e.g. a C++ method whose class is
        # declared in a `.h` parsed as C, so no `cpp:` type node owns it — fall back to
        # the source file it's defined in, never the bare symbol id (which for C/C++ is
        # a symbol like `cpp:HSL2RGB`, not a location, so it'd be its own component).
        mod = _owning_module_name(n.id)
        if mod is None and n.provenance is not None:
            mod = n.provenance.file
        return _area(mod) if mod else _area(n.id.split(":", 1)[-1])

    area_funcs: Counter[str] = Counter()
    for n in nodes:
        if n.kind is NodeKind.FUNCTION and not _is_generated(n):
            area_funcs[_area_of(n)] += 1

    controllers = [n for n in types if n.name.endswith("Controller") and not _is_generated(n)]
    ctrl_ids = {c.id for c in controllers}
    ep_by_ctrl: Counter[str] = Counter()
    for e in contains:
        if e.src in ctrl_ids and by_id.get(e.dst) and by_id[e.dst].kind is NodeKind.FUNCTION:
            ep_by_ctrl[e.src] += 1
    busiest = [(by_id[cid].name, n) for cid, n in ep_by_ctrl.most_common(8)]

    coupling: Counter[tuple[str, str]] = Counter()
    external: Counter[str] = Counter()
    for e in batch.edges:
        if e.kind is not EdgeKind.IMPORTS:
            continue
        src = by_id.get(e.src)
        if not src:
            continue
        dst_name = e.dst.split(":", 1)[-1]
        if _is_framework(dst_name) or dst_name.split(".")[0] not in internal:
            external[".".join(dst_name.split(".")[:2])] += 1
        else:
            sa, da = _area(src.name), _area(dst_name)
            if sa != da:
                coupling[(sa, da)] += 1

    interfaces = sum(1 for n in types if _is_interface(n) and not _is_generated(n))

    hotspots = [
        (by_id[t].name, c, str(by_id[t].provenance))
        for t, c in members.most_common()
        if not _is_generated(by_id[t])
    ][:10]

    size_dist: Counter[str] = Counter()
    for n in types:
        c = members.get(n.id, 0)
        bucket = (
            "god (>40)" if c > 40 else ">25" if c > 25 else ">10" if c > 10 else "1-10" if c > 0 else "marker"
        )
        size_dist[bucket] += 1

    def _is_test(n: Node) -> bool:
        f = (n.provenance.file if n.provenance else "").lower()
        return "test" in n.name.lower() or ".tests" in n.id.lower() or "/test" in f

    tested = {_area_of(n) for n in types if _is_test(n)}
    untested_top = [(a, c) for a, c in area_types.most_common(5) if a not in tested]

    dup_names = [
        (nm, c) for nm, c in Counter(n.name for n in types if not _is_generated(n)).most_common() if c > 1
    ][:10]

    repos = [n for n in types if n.name.endswith(("Repository", "Dao")) and not _is_generated(n)]
    data_access: list[str] = []
    if any("entityframework" in d.lower() for d in external) or any(
        n.name.endswith("DbContext") for n in types
    ):
        data_access.append("Entity Framework (DbContext)")
    if any("hibernate" in d.lower() or "sqlalchemy" in d.lower() for d in external):
        data_access.append("ORM (Hibernate/SQLAlchemy)")
    if any(d in external for d in ("System.Data",)) or (repos and not data_access):
        data_access.append("raw SQL / ADO.NET (no ORM)")

    call_in: Counter[str] = Counter(e.dst for e in batch.edges if e.kind is EdgeKind.CALLS)
    call_hotspots = [
        (by_id[fid].name, c)
        for fid, c in call_in.most_common()
        if fid in by_id and by_id[fid].kind is NodeKind.FUNCTION and not _is_generated(by_id[fid])
    ][:10]
    has_calls = any(e.kind is EdgeKind.CALLS for e in batch.edges)
    auth_surface = [
        n.name for n in types if any(k in n.name.lower() for k in _AUTH_KW) and not _is_generated(n)
    ][:12]

    doc_ids = {n.id for n in nodes if n.kind is NodeKind.DOC}
    mentions = [e for e in batch.edges if e.kind is EdgeKind.MENTIONS]
    documented = {e.dst for e in mentions if e.dst in by_id}
    coverable = {
        n.id
        for n in nodes
        if n.kind in (NodeKind.TYPE, NodeKind.FUNCTION, NodeKind.MODULE) and not _is_generated(n)
    }
    drift = [f for f in doc_drift(batch, root) if _symbolish_drift(f.mention)] if root and doc_ids else []

    state = CurrentState(
        languages=sorted(profile.languages),
        framework=profile.framework,
        test_runner=profile.test_runner,
        counts=counts,
        namespaces=len(mods),
        areas=len(area_types),
        layers=layers,
        area_types=area_types,
        area_funcs=area_funcs,
        controllers=len(controllers),
        endpoints=sum(ep_by_ctrl.values()),
        busiest_controllers=busiest,
        coupling=coupling,
        external_deps=external,
        interfaces=interfaces,
        hotspots=hotspots,
        size_dist=size_dist,
        tested_areas=len(tested),
        untested_top=untested_top,
        dup_names=dup_names,
        data_access=data_access,
        generated=sum(1 for n in types if _is_generated(n)),
        has_calls=has_calls,
        call_hotspots=call_hotspots,
        auth_surface=auth_surface,
        recent_areas=_recent_areas(root),
        infrastructure=detect_infrastructure(root) if root is not None else None,
        entry_points=_entry_points(root, nodes),
        modules=len(mods),
        docs=len(doc_ids),
        documented_symbols=len(documented & coverable),
        coverable_symbols=len(coverable),
        doc_drift_total=len(drift),
        doc_drift_top=[(f.mention, f.page_title) for f in drift[:8]],
    )
    state.recommendations = _recommend(state)
    return state


def _entry_points(root: Path | None, nodes: list[Node]) -> list[str]:
    """How the system is started: declared console scripts + ``main`` functions."""
    eps: list[str] = []
    if root is not None and (root / "pyproject.toml").is_file():
        try:
            import tomllib

            data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
            for name, target in (data.get("project", {}).get("scripts", {}) or {}).items():
                eps.append(f"`{name}` → {target} (console script)")
        except (OSError, ValueError, ModuleNotFoundError):
            pass
    for n in nodes:
        if n.kind is NodeKind.FUNCTION and n.name.lower() == "main" and n.provenance:
            eps.append(f"`main()` @ {n.provenance}")
        if len(eps) >= 8:
            break
    return eps


def _recommend(s: CurrentState) -> list[tuple[str, str]]:
    """Derive a prioritized to-do from the metrics. (priority, text)."""
    recs: list[tuple[str, str]] = []
    if s.tested_areas == 0 and s.counts.get("Type", 0) > 20:
        first = ", ".join(f"`{n}` ({c})" for n, c in s.busiest_controllers[:2]) or "the core areas"
        recs.append(("P1", f"Add automated tests — 0/{s.areas} areas covered. Start with {first}."))
    if "raw SQL / ADO.NET (no ORM)" in s.data_access:
        recs.append(("P1", "SQL-injection / data-layer security pass — raw SQL with no ORM is the top risk."))
    if s.controllers and s.endpoints:
        recs.append(
            ("P1", f"Audit endpoint auth — ~{s.endpoints} endpoints; verify each controller's access rules.")
        )
    fat = [(n, c) for n, c in s.busiest_controllers if c > 25]
    if fat:
        recs.append(
            ("P2", "Break up fat controllers: " + ", ".join(f"`{n}` ({c})" for n, c in fat[:4]) + ".")
        )
    gods = s.size_dist.get("god (>40)", 0)
    if gods:
        big = ", ".join(f"`{n}` ({c})" for n, c, _ in s.hotspots[:2])
        recs.append(("P2", f"Refactor {gods} god-classes (>40 members), e.g. {big}."))
    if s.dup_names:
        recs.append(
            ("P3", "De-duplicate type names: " + ", ".join(f"`{n}`×{c}" for n, c in s.dup_names[:4]) + ".")
        )
    return recs


def _mid(area: str) -> str:
    return "a" + re.sub(r"[^A-Za-z0-9]", "", area)[:24]


_ZONE_LABELS = {
    "src": "src — applications / services",
    "lib": "lib — libraries",
    "tests": "tests",
    "test": "tests",
    "app": "app",
}


def _zone_label(zone: str) -> str:
    return _ZONE_LABELS.get(zone, zone)


def architecture_graph(s: CurrentState) -> tuple[list[str], list[tuple[tuple[str, str], int]]]:
    """The bounded ``(components, weighted_edges)`` behind the architecture diagram.

    The strongest cross-component dependency edges (from the import / ``#include`` graph)
    plus the biggest components, capped so any visual stays legible (invariant #7). Shared
    by the mermaid block and the SVG renderer so the two never draw different architectures
    for the same commit. ``components`` is the draw order; each edge is ``((from, to), weight)``
    over that node set.
    """

    def _is_test_area(a: str) -> bool:
        return _zone(a) in ("tests", "test")

    # The architecture is the PRODUCTION structure — drop edges originating in test
    # code (test→lib isn't architecture) and test areas from the size-based picks.
    edges = [(ab, c) for ab, c in s.coupling.most_common(40) if not _is_test_area(ab[0])][:14]
    nodes: list[str] = []
    seen: set[str] = set()

    def add(a: str) -> None:
        if a not in seen:
            seen.add(a)
            nodes.append(a)

    for (a, b), _c in edges:
        add(a)
        add(b)
    for a, _c in s.area_types.most_common(24):
        if not _is_test_area(a):
            add(a)
    nodes = nodes[:18]
    nodeset = set(nodes)
    edges = [(ab, c) for ab, c in edges if ab[0] in nodeset and ab[1] in nodeset]
    return nodes, edges


def _architecture_mermaid(s: CurrentState) -> list[str]:
    """A system-architecture flowchart: the top components grouped into zone
    subgraphs, with the strongest cross-component dependency edges (from the
    `#include` / import graph). Bounded so it stays readable."""
    from collections import defaultdict

    nodes, edges = architecture_graph(s)
    nodeset = set(nodes)

    by_zone: dict[str, list[str]] = defaultdict(list)
    for a in nodes:
        by_zone[_zone(a)].append(a)

    out = ["```mermaid", "flowchart LR"]
    for zone in sorted(by_zone):
        zid = "z_" + re.sub(r"[^A-Za-z0-9]", "", zone)[:16]
        out.append(f'  subgraph {zid}["{_zone_label(zone)}"]')
        for a in sorted(by_zone[zone]):
            t, f = s.area_types.get(a, 0), s.area_funcs.get(a, 0)
            out.append(f'    {_mid(a)}["{a}<br/>{t} types · {f} fns"]')
        out.append("  end")
    for (a, b), c in edges:
        if a in nodeset and b in nodeset:
            out.append(f"  {_mid(a)} -->|{c}| {_mid(b)}")
    out.append("```")
    return out


def render_current_state(state: CurrentState, *, lens: str = "developer") -> str:
    """Render the current state as markdown for the given lens (developer/stakeholder)."""
    if lens == "stakeholder":
        return _render_stakeholder(state)
    return _render_developer(state)


def _render_stakeholder(s: CurrentState) -> str:
    lang = "C# / .NET" if "csharp" in s.languages else (", ".join(s.languages) or "unknown")
    app = "web API / service" if s.controllers else "library / service"
    health = "✅ has automated tests" if s.tested_areas else "⚠️ no automated tests detected"
    out = [
        "# Current State",
        "",
        f"This is a **{lang}** {app}. It's built from about **{s.counts.get('Type', 0)} components** "
        f"across ~**{s.areas} areas**, with **{s.counts.get('Function', 0)} operations**"
        + (
            f" and **~{s.endpoints} endpoints** across **{s.controllers} controllers**"
            if s.controllers
            else ""
        )
        + ".",
        "",
        f"**Health:** {health}.",
        "",
    ]
    if s.infrastructure and s.infrastructure.summary:
        out += [
            f"**To run it, you need:** {', '.join(s.infrastructure.summary[:6])}.",
            "",
        ]
    if s.recommendations:
        out += ["**What to do next:**"]
        out += [f"- {text}" for _p, text in s.recommendations[:4]]
    return "\n".join(out) + "\n"


def _app_type(s: CurrentState) -> str:
    if s.controllers or s.endpoints:
        return "web service / API"
    if s.framework:
        return f"{s.framework} application"
    if s.languages == ["c"] or s.languages == ["c", "h"]:
        return "C codebase"
    return "library / service"


def _overview(s: CurrentState) -> str:
    """A plain-language paragraph: what this is, what it needs, what to fix first."""
    lang = ", ".join(s.languages) or "code"
    parts = [
        f"This is a {lang} {_app_type(s)} — **{s.counts.get('Type', 0)} types** and "
        f"**{s.counts.get('Function', 0)} functions** across **{s.areas} components** "
        f"({s.modules} files)."
    ]
    if s.infrastructure and s.infrastructure.summary:
        parts.append(f"To run it you'll need {', '.join(s.infrastructure.summary[:5])}.")
    if not s.tested_areas:
        parts.append("No automated tests were detected — that's the first gap to close.")
    if s.recommendations:
        parts.append(f"Top priority: {s.recommendations[0][1]}")
    return " ".join(parts)


def _code_structure(s: CurrentState) -> list[str]:
    """Project layout (top components per zone), entry points, and public surface."""
    o = [
        "## Code structure",
        "",
        "_How the code is organized — components are top-level source directories or "
        "namespaces; entry points are how it starts._",
        "",
        f"{', '.join(s.languages) or 'code'} · {s.modules} files · {s.counts.get('Type', 0)} types · "
        f"{s.counts.get('Function', 0)} functions · {s.counts.get('Field', 0)} fields.",
        "",
        "Layout — top components by zone:",
    ]
    zones: dict[str, list[str]] = {}
    areas = set(s.area_types) | set(s.area_funcs)
    for a in sorted(areas, key=lambda a: s.area_types.get(a, 0) * 3 + s.area_funcs.get(a, 0), reverse=True):
        zones.setdefault(_zone(a), []).append(a)
    for zone in sorted(zones):
        top = zones[zone][:5]
        cells = ", ".join(
            f"`{a}` ({s.area_types.get(a, 0)} types, {s.area_funcs.get(a, 0)} fns)" for a in top
        )
        o.append(f"- **{zone}/** — {cells}")
    if s.entry_points:
        o += ["", "Entry points:"]
        o += [f"- {e}" for e in s.entry_points[:8]]
    return o


def _infrastructure_section(s: CurrentState) -> list[str]:
    inf = s.infrastructure
    if inf is None or inf.is_empty():
        return []
    o = [
        "## Infrastructure & runtime",
        "",
        "_What this codebase declares it needs to run and deploy — read from its manifests, "
        'build files, and container configs. Absence means "not declared here", not "unused"._',
        "",
    ]
    if inf.summary:
        o += [f"**To stand it up, you'll need:** {', '.join(inf.summary)}.", ""]
    for cat, items in inf.categories.items():
        o.append(f"- **{cat}:** {', '.join(items)}")
    return o


def _documentation_section(s: CurrentState) -> list[str]:
    """Doc-ingestion surface: how much of the code the docs describe, and where the
    docs claim code that the graph can't find (potential drift). Empty with no docs."""
    if not s.docs:
        return []
    pct = round(100 * s.documented_symbols / s.coverable_symbols) if s.coverable_symbols else 0
    o = [
        "",
        "## Documentation",
        "",
        "_Repo docs folded into the graph (`Doc` nodes + `MENTIONS` edges). Deterministic — "
        "a mention counts only when it binds to exactly one symbol._",
        "",
        f"- **{s.docs} doc{'s' if s.docs != 1 else ''}** ingested; they name "
        f"**{s.documented_symbols} of {s.coverable_symbols} symbols** ({pct}% doc coverage).",
    ]
    if s.doc_drift_total:
        o.append(
            f"- **{s.doc_drift_total} potential drift** — doc claims that reference code the "
            f"graph doesn't have (renamed/removed symbols, or prose the binder can't resolve)."
        )
        o += ["", "| Doc claims… | …in |", "|---|---|"]
        o += [f"| `{claim}` | {doc} |" for claim, doc in s.doc_drift_top]
        if s.doc_drift_total > len(s.doc_drift_top):
            o.append(f"| … | _+{s.doc_drift_total - len(s.doc_drift_top)} more_ |")
    return o


def _render_developer(s: CurrentState) -> str:
    o: list[str] = [
        "# Current State",
        "",
        "_Derived from the code (PKG) + profile. No LLM — re-run to refresh._",
        "",
        "## Overview",
        "",
        _overview(s),
        "",
    ]
    o += [
        "## At a glance",
        "",
        f"- Stack: `{', '.join(s.languages) or '—'}` · framework `{s.framework or '—'}`"
        f" · tests `{s.test_runner or 'none detected'}`",
        f"- Size: {s.namespaces} namespaces (≈{s.areas} areas) · "
        + " · ".join(f"{v} {k.lower()}s" for k, v in s.counts.items() if v),
        f"- API surface: {s.controllers} controllers · ~{s.endpoints} endpoints"
        if s.controllers
        else "- API surface: none detected",
        f"- Data access: {', '.join(s.data_access) or '—'}",
        f"- Call graph: {'available' if s.has_calls else 'not extracted for this language yet'}",
    ]
    infra = _infrastructure_section(s)
    if infra:
        o += ["", *infra]
    o += ["", *_code_structure(s)]
    if s.coupling:
        o += [
            "",
            "## System architecture",
            "",
            "_Components (top areas) grouped by zone; arrows are dependency strength "
            "(import / `#include` count)._",
            "",
        ]
        o += _architecture_mermaid(s)
        o += [
            "",
            "### Component dependencies (strongest)",
            "",
            "| From | → | To | Strength |",
            "|---|---|---|---|",
        ]
        o += [f"| `{a}` | → | `{b}` | {c} |" for (a, b), c in s.coupling.most_common(10)]
    o += ["", "## Architecture — layers", "", "| Layer | Components |", "|---|---|"]
    o += [f"| {lyr} | {c} |" for lyr, c in s.layers.most_common()]
    if s.busiest_controllers:
        o += ["", "## API surface — busiest controllers", "", "| Controller | Endpoints |", "|---|---|"]
        o += [f"| `{n}` | {c} |" for n, c in s.busiest_controllers]
    o += ["", "## Areas — size (top 12)", "", "| Area | Types | Functions |", "|---|---|---|"]
    o += [f"| {a} | {c} | {s.area_funcs.get(a, 0)} |" for a, c in s.area_types.most_common(12)]
    if s.external_deps:
        o += ["", "## External dependencies (top)", ""]
        o += [f"- {d} ({c})" for d, c in s.external_deps.most_common(8)]
    o += [
        "",
        "## Complexity",
        "",
        "Size distribution: " + " · ".join(f"{k}: {v}" for k, v in s.size_dist.most_common()),
        "",
        "| Largest component | Members | Location |",
        "|---|---|---|",
    ]
    o += [f"| `{n}` | {c} | {loc} |" for n, c, loc in s.hotspots]
    if s.call_hotspots:
        o += [
            "",
            "## Call graph — most-depended-upon functions",
            "",
            "_Functions with the most incoming calls — the code most other code relies on._",
            "",
            "| Function | Called from (sites) |",
            "|---|---|",
        ]
        o += [f"| `{n}` | {c} |" for n, c in s.call_hotspots]
    o += [
        "",
        "## Test coverage",
        "",
        f"- **{s.tested_areas} of {s.areas} areas** have any test type."
        + (
            " Largest untested: " + ", ".join(f"{a} ({c})" for a, c in s.untested_top)
            if s.untested_top
            else ""
        ),
    ]
    o += _documentation_section(s)
    if s.recent_areas:
        o += [
            "",
            "## Recent activity (last ~60 commits)",
            "",
            "Most-churned areas: " + ", ".join(f"{a} ({c})" for a, c in s.recent_areas),
        ]
    if s.auth_surface:
        o += [
            "",
            "## Security surface",
            "",
            f"- {len(s.auth_surface)} auth/security-related types (by name): "
            + ", ".join(f"`{n}`" for n in s.auth_surface),
            "- ⚠️ Attribute-level auth (`[Authorize]`/decorators) isn't extracted yet — "
            "endpoint access rules can't be confirmed from the graph alone.",
        ]
    if s.dup_names:
        o += [
            "",
            "## Naming smells",
            "",
            "Duplicate type names: " + ", ".join(f"`{n}`×{c}" for n, c in s.dup_names[:8]),
        ]
    if s.recommendations:
        o += ["", "## Recommendations — prioritized actions", ""]
        for pri, text in s.recommendations:
            o.append(f"- **{pri}** — {text}")
    o += [
        "",
        "## Caveats",
        "- Heuristic synthesis from naming + structure. "
        + ("Call graph not yet available for this language." if not s.has_calls else ""),
        f"- {s.generated} generated types flagged + excluded from hotspots.",
    ]
    return "\n".join(o) + "\n"


def load_current_state(
    root: Path | str, *, refresh: bool = False, sql_dialect: str | None = None
) -> tuple[CurrentState, FactBatch]:
    """Extract the PKG + profile for ``root`` and return the structured ``CurrentState``.

    The raw ``FactBatch`` is returned alongside so callers that need graph-level
    provenance (grounded/edge counts, the HTML report's blast-radius spotlight) can
    reuse it without a second extraction. Rendering (markdown, HTML) is a separate step.
    """
    from orchestrator.catalog.profile import ProjectProfile
    from orchestrator.pkg.data_layer_link import link_data_layer
    from orchestrator.pkg.doc_link import link_docs
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.migrations import apply_migrations
    from orchestrator.pkg.persistence import load_or_extract

    root_path = Path(root)
    # A pinned --dialect changes SQL extraction, so bypass the sha-keyed cache.
    if refresh or sql_dialect is not None:
        batch = RepoCodeExtractor(sql_dialect=sql_dialect).extract(root_path)
    else:
        batch = load_or_extract(root_path)
    # A4 (fold ordered migrations → current schema) then A3 (schema is
    # authoritative over ORM-inferred entities). Both no-op without SQL.
    batch = apply_migrations(batch, root_path)
    batch = link_data_layer(batch)
    # Fold the repo's text docs into the graph (Doc nodes + MENTIONS edges); no-op with no docs.
    batch = link_docs(batch, root_path)
    profile = ProjectProfile.from_repo(root_path)
    state = compute_current_state(batch, profile, root=root_path)
    return state, batch


def build_current_state(
    root: Path | str, *, lens: str = "developer", refresh: bool = False, sql_dialect: str | None = None
) -> str:
    """Extract the PKG + profile for ``root`` and render the current-state markdown."""
    state, _batch = load_current_state(root, refresh=refresh, sql_dialect=sql_dialect)
    return render_current_state(state, lens=lens)


__all__ = [
    "CurrentState",
    "architecture_graph",
    "build_current_state",
    "compute_current_state",
    "load_current_state",
    "render_current_state",
]
