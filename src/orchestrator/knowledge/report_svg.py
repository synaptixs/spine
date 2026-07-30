"""Deterministic inline-SVG architecture diagram for the shareable report.

Spine draws no random/force layout: positions are **computed, seeded, in Python** so the
same commit yields the same picture and two reports diff cleanly (invariant #3). This module
turns the bounded ``(components, edges)`` from ``architecture_graph`` into a layered SVG —
one column per zone, components stacked by weight, weighted dependency arrows between them.

Self-contained: emits plain ``<svg>`` with class hooks; all colour comes from the report's
inline ``<style>`` via CSS variables, so it themes light/dark with the rest of the page and
fetches nothing (invariant #5). No ``<script>``, no external marker refs.
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import TYPE_CHECKING

from orchestrator.knowledge.clustering import (
    communities_by_id,
    detect_communities,
    modularity,
    significant_edges,
)
from orchestrator.knowledge.current_state import architecture_graph, is_test_area

if TYPE_CHECKING:
    from orchestrator.knowledge.current_state import CurrentState

# Layout constants (px). Fixed geometry keeps the picture reproducible and bounded.
_BOX_W = 168
_BOX_H = 48
_VGAP = 18  # vertical gap between stacked components
_HGAP = 72  # horizontal gap between zone columns (room for cross-zone arrows)
_PAD = 20  # outer padding
_ZONE_HEAD = 30  # zone label band height
_LABEL_MAX = 22  # component-name truncation
_MAX_ROWS = 6  # a zone taller than this wraps into extra sub-columns (keeps it legible)


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _truncate(name: str) -> str:
    return name if len(name) <= _LABEL_MAX else name[: _LABEL_MAX - 1] + "…"


def _cluster_label(members: list[str], shown: int) -> str:
    """Name a cluster after the areas in it, not "Cluster 3".

    ``members`` is the whole community (which may include areas the bounded picture does not
    draw) **already ordered by weight**; ``shown`` is how many of them appear. Naming after the
    two heaviest members tells a reader what the group *is* — an ordinal does not, and
    alphabetical order does not either: it labelled this repo's build toolchain
    "agentic · cli" when its centre of mass is ``sdlc`` and ``pkg``.
    """
    head = " · ".join(m.rsplit(".", 1)[-1] for m in members[:2])
    if len(members) == 1:
        return head
    # One count, not two. An earlier form read "agentic · cli +9 (9 shown)", where the two
    # nines meant different things and looked like a contradiction.
    return f"{head} — {shown} of {len(members)}" if shown < len(members) else f"{head} — {len(members)}"


def _cluster_areas(state: CurrentState) -> tuple[dict[str, int], list[list[str]], float]:
    """Community assignment over the production coupling graph.

    Two filters before clustering, both of which change the answer materially:

    * **Test areas are dropped.** A test imports what it tests harder than anything else
      imports anything, so on the raw graph every community is one ``x`` + ``tests.x`` pair —
      structurally true and architecturally useless. ``architecture_graph`` drops test-origin
      edges for the same reason.
    * **Weak couplings are dropped** at the mean weight (see
      :func:`clustering.significant_edges`). Keeping them fuses 29 of this repo's 40
      production areas into one community at modularity 0.245; cutting them gives 11 / 9 / 2
      at 0.363.
    """
    production = {
        pair: w
        for pair, w in state.coupling.items()
        if not is_test_area(pair[0]) and not is_test_area(pair[1])
    }
    kept, _threshold = significant_edges(production)
    names = sorted({n for pair in production for n in pair})
    assignment = detect_communities(kept, nodes=names)
    return assignment, communities_by_id(assignment), modularity(kept, assignment)


def _border_point(cx: float, cy: float, tx: float, ty: float) -> tuple[float, float]:
    """Where the ray from box-center (cx,cy) toward (tx,ty) exits the box border.
    Straight AABB clip — keeps arrowheads on the box edge, not buried inside it."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = _BOX_W / 2, _BOX_H / 2
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def architecture_svg(state: CurrentState) -> str:
    """Render the architecture as one deterministic, theme-aware inline ``<svg>`` string.

    Returns ``""`` when there's nothing to draw. Layout is a pure function of ``state`` —
    no randomness, no clock — so the diagram is byte-identical for an identical commit.
    """
    nodes, edges = architecture_graph(state)
    if not nodes:
        return ""

    def _weight(a: str) -> int:
        return state.area_types.get(a, 0) * 3 + state.area_funcs.get(a, 0)

    # Group by structural community rather than by zone. `zone_of` reads the first segments of
    # a module NAME, which on a single-namespace repo puts every component in one band and says
    # nothing — measured here: all 18 drawn components land in the `orchestrator` zone. The
    # coupling graph knows more than the naming does.
    assignment, all_groups, q = _cluster_areas(state)
    members_of = {cid: group for cid, group in enumerate(all_groups)}

    by_cluster: dict[int, list[str]] = defaultdict(list)
    for a in nodes:
        # Unclustered components (no significant coupling) share a trailing group rather than
        # each taking a column; -1 sorts last.
        by_cluster[assignment.get(a, -1)].append(a)
    clusters = sorted(by_cluster)
    # Deterministic stacking within a column: weight desc, name asc (the name tiebreak is
    # what makes equal-weight rows reproducible rather than input-order dependent).
    for c in clusters:
        by_cluster[c].sort(key=lambda a: (-_weight(a), a))

    def _label(cid: int) -> str:
        if cid == -1:
            return "no strong coupling"
        # Heaviest first, name as the tiebreak — the same ordering the boxes stack by, so the
        # label names whatever sits at the top of the band.
        members = sorted(members_of.get(cid, by_cluster[cid]), key=lambda a: (-_weight(a), a))
        return _cluster_label(members, len(by_cluster[cid]))

    # Position every box. Each zone is a block of one-or-more sub-columns: components
    # stack down a column up to _MAX_ROWS, then wrap into the next sub-column of the same
    # zone — so a single-zone repo (everything under one namespace) grids out instead of
    # forming one absurdly tall column. Placement is column-major and fully determined by
    # the sorted order, so the layout stays reproducible (invariant #3).
    pos: dict[str, tuple[float, float]] = {}  # area -> (top-left x, y)
    blocks: list[tuple[int, int, int]] = []  # (cluster id, start_col, ncols)
    col_cursor = 0
    for c in clusters:
        members = by_cluster[c]
        ncols = (len(members) + _MAX_ROWS - 1) // _MAX_ROWS
        for idx, a in enumerate(members):
            col = col_cursor + idx // _MAX_ROWS
            row = idx % _MAX_ROWS
            x = _PAD + col * (_BOX_W + _HGAP)
            y = _PAD + _ZONE_HEAD + row * (_BOX_H + _VGAP)
            pos[a] = (x, y)
        blocks.append((c, col_cursor, ncols))
        col_cursor += ncols

    total_cols = col_cursor
    max_rows = max(min(len(by_cluster[c]), _MAX_ROWS) for c in clusters)
    width = _PAD * 2 + total_cols * _BOX_W + (total_cols - 1) * _HGAP
    height = _PAD * 2 + _ZONE_HEAD + max_rows * (_BOX_H + _VGAP) - _VGAP + 12

    parts: list[str] = [
        # Inline in HTML5 — no xmlns needed, and omitting it keeps the report free of any
        # http(s) URL so "self-contained, nothing fetched" stays trivially checkable.
        f'<svg class="arch" viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="xMidYMin meet" role="img" '
        f'aria-label="Architecture: {len(nodes)} components in {len(clusters)} '
        f'structural clusters, modularity {q:.2f}">',
        # A closed arrowhead marker, styled via CSS (fill:currentColor on the edge group).
        '<defs><marker id="ah" markerWidth="9" markerHeight="9" refX="7.5" refY="4" '
        'orient="auto" markerUnits="userSpaceOnUse">'
        '<path d="M0,0 L8,4 L0,8 Z" class="arch-head"/></marker></defs>',
    ]

    # Cluster bands (behind the boxes) + labels. A band spans all of its cluster's sub-columns
    # and is as tall as its fullest column. Class names stay `arch-zone*` so the report's
    # existing inline CSS keeps styling them — the grouping changed, the visual role did not.
    for c, start_col, ncols in blocks:
        rows = min(len(by_cluster[c]), _MAX_ROWS)
        zx = _PAD + start_col * (_BOX_W + _HGAP) - 8
        zw = ncols * _BOX_W + (ncols - 1) * _HGAP + 16
        zh = _ZONE_HEAD + rows * (_BOX_H + _VGAP) - _VGAP + 12
        parts.append(f'<rect class="arch-zone" x="{zx}" y="{_PAD}" width="{zw}" height="{zh}" rx="10"/>')
        parts.append(
            f'<text class="arch-zone-label" x="{zx + zw / 2}" y="{_PAD + 19}" '
            f'text-anchor="middle">{_e(_label(c))}</text>'
        )

    # Edges first, so boxes paint over the line ends.
    for (a, b), c in edges:
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        acx, acy = ax + _BOX_W / 2, ay + _BOX_H / 2
        bcx, bcy = bx + _BOX_W / 2, by + _BOX_H / 2
        x1, y1 = _border_point(acx, acy, bcx, bcy)
        x2, y2 = _border_point(bcx, bcy, acx, acy)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        parts.append(
            f'<line class="arch-edge" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'marker-end="url(#ah)"/>'
        )
        parts.append(
            f'<text class="arch-weight" x="{mx:.1f}" y="{my - 3:.1f}" text-anchor="middle">{c}</text>'
        )

    # Boxes with the component name + "T types · F fns".
    for a, (nx, ny) in pos.items():
        t, f = state.area_types.get(a, 0), state.area_funcs.get(a, 0)
        parts.append(
            f'<g class="arch-node"><rect x="{nx}" y="{ny}" width="{_BOX_W}" height="{_BOX_H}" rx="8"/>'
        )
        parts.append(
            f'<text class="arch-name" x="{nx + _BOX_W / 2}" y="{ny + 20}" text-anchor="middle">'
            f"{_e(_truncate(a))}</text>"
        )
        parts.append(
            f'<text class="arch-meta" x="{nx + _BOX_W / 2}" y="{ny + 36}" text-anchor="middle">'
            f"{t} types · {f} fns</text></g>"
        )

    parts.append("</svg>")
    return "".join(parts)


__all__ = ["architecture_svg"]
