"""OntomeshGrounder + CompositeGrounder + Grounding Block (Spine Phase 1, Seam 1)."""

from __future__ import annotations

from orchestrator.spine import (
    Citation,
    CompositeGrounder,
    GroundingBlock,
    OntomeshGrounder,
    ReasonedAnswer,
)


class _FakeSearch:
    """In-memory OntomeshSearch: returns a canned answer, records the question."""

    def __init__(self, answer: ReasonedAnswer) -> None:
        self._answer = answer
        self.asked: list[str] = []

    def search(self, question: str) -> ReasonedAnswer:
        self.asked.append(question)
        return self._answer


class _Boom:
    def search(self, question: str) -> ReasonedAnswer:  # noqa: ARG002
        from orchestrator.spine import OntomeshError

        raise OntomeshError("down")


_SPEC = {
    "title": "Tighten fraud scoring",
    "acceptance_criteria": ["High-risk card transactions must be flagged"],
}

_OK = ReasonedAnswer(
    answer="A CardTransaction belongs to a Customer and is scored by the FraudDetector.",
    citations=(
        Citation(iri="ex:FraudDetector", label="Fraud Detector"),
        Citation(iri="ex:CardTransaction", label="Card Transaction", inferred=True),
    ),
    confidence=0.82,
    status="ok",
)


def test_grounding_block_render_includes_text_and_cited_iris() -> None:
    block = GroundingBlock(text=_OK.answer, citations=_OK.citations, confidence=0.82)
    out = block.render()
    assert "DOMAIN KNOWLEDGE (ontomesh" in out
    assert "FraudDetector" in out
    assert "<ex:FraudDetector>" in out
    assert "(inferred)" in out  # the inferred citation is flagged


def test_empty_block_renders_nothing() -> None:
    assert GroundingBlock(text="   ").render() == ""


def test_grounder_returns_cited_context_and_uses_spec_terms() -> None:
    fake = _FakeSearch(_OK)
    ctx = OntomeshGrounder(fake).context_for_spec(_SPEC)
    assert "FraudDetector" in ctx and "<ex:CardTransaction>" in ctx
    # the question was formed from the spec's prose + criteria
    assert "fraud" in fake.asked[0].lower() and "card transactions" in fake.asked[0].lower()


def test_grounder_degrades_on_low_confidence() -> None:
    weak = ReasonedAnswer(answer="maybe", confidence=0.2, status="ok")
    assert OntomeshGrounder(_FakeSearch(weak), min_confidence=0.5).context_for_spec(_SPEC) == ""


def test_grounder_degrades_on_non_ok_status() -> None:
    blocked = ReasonedAnswer(answer="secret", confidence=0.9, status="blocked")
    assert OntomeshGrounder(_FakeSearch(blocked)).context_for_spec(_SPEC) == ""


def test_grounder_degrades_on_error() -> None:
    assert OntomeshGrounder(_Boom()).context_for_spec(_SPEC) == ""


def test_grounder_empty_when_spec_has_no_prose() -> None:
    fake = _FakeSearch(_OK)
    assert OntomeshGrounder(fake).context_for_spec({}) == ""
    assert fake.asked == []  # never queried ontomesh for an empty spec


def test_composite_concatenates_code_and_domain_grounding() -> None:
    class _PKGish:
        def context_for_spec(self, spec: dict[str, object]) -> str:  # noqa: ARG002
            return "EXISTING CODE: class FraudDetector(...)"

    composite = CompositeGrounder([_PKGish(), OntomeshGrounder(_FakeSearch(_OK))])
    out = composite.context_for_spec(_SPEC)
    assert "EXISTING CODE" in out  # code-true
    assert "DOMAIN KNOWLEDGE" in out  # domain-true
    assert out.index("EXISTING CODE") < out.index("DOMAIN KNOWLEDGE")


def test_composite_survives_a_failing_grounder() -> None:
    class _Broken:
        def context_for_spec(self, spec: dict[str, object]) -> str:  # noqa: ARG002
            raise RuntimeError("boom")

    out = CompositeGrounder([_Broken(), OntomeshGrounder(_FakeSearch(_OK))]).context_for_spec(_SPEC)
    assert "DOMAIN KNOWLEDGE" in out
