"""ShipmentRegistrar — register shipped units with infodrift (Spine Phase 3, Seam 2)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import (
    CodeOntologyMapper,
    InfodriftError,
    InfodriftHttpClient,
    MappingLedger,
    OntologyClass,
    RegistrationRequest,
    RegistrationResult,
    ShipmentRegistrar,
    ShippedUnit,
    StaticDeployTopology,
    component_for_nodes,
)

IRI = "ex:FraudDetector"


class _RecordingRegistry:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.requests: list[RegistrationRequest] = []
        self._fail_on = fail_on or set()

    def register(self, request: RegistrationRequest) -> RegistrationResult:
        self.requests.append(request)
        if request.entity_key in self._fail_on:
            raise InfodriftError("boom")
        return RegistrationResult(entity_key=request.entity_key, ok=True, detail="registered")


_TOPOLOGY = StaticDeployTopology(
    {"FraudDetector": [("APAC", "CardTransactions"), ("EU", "CardTransactions")]}
)
_UNIT = ShippedUnit(
    component="FraudDetector", version="5", ontology_iri=IRI, trace_id="t-1", pr_url="http://pr/1"
)


def test_entity_keys_one_per_placement() -> None:
    reg = ShipmentRegistrar(_RecordingRegistry(), _TOPOLOGY)
    keys = [k.format() for k in reg.entity_keys(_UNIT)]
    assert keys == [
        "FraudDetector_v5::APAC::CardTransactions",
        "FraudDetector_v5::EU::CardTransactions",
    ]


def test_register_emits_unit_shipped_per_placement() -> None:
    sink = _RecordingRegistry()
    results = ShipmentRegistrar(sink, _TOPOLOGY).register(_UNIT)
    assert all(r.ok for r in results) and len(results) == 2
    r0 = sink.requests[0]
    assert r0.entity_key == "FraudDetector_v5::APAC::CardTransactions"
    assert r0.version == "5" and r0.model_version == "5"
    assert r0.baseline_id == "FraudDetector_v5::APAC::CardTransactions@ship"
    assert r0.ontology_iri == IRI and r0.trace_id == "t-1" and r0.pr_url == "http://pr/1"


def test_register_is_best_effort_per_placement() -> None:
    sink = _RecordingRegistry(fail_on={"FraudDetector_v5::APAC::CardTransactions"})
    results = ShipmentRegistrar(sink, _TOPOLOGY).register(_UNIT)
    assert len(results) == 2  # both attempted despite one failing
    assert results[0].ok is False and "boom" in results[0].detail
    assert results[1].ok is True


def test_no_placements_no_registrations() -> None:
    results = ShipmentRegistrar(_RecordingRegistry(), StaticDeployTopology({})).register(_UNIT)
    assert results == []


def test_component_for_nodes_picks_dominant_entity() -> None:
    b = FactBatch()
    p = Provenance(file="app/fraud.py", line=1)
    b.add_node(Node("py:app.fraud.FraudDetector", NodeKind.TYPE, "FraudDetector", "python", p))
    cands = CodeOntologyMapper([OntologyClass(IRI, "Fraud Detector")]).propose(FactStore(b))
    ledger = MappingLedger()
    ledger.confirm(cands[0])
    assert component_for_nodes(["py:app.fraud.FraudDetector"], ledger) == ("FraudDetector", IRI)


def test_component_for_nodes_none_when_unmapped() -> None:
    assert component_for_nodes(["py:app.x.Unmapped"], MappingLedger()) is None


def test_http_client_posts_unit_shipped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(202, json={"status": "accepted"})

    monkeypatch.setattr(httpx, "post", fake_post)
    req = RegistrationRequest(
        entity_key="FraudDetector_v5::APAC::CardTransactions",
        version="5",
        model_version="5",
        baseline_id="b@ship",
        ontology_iri=IRI,
    )
    res = InfodriftHttpClient("http://infodrift:8080").register(req)
    assert res.ok and captured["url"].endswith("/api/register")
    assert captured["body"]["entity_key"] == "FraudDetector_v5::APAC::CardTransactions"


def test_http_client_raises_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        return httpx.Response(500, text="nope")

    monkeypatch.setattr(httpx, "post", fake_post)
    req = RegistrationRequest(entity_key="E_v1::R::I", version="1", model_version="1", baseline_id="b")
    with pytest.raises(InfodriftError):
        InfodriftHttpClient("http://infodrift:8080").register(req)
