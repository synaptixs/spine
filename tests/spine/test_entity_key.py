"""EntityKey contract — the universal join's grammar (Spine Phase 0)."""

from __future__ import annotations

import pytest

from orchestrator.spine import EntityKey, EntityKeyError


def test_parse_roundtrip_reference_keys() -> None:
    for s in ("AMF_v2::RegionA::N11", "FraudDetector_v5::APAC::CardTransactions"):
        k = EntityKey.parse(s)
        assert k.format() == s
        assert str(k) == s


def test_parse_fields() -> None:
    k = EntityKey.parse("FraudDetector_v5::APAC::CardTransactions")
    assert k.component == "FraudDetector"
    assert k.version == "5"
    assert k.region == "APAC"
    assert k.interface == "CardTransactions"
    assert k.component_version == "FraudDetector_v5"


def test_component_may_contain_v_token() -> None:
    # rsplit on the LAST _v so a component name containing _v survives.
    k = EntityKey.parse("Edge_vCache_v3::EU::Http")
    assert k.component == "Edge_vCache"
    assert k.version == "3"


def test_version_is_string() -> None:
    assert EntityKey.parse("X_v2.1::R::I").version == "2.1"


@pytest.mark.parametrize(
    "bad",
    ["no-separators", "Comp::OnlyTwo::", "MissingVersion::R::I", "A_v1::R", "A_v1::R::I::extra"],
)
def test_invalid_raises(bad: str) -> None:
    assert EntityKey.is_valid(bad) is False
    with pytest.raises(EntityKeyError):
        EntityKey.parse(bad)


def test_construct_rejects_separator_in_field() -> None:
    with pytest.raises(EntityKeyError):
        EntityKey(component="A::B", version="1", region="R", interface="I")
