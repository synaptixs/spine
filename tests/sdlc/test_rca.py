"""Root-cause analysis (C2): bug → grounded RCA report, deterministic + LLM."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.rca import build_rca, render_rca_md


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int, end: int) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line, end))


def _graph() -> FactBatch:
    b = FactBatch()
    auth = _node("py:auth", NodeKind.MODULE, "auth.py", "auth.py", 1, 40)
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py", 1, 20)
    authn = _node("py:auth.authenticate", NodeKind.FUNCTION, "authenticate", "auth.py", 10, 14)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5, 8)
    for n in (auth, web, authn, handler):
        b.add_node(n)
    b.add_edge(Edge("py:auth", "py:auth.authenticate", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:auth", EdgeKind.IMPORTS))  # web imports auth
    b.add_edge(Edge("py:web.handler", "py:auth.authenticate", EdgeKind.CALLS, Provenance("web.py", 6)))
    return b


_TRACEBACK = """Traceback (most recent call last):
  File "/srv/web.py", line 6, in handler
    return authenticate(token)
  File "/srv/auth.py", line 12, in authenticate
    raise ValueError("empty token")
ValueError: empty token
"""


async def test_deterministic_rca_grounds_fault_and_hypotheses() -> None:
    report = await build_rca(_TRACEBACK, store=FactStore(_graph()))
    assert report.exception == "ValueError: empty token"
    assert report.fault_site == "authenticate at auth.py:12"
    assert report.fault_module == "auth.py"
    assert any("py:web.handler" in c for c in report.callers)
    # a ValueError → the value-hint hypothesis; a caller → the input hypothesis
    claims = " ".join(h.claim for h in report.hypotheses)
    assert "invalid value" in claims.lower()
    assert "call site" in claims.lower()
    # regression surface picks up that web imports auth
    assert any("web" in s for s in report.regression_surface)
    assert report.llm is False


async def test_unresolved_bug_gives_low_confidence_hypothesis() -> None:
    trace = 'File "/usr/lib/python3.12/json/decoder.py", line 355, in raw_decode\nJSONDecodeError: x\n'
    report = await build_rca(trace, store=FactStore(_graph()))
    assert report.fault_site == ""
    assert any(h.confidence == "low" for h in report.hypotheses)


async def test_recent_churn_raises_regression_hypothesis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.rca._recently_changed_files", lambda root, **k: {"auth.py"})
    report = await build_rca(_TRACEBACK, store=FactStore(_graph()), root="/repo")
    assert report.recently_changed is True
    top = report.hypotheses[0]
    assert top.confidence == "high" and "regression" in top.claim.lower()


async def test_llm_enriches_hypotheses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda: "test-model")

    class _FakeLLM:
        async def complete(self, messages: Any, **kw: Any) -> Any:
            from orchestrator.core.llm.client import CompletionResult

            payload = {
                "hypotheses": [
                    {"claim": "token validation missing upstream", "evidence": ["e"], "confidence": "high"}
                ],
                "fix_approach": "guard the token in handler",
            }
            return CompletionResult(
                text=json.dumps(payload),
                model="m",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
                latency_ms=1.0,
            )

    report = await build_rca(_TRACEBACK, store=FactStore(_graph()), llm=_FakeLLM())
    assert report.llm is True
    assert report.hypotheses[0].claim == "token validation missing upstream"
    assert report.fix_approach == "guard the token in handler"


async def test_llm_failure_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda: "test-model")

    class _BadLLM:
        async def complete(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("provider down")

    report = await build_rca(_TRACEBACK, store=FactStore(_graph()), llm=_BadLLM())
    assert report.llm is False and report.fault_site == "authenticate at auth.py:12"


async def test_render_has_sections_and_stops_at_analysis() -> None:
    md = render_rca_md(await build_rca(_TRACEBACK, store=FactStore(_graph())))
    for section in (
        "# Root-cause analysis",
        "## Fault site",
        "## Root-cause hypotheses",
        "## Regression surface",
        "## Suggested fix approach",
    ):
        assert section in md
    assert "no code is changed" in md.lower()
