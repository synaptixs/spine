"""The architecture SVG groups by structural cluster, not by name prefix.

`zone_of` reads the first segments of a module *name*. On a single-namespace repo that puts
every component in one band and communicates nothing — measured on this repo, all 18 drawn
components share the `orchestrator` zone. The coupling graph knows more than the naming does.
"""

from __future__ import annotations

import re
from collections import Counter

from orchestrator.knowledge.current_state import CurrentState
from orchestrator.knowledge.report_svg import _cluster_label, architecture_svg


def _labels(svg: str) -> list[str]:
    return re.findall(r"arch-zone-label[^>]*>([^<]+)<", svg)


def test_label_names_the_cluster_rather_than_numbering_it() -> None:
    """ "registry · temporal" tells a reader what the group is; "Cluster 3" does not."""
    assert _cluster_label(["a.registry", "a.temporal"], 2) == "registry · temporal — 2"


def test_label_carries_one_count_not_two() -> None:
    """An earlier form read "agentic · cli +9 (9 shown)" — two nines meaning different
    things, which reads as a contradiction."""
    label = _cluster_label(["a.agentic", "a.cli"] + [f"a.m{i}" for i in range(9)], 9)
    assert label == "agentic · cli — 9 of 11"
    assert "+" not in label and "(" not in label


def test_singleton_cluster_is_just_its_name() -> None:
    assert _cluster_label(["a.approval"], 1) == "approval"


def test_label_omits_the_partial_count_when_everything_is_drawn() -> None:
    assert _cluster_label(["a.x", "a.y", "a.z"], 3) == "x · y — 3"


def _state(coupling: Counter[tuple[str, str]], areas: list[str]) -> CurrentState:
    """A CurrentState carrying only what the architecture renderer reads."""
    from dataclasses import fields

    kwargs: dict[str, object] = {}
    for f in fields(CurrentState):
        if f.name == "coupling":
            kwargs[f.name] = coupling
        elif f.name in ("area_types", "area_funcs"):
            kwargs[f.name] = Counter(dict.fromkeys(areas, 3))
        elif f.type is not None and "Counter" in str(f.type):
            kwargs[f.name] = Counter()
        elif "list" in str(f.type):
            kwargs[f.name] = []
        elif "dict" in str(f.type):
            kwargs[f.name] = {}
        elif "int" in str(f.type):
            kwargs[f.name] = 0
        elif "float" in str(f.type):
            kwargs[f.name] = 0.0
        else:
            kwargs[f.name] = ""
    return CurrentState(**kwargs)  # type: ignore[arg-type]


def test_two_coupled_groups_render_as_two_bands() -> None:
    """The whole point: components that depend on each other end up in the same band even
    though every name here shares one prefix, so `zone_of` would draw a single band."""
    coupling: Counter[tuple[str, str]] = Counter()
    for a, b in [("app.a1", "app.a2"), ("app.a2", "app.a3"), ("app.a3", "app.a1")]:
        coupling[(a, b)] = 20
    for a, b in [("app.b1", "app.b2"), ("app.b2", "app.b3"), ("app.b3", "app.b1")]:
        coupling[(a, b)] = 20
    coupling[("app.a1", "app.b1")] = 1  # weak bridge, cut before clustering

    areas = ["app.a1", "app.a2", "app.a3", "app.b1", "app.b2", "app.b3"]
    svg = architecture_svg(_state(coupling, areas))
    assert svg, "expected a diagram"
    assert len(_labels(svg)) == 2, f"expected two cluster bands, got {_labels(svg)}"


def test_render_is_byte_identical_for_identical_input() -> None:
    """Invariant #3 — a picture that redraws differently for the same commit cannot be
    diffed, which is the property the whole no-force-layout rule protects."""
    coupling: Counter[tuple[str, str]] = Counter(
        {("app.x", "app.y"): 5, ("app.y", "app.z"): 5, ("app.z", "app.x"): 5}
    )
    state = _state(coupling, ["app.x", "app.y", "app.z"])
    assert architecture_svg(state) == architecture_svg(state)


def test_empty_state_renders_nothing_rather_than_a_broken_svg() -> None:
    assert architecture_svg(_state(Counter(), [])) == ""
