"""Deterministic architecture SVG — bounded, non-overlapping, theme-agnostic, no fetches."""

from __future__ import annotations

import re
from collections import Counter

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.knowledge.current_state import CurrentState, compute_current_state
from orchestrator.knowledge.report_svg import _BOX_H, _BOX_W, _MAX_ROWS, architecture_svg
from orchestrator.pkg.facts import FactBatch

_PROFILE_AREAS = 30
_PROFILE = ProjectProfile(
    languages=frozenset({"python"}),
    framework=None,
    has_db=False,
    has_migrations=False,
    test_runner=None,
    task_type="feature",
)


def _many_area_state() -> CurrentState:
    """A state with more components than fit one column, to exercise grid-wrapping."""
    s = compute_current_state(FactBatch(), _PROFILE)
    # One zone ("pkg") with many equal-ish components → must wrap into sub-columns.
    s.area_types = Counter({f"pkg.mod{i:02d}": 10 - (i % 5) for i in range(_PROFILE_AREAS)})
    s.area_funcs = Counter({f"pkg.mod{i:02d}": 3 for i in range(_PROFILE_AREAS)})
    s.coupling = Counter({("pkg.mod00", "pkg.mod01"): 5, ("pkg.mod02", "pkg.mod03"): 3})
    return s


def _boxes(svg: str) -> list[tuple[float, float]]:
    return [
        (float(x), float(y))
        for x, y in re.findall(r'<g class="arch-node"><rect x="([\d.]+)" y="([\d.]+)"', svg)
    ]


def test_empty_state_draws_nothing() -> None:
    s = _many_area_state()
    s.area_types = Counter()
    s.area_funcs = Counter()
    s.coupling = Counter()
    assert architecture_svg(s) == ""


def test_deterministic() -> None:
    s = _many_area_state()
    assert architecture_svg(s) == architecture_svg(s)


def test_self_contained_svg() -> None:
    svg = architecture_svg(_many_area_state())
    assert svg.startswith("<svg")
    for needle in ("http://", "https://", "<script", "<image", "xlink"):
        assert needle not in svg


def test_boxes_in_bounds_and_non_overlapping() -> None:
    svg = architecture_svg(_many_area_state())
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert m
    w, h = float(m.group(1)), float(m.group(2))
    boxes = _boxes(svg)
    assert boxes
    for x, y in boxes:
        assert x >= 0 and y >= 0 and x + _BOX_W <= w and y + _BOX_H <= h
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            ax, ay = boxes[i]
            bx, by = boxes[j]
            assert not (abs(ax - bx) < _BOX_W and abs(ay - by) < _BOX_H), "boxes overlap"


def test_single_zone_wraps_into_grid() -> None:
    # 30 components in one zone must not stack into one column taller than _MAX_ROWS.
    svg = architecture_svg(_many_area_state())
    ys = {y for _x, y in _boxes(svg)}
    xs = {x for x, _y in _boxes(svg)}
    assert len(ys) <= _MAX_ROWS  # at most _MAX_ROWS distinct rows
    assert len(xs) > 1  # wrapped into multiple sub-columns
