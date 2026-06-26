"""OntomeshHttpClient — request shape + response parsing (Spine Phase 1)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.spine import OntomeshError, OntomeshHttpClient, ReasonedAnswer


def test_reasoned_answer_from_payload() -> None:
    ans = ReasonedAnswer.from_payload(
        {
            "answer": "x",
            "confidence": 0.7,
            "status": "ok",
            "citations": [
                {"iri": "ex:A", "label": "A"},
                {"label": "no-iri-dropped"},
                {"iri": "ex:B", "inferred": True},
            ],
        }
    )
    assert ans.answer == "x" and ans.confidence == 0.7
    assert [c.iri for c in ans.citations] == ["ex:A", "ex:B"]  # citation with no iri dropped
    assert ans.citations[1].inferred is True


def test_http_client_sends_question_and_flavor(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        captured.update(json)
        return httpx.Response(200, json={"answer": "ok", "confidence": 0.9, "status": "ok", "citations": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    ans = OntomeshHttpClient("http://ontomesh:5051", flavor="telco").search("how does fraud scoring work?")
    assert captured == {"question": "how does fraud scoring work?", "flavor": "telco"}
    assert ans.status == "ok" and ans.confidence == 0.9


def test_http_client_raises_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        return httpx.Response(404, text="reasoning search is not enabled")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(OntomeshError):
        OntomeshHttpClient("http://ontomesh:5051", flavor="telco").search("q")


def test_http_client_raises_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(OntomeshError):
        OntomeshHttpClient("http://ontomesh:5051", flavor="telco").search("q")
