"""``SDLC_MAX_CRITERIA`` raises the criteria cap without a code change (B82).

``TOO_BIG`` is a hard stop at 12 stated criteria. An operator whose tickets are legitimately larger
had no way past it short of editing the source. Measured on 262 saved specs the cap never fires, so
the default stays; this is the escape hatch for the case where it does.
"""

from __future__ import annotations

import logging

import pytest

from orchestrator.evals.agent_corpus import score_gate
from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.validity import DEFAULT_MAX_CRITERIA, Verdict, assess


def _store() -> FactStore:
    batch = FactBatch()
    batch.add_node(Node("py:m0", NodeKind.MODULE, "m0", "python", Provenance("m0.py", 1)))
    return FactStore(batch)


def _spec(n: int) -> dict[str, object]:
    return {"title": "t", "summary": "s", "acceptance_criteria": [f"criterion {i}" for i in range(n)]}


def test_the_default_cap_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_MAX_CRITERIA", raising=False)
    assert DEFAULT_MAX_CRITERIA == 12
    assert assess(_spec(12), store=_store()).verdict is Verdict.PROCEED
    assert assess(_spec(13), store=_store()).verdict is Verdict.TOO_BIG


def test_the_knob_raises_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_MAX_CRITERIA", "20")
    assert assess(_spec(13), store=_store()).verdict is Verdict.PROCEED
    assert assess(_spec(21), store=_store()).verdict is Verdict.TOO_BIG


def test_the_knob_can_lower_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_MAX_CRITERIA", "3")
    assert assess(_spec(4), store=_store()).verdict is Verdict.TOO_BIG


def test_an_explicit_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_MAX_CRITERIA", "100")
    assert assess(_spec(13), store=_store(), max_criteria=12).verdict is Verdict.TOO_BIG


def test_the_refusal_names_the_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_MAX_CRITERIA", raising=False)
    finding = assess(_spec(15), store=_store()).findings[0]
    assert "cap is 12" in finding.evidence
    assert "SDLC_MAX_CRITERIA" in finding.evidence


@pytest.mark.parametrize("bad", ["abc", "", "0", "-4", "2.5"])
def test_a_bad_value_falls_back_to_the_default_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    monkeypatch.setenv("SDLC_MAX_CRITERIA", bad)
    with caplog.at_level(logging.WARNING):
        result = assess(_spec(13), store=_store())
    assert result.verdict is Verdict.TOO_BIG, "fell back to 12"
    if bad:  # an empty value is simply unset
        assert any("SDLC_MAX_CRITERIA" in r.getMessage() for r in caplog.records)


def test_the_eval_corpus_is_pinned_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval measures the gate, not whatever the operator's environment says."""
    monkeypatch.delenv("SDLC_MAX_CRITERIA", raising=False)
    baseline = [(r.case.ticket, r.actual) for r in score_gate(_store()).results]
    monkeypatch.setenv("SDLC_MAX_CRITERIA", "1000")
    assert [(r.case.ticket, r.actual) for r in score_gate(_store()).results] == baseline
