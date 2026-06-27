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

from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind

if TYPE_CHECKING:
    from orchestrator.catalog.profile import ProjectProfile

_GENERATED = (
    ".designer.cs",
    "reference.cs",
    "connected services",
    ".g.cs",
    "assemblyinfo",
    "/migrations/",
    ".generated.",
)
_FRAMEWORK_PREFIXES = ("System", "Microsoft", "java", "javax", "android")
_DATA_SHAPE_SUFFIXES = ("ViewModel", "Dto", "Entity", "Request", "Response", "Model", "Details", "Item")


def _area(name: str) -> str:
    """Group a module/namespace into a top-level area (first two dotted segments)."""
    return ".".join(name.split(".")[:2])


def _is_generated(node: Node) -> bool:
    f = (node.provenance.file if node.provenance else "").lower()
    return any(p in f for p in _GENERATED)


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
    auth_surface: list[str] = field(default_factory=list)
    recent_areas: list[tuple[str, int]] = field(default_factory=list)
    recommendations: list[tuple[str, str]] = field(default_factory=list)


_AUTH_KW = ("auth", "login", "permission", "role", "identity", "token", "jwt", "session", "secur")


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
    exts = (".cs", ".py", ".java", ".ts", ".tsx")
    changed = Counter(
        "/".join(line.strip().split("/")[:2])
        for line in out.stdout.splitlines()
        if line.strip().endswith(exts)
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
        if s and s.kind is NodeKind.MODULE and d and d.kind is NodeKind.TYPE:
            area_types[_area(s.name)] += 1
    area_funcs: Counter[str] = Counter(
        _area(n.id.split(":", 1)[-1]) for n in nodes if n.kind is NodeKind.FUNCTION
    )

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

    tested = {_area(n.id.split(":", 1)[-1]) for n in types if _is_test(n)}
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

    has_calls = any(e.kind is EdgeKind.CALLS for e in batch.edges)
    auth_surface = [
        n.name for n in types if any(k in n.name.lower() for k in _AUTH_KW) and not _is_generated(n)
    ][:12]

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
        auth_surface=auth_surface,
        recent_areas=_recent_areas(root),
    )
    state.recommendations = _recommend(state)
    return state


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
    if s.recommendations:
        out += ["**What to do next:**"]
        out += [f"- {text}" for _p, text in s.recommendations[:4]]
    return "\n".join(out) + "\n"


def _render_developer(s: CurrentState) -> str:
    o: list[str] = [
        "# Current State",
        "",
        "_Derived from the code (PKG) + profile. No LLM — re-run to refresh._",
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
        "",
        "## Architecture — layers",
        "",
        "| Layer | Components |",
        "|---|---|",
    ]
    o += [f"| {lyr} | {c} |" for lyr, c in s.layers.most_common()]
    if s.coupling:
        o += ["", "## Area dependency map", "", "```mermaid", "flowchart LR"]
        seen: set[str] = set()
        for (sa, da), c in s.coupling.most_common(9):
            for a in (sa, da):
                if a not in seen:
                    o.append(f'  {_mid(a)}["{a}"]')
                    seen.add(a)
            o.append(f"  {_mid(sa)} -->|{c}| {_mid(da)}")
        o += ["```"]
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


def build_current_state(root: Path | str, *, lens: str = "developer", refresh: bool = False) -> str:
    """Extract the PKG + profile for ``root`` and render the current-state markdown."""
    from orchestrator.catalog.profile import ProjectProfile
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.persistence import load_or_extract

    root_path = Path(root)
    batch = RepoCodeExtractor().extract(root_path) if refresh else load_or_extract(root_path)
    profile = ProjectProfile.from_repo(root_path)
    state = compute_current_state(batch, profile, root=root_path)
    return render_current_state(state, lens=lens)


__all__ = ["CurrentState", "build_current_state", "compute_current_state", "render_current_state"]
