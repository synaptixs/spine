from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.core.claim import Claim, ClaimType, Evidence


def _evidence() -> list[Evidence]:
    return [Evidence(artifact_id="art_123")]


def test_valid_claim() -> None:
    claim = Claim(
        id="c_arr_growth",
        statement="ARR grew 12% QoQ.",
        claim_type=ClaimType.METRIC,
        supporting_artifacts=_evidence(),
        metric_values={"arr_growth_qoq": 0.12},
        confidence=0.85,
    )
    assert claim.confidence == 0.85


def test_invalid_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Claim(
            id="ARR_GROWTH",
            statement="x",
            claim_type=ClaimType.METRIC,
            supporting_artifacts=_evidence(),
            confidence=0.5,
        )


def test_empty_supporting_artifacts_rejected() -> None:
    with pytest.raises(ValidationError):
        Claim(
            id="c_x",
            statement="x",
            claim_type=ClaimType.QUALITATIVE,
            supporting_artifacts=[],
            confidence=0.5,
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1, 2.0])
def test_confidence_out_of_range_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Claim(
            id="c_x",
            statement="x",
            claim_type=ClaimType.METRIC,
            supporting_artifacts=_evidence(),
            confidence=confidence,
        )
