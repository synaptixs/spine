"""Fault localization (C6): a stack trace / failing test → PKG nodes.

The entry point for root-cause work (C2): turn raw error text — a Python
traceback, a pytest failure — into graph coordinates. Every PKG node carries
``Provenance(file, line)``, so localization is mostly a *reverse* lookup: for
each trace frame, find the grounded symbol whose span covers that line, then
surface who calls the fault site (the paths that could have triggered it).

Deterministic, no LLM. Trace file paths are usually absolute (or relative to a
different root) while provenance is repo-relative, so matching is by path
suffix / basename, not equality. External frames (stdlib, site-packages) simply
don't resolve to a grounded repo node and drop out — which is what you want.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Node, NodeKind

# `  File "/abs/auth.py", line 12, in authenticate`
_PY_FRAME_RE = re.compile(r'\s*File "(?P<file>.+?)", line (?P<line>\d+), in (?P<func>.+?)\s*$')
# pytest short style: `auth.py:12: in authenticate`
_PYTEST_FRAME_RE = re.compile(r"(?P<file>[^\s:][^:]*\.[A-Za-z0-9_]+):(?P<line>\d+): in (?P<func>.+?)\s*$")
# Exception summary line (optionally prefixed by pytest's `E `).
_EXC_RE = re.compile(
    r"^(?:E\s+)?(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Iteration|Timeout|Failure)\b.*)$"
)


@dataclass(frozen=True)
class Frame:
    """One trace frame, and the PKG symbol it resolved to (if any)."""

    file: str  # as written in the trace
    line: int
    func: str  # function name from the trace
    node_id: str = ""  # resolved grounded node id, or ""
    where: str = ""  # resolved node provenance "file:line"
    module: str = ""  # owning module

    @property
    def resolved(self) -> bool:
        return bool(self.node_id)


@dataclass
class Localization:
    exception: str = ""  # e.g. "ValueError: empty token"
    frames: list[Frame] = field(default_factory=list)  # trace order (outermost → innermost)
    fault: Frame | None = None  # innermost in-repo frame — the best fault-site candidate
    callers: list[str] = field(default_factory=list)  # who calls the fault symbol ("id @ file:line")
    grounded: bool = False


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _extract_frames(text: str) -> tuple[list[tuple[str, int, str]], str]:
    """Pull (file, line, func) frames + the exception summary from trace text."""
    frames: list[tuple[str, int, str]] = []
    exception = ""
    for raw in text.splitlines():
        m = _PY_FRAME_RE.match(raw) or _PYTEST_FRAME_RE.match(raw)
        if m:
            frames.append((m.group("file"), int(m.group("line")), m.group("func")))
            continue
        em = _EXC_RE.match(raw)
        if em:
            exception = em.group("exc").strip()  # keep the last one (innermost)
    return frames, exception


def _owning_module(store: FactStore, node_id: str, parents: dict[str, str]) -> str:
    cur = node_id
    for _ in range(16):
        parent = parents.get(cur)
        if parent is None:
            break
        pnode = store.node(parent)
        if pnode is not None and pnode.kind is NodeKind.MODULE:
            return pnode.name
        cur = parent
    node = store.node(node_id)
    return (node.provenance.file if node and node.provenance else "") or ""


def _resolve_frame(store: FactStore, file: str, line: int) -> Node | None:
    """The smallest grounded symbol whose file matches ``file`` and span covers ``line``.

    Path match is lenient (suffix or basename) because a trace path is usually
    absolute while provenance is repo-relative.
    """
    norm = file.replace("\\", "/")
    base = _basename(norm)
    best: Node | None = None
    best_size = 1 << 62
    for node in store.nodes:
        prov = node.provenance
        if not node.grounded or prov is None:
            continue
        pf = prov.file.replace("\\", "/")
        matches = pf == norm or norm.endswith("/" + pf) or pf.endswith("/" + norm) or _basename(pf) == base
        if not matches:
            continue
        end = prov.end_line if prov.end_line is not None else prov.line
        if prov.line <= line <= end:
            size = end - prov.line
            if size < best_size:
                best, best_size = node, size
    return best


def localize_trace(text: str, *, store: FactStore) -> Localization:
    """Resolve a stack trace / failing-test output against the PKG. Deterministic."""
    raw_frames, exception = _extract_frames(text)
    parents = store.parents_index()

    frames: list[Frame] = []
    for file, line, func in raw_frames:
        node = _resolve_frame(store, file, line)
        if node is not None:
            # repo-relative file (from provenance) + the *trace* line (where it failed),
            # not the symbol's definition line — that's the actionable fault point.
            repo_file = node.provenance.file if node.provenance else file
            frames.append(
                Frame(
                    file=file,
                    line=line,
                    func=func,
                    node_id=node.id,
                    where=f"{repo_file}:{line}",
                    module=_owning_module(store, node.id, parents),
                )
            )
        else:
            frames.append(Frame(file=file, line=line, func=func))

    # The fault site is the innermost frame that resolved to an in-repo symbol.
    fault = next((f for f in reversed(frames) if f.resolved), None)
    callers: list[str] = []
    if fault is not None:
        callers = [f"{cs.caller.id} @ {cs.at}" for cs in store.callers_of(fault.node_id)]

    return Localization(
        exception=exception,
        frames=frames,
        fault=fault,
        callers=callers,
        grounded=store.summary().get("grounded_nodes", 0) > 0,
    )


def render_localization_md(loc: Localization) -> str:
    """Render the localization as markdown. Honest when frames don't resolve."""
    out: list[str] = ["# Fault localization\n"]
    if loc.exception:
        out.append(f"**Exception:** `{loc.exception}`\n")

    out.append("## Fault path")
    if loc.frames:
        out.append("_Trace order (innermost last). ✓ = resolved to a repo symbol; others are external._\n")
        for f in loc.frames:
            if f.resolved:
                out.append(f"- ✓ `{f.func}` — {f.where}" + (f" _(in {f.module})_" if f.module else ""))
            else:
                out.append(f"- ·  {f.func} — {f.file}:{f.line} _(external / unresolved)_")
    elif not loc.grounded:
        out.append("_No knowledge graph (greenfield/empty repo) — nothing to resolve against._")
    else:
        out.append("_No stack frames found in the input._")
    out.append("")

    out.append("## Likely fault site")
    if loc.fault is not None:
        out.append(
            f"`{loc.fault.func}` at {loc.fault.where}"
            + (f" (in {loc.fault.module})" if loc.fault.module else "")
        )
        if loc.callers:
            out.append("\n_Called by (potential trigger paths):_")
            out.extend(f"- {c}" for c in loc.callers[:15])
            if len(loc.callers) > 15:
                out.append(f"- …and {len(loc.callers) - 15} more")
        else:
            out.append("\n_No in-repo callers — likely an entry point or only externally invoked._")
    else:
        out.append(
            "_No trace frame resolved to a repo symbol — the fault may be in a dependency, "
            "or the trace names files outside this repo._"
        )
    out.append("")

    out.append("## Next step")
    out.append("Feed this into an investigation / RCA — `orchestrator investigate` grounds the fix.")
    return "\n".join(out) + "\n"


__all__ = ["Frame", "Localization", "localize_trace", "render_localization_md"]
