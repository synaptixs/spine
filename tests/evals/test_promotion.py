"""Promotion machinery for the persona-skill measurement (P3).

Decide → render → inject, all verified without an LLM call. The catalog-overlay
injection is exercised against real catalog source so a promoted skill actually
parses and becomes a selectable capability.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.catalog.models import CapabilityKind
from orchestrator.evals.promotion import (
    apply_to_catalog_source,
    capability_source,
    decision_from_ab,
    promoted_capability,
    promoted_ids_in_source,
    render_decisions_log,
)


def _ab_json(
    skill: str, baseline: float, treatment: float, *, runs: int = 18, margin: float = 0.10
) -> dict[str, Any]:
    """A minimal scripts/skill_ab.py result JSON."""
    return {
        "skill": skill,
        "provider": "claude",
        "model": "claude-sonnet-4-6",
        "verdict": {
            "promote": (treatment - baseline) >= margin,
            "baseline_rate": baseline,
            "treatment_rate": treatment,
            "delta": treatment - baseline,
            "margin": margin,
        },
        "baseline": {"metrics": {"runs": runs}},
        "treatment": {"metrics": {"runs": runs}},
    }


class TestDecision:
    def test_clears_bar(self) -> None:
        d = decision_from_ab(_ab_json("test-strategy", 0.4, 0.7))
        assert d.promote is True
        assert d.delta == pytest.approx(0.3)
        assert d.runs_per_arm == 18
        assert d.eval_id == "skill-ab:test-strategy"

    def test_holds_within_noise(self) -> None:
        d = decision_from_ab(_ab_json("convention-digest", 0.5, 0.55))
        assert d.promote is False
        assert "HOLD" in d.summary()

    def test_margin_override_reapplies_the_bar(self) -> None:
        data = _ab_json("test-strategy", 0.4, 0.5, margin=0.10)  # +10pp recorded → promote
        assert decision_from_ab(data).promote is True
        # a stricter bar at promotion time flips it to HOLD
        assert decision_from_ab(data, margin=0.20).promote is False


class TestCapability:
    def test_promoted_capability_carries_evidence(self) -> None:
        cap = promoted_capability(decision_from_ab(_ab_json("security-aware-coding", 0.3, 0.6)))
        assert cap.kind is CapabilityKind.SKILL
        assert cap.id == "security-aware-coding"
        assert cap.payload["eval"]["achieved"] == 0.6
        assert cap.payload["eval"]["min_score"] == 0.4  # baseline 0.3 + margin 0.10
        assert cap.selector.task_types == frozenset({"feature"})

    def test_hold_has_no_capability(self) -> None:
        with pytest.raises(ValueError):
            promoted_capability(decision_from_ab(_ab_json("test-strategy", 0.5, 0.5)))

    def test_capability_source_is_valid_python(self) -> None:
        src = capability_source(decision_from_ab(_ab_json("test-strategy", 0.4, 0.7)))
        assert "Capability(" in src and "CapabilityKind.SKILL" in src
        compile(src.strip().rstrip(","), "<snippet>", "eval")  # parses as an expression


_FAKE_CATALOG = """\
from orchestrator.catalog.models import Capability, CapabilityKind, CapabilitySelector

_SEED = (
    Capability("python-conventions", CapabilityKind.SKILL, "x", CapabilitySelector()),
)

_PROMOTED: tuple[Capability, ...] = ()


def default_catalog():
    return list(_SEED) + list(_PROMOTED)
"""


class TestCatalogInjection:
    def test_empty_ids(self) -> None:
        assert promoted_ids_in_source(_FAKE_CATALOG) == []

    def test_injects_only_promoted(self) -> None:
        decisions = [
            decision_from_ab(_ab_json("test-strategy", 0.4, 0.7)),  # promote
            decision_from_ab(_ab_json("convention-digest", 0.5, 0.52)),  # hold
        ]
        out = apply_to_catalog_source(_FAKE_CATALOG, decisions)
        assert "test-strategy" in promoted_ids_in_source(out)
        assert "convention-digest" not in promoted_ids_in_source(out)
        # the injected file still parses
        compile(out, "<catalog>", "exec")

    def test_idempotent(self) -> None:
        decisions = [decision_from_ab(_ab_json("test-strategy", 0.4, 0.7))]
        once = apply_to_catalog_source(_FAKE_CATALOG, decisions)
        twice = apply_to_catalog_source(once, decisions)
        assert once == twice  # re-applying adds nothing

    def test_appends_alongside_existing(self) -> None:
        first = apply_to_catalog_source(
            _FAKE_CATALOG, [decision_from_ab(_ab_json("test-strategy", 0.4, 0.7))]
        )
        second = apply_to_catalog_source(
            first, [decision_from_ab(_ab_json("security-aware-coding", 0.3, 0.6))]
        )
        ids = promoted_ids_in_source(second)
        assert "test-strategy" in ids and "security-aware-coding" in ids
        compile(second, "<catalog>", "exec")

    def test_missing_marker_raises(self) -> None:
        with pytest.raises(ValueError):
            promoted_ids_in_source("no overlay here")


class TestDecisionsLog:
    def test_records_winners_and_losers(self) -> None:
        decisions = [
            decision_from_ab(_ab_json("test-strategy", 0.4, 0.7)),  # promote
            decision_from_ab(_ab_json("convention-digest", 0.6, 0.6)),  # hold
        ]
        log = render_decisions_log(decisions, stamp="2026-06-24")
        assert "**PROMOTE**" in log and "HOLD" in log
        assert "test-strategy" in log and "convention-digest" in log
        assert "**Promoted:** test-strategy" in log
        assert "**Held (still candidates):** convention-digest" in log
