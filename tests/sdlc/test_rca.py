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
    # Patched at the git boundary in `sdlc.churn`, which both the bug path and the enhancement
    # profile's churn node now share — one implementation, so they cannot answer differently.
    monkeypatch.setattr("orchestrator.sdlc.churn.recently_changed_files", lambda root, **k: {"auth.py"})
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


def test_the_fault_site_carries_the_source_at_its_line() -> None:
    """An RCA whose hypotheses are templates over code nobody quoted asks the reader to take
    the ranking on faith. Ranking *by evidence* means the evidence is on the page."""
    from orchestrator.sdlc.rca import RCAReport, render_rca_md

    md = render_rca_md(
        RCAReport(
            fault_site="f at a.py:1",
            fault_module="a",
            fault_source="```python\ndef f():\n    return d[k]\n```",
        )
    )
    assert "## Fault site" in md and "return d[k]" in md


def test_an_unlocalized_rca_has_no_excerpt_and_says_why() -> None:
    from orchestrator.sdlc.rca import RCAReport, render_rca_md

    md = render_rca_md(RCAReport())
    assert "```" not in md.split("## Root-cause")[0]
    assert "Not localized to a repo symbol" in md


# ---- B161 / SSPN-377: recency and provenance must not overstate ------------------------------
#
# SSPN-264 (no traceback) got "[high] Recent change to server.py — likely a regression" because the
# file was touched in the last 40 commits, as it is in almost every release; and "named by the
# ticket" for paths only the intake model wrote. A busy file named in prose is not a regression
# signal, and a model's inference is not the reporter's statement.


async def test_b161_a_named_file_that_is_merely_busy_is_low_not_high(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.churn.recently_changed_files", lambda root, **k: {"auth.py"})
    report = await build_rca(
        "Login fails for some users; see auth.py.", store=FactStore(_graph()), root="/repo"
    )

    assert report.recently_changed is True  # the fact is kept
    assert not any(h.confidence == "high" for h in report.hypotheses)
    recent = next(h for h in report.hypotheses if "40 commits" in h.claim)
    assert recent.confidence == "low"
    assert "likely a regression" not in recent.claim


async def test_b161_a_traced_fault_in_a_busy_file_is_still_high(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.churn.recently_changed_files", lambda root, **k: {"auth.py"})
    report = await build_rca(_TRACEBACK, store=FactStore(_graph()), root="/repo")

    assert report.fault_origin == "traced"
    assert report.hypotheses[0].confidence == "high"


async def test_b161_a_path_only_the_spec_names_is_not_called_the_tickets() -> None:
    report = await build_rca(
        "Calls are slow. The cache belongs in auth.py.",
        store=FactStore(_graph()),
        source_text="Every call takes three seconds on a large repository.",
    )

    assert report.fault_origin == "spec"
    named = next(h for h in report.hypotheses if "auth.py" in h.claim)
    assert "named by the ticket" not in named.claim
    assert "intake spec" in named.claim


async def test_b161_a_path_the_ticket_names_is_the_tickets() -> None:
    report = await build_rca(
        "Calls are slow. The cache belongs in auth.py.",
        store=FactStore(_graph()),
        source_text="Every call re-reads the graph; see src/auth.py and auth.py.",
    )

    assert report.fault_origin == "ticket"
    assert any("named by the ticket" in h.claim for h in report.hypotheses)


async def test_b161_without_a_source_the_text_read_is_the_tickets() -> None:
    """The `rca` command and MCP `root_cause` read the reporter's own text: there is no intake step."""
    report = await build_rca("Login fails; see auth.py.", store=FactStore(_graph()))

    assert report.fault_origin == "ticket"
    assert any("named by the ticket" in h.claim for h in report.hypotheses)


def test_b161_a_ticket_names_a_file_only_on_a_directory_boundary() -> None:
    from orchestrator.sdlc.rca import _named_in

    assert _named_in("src/pkg/auth.py", "see pkg/auth.py") is True
    assert _named_in("src/pkg/auth.py", "see src/pkg/auth.py") is True
    assert _named_in("src/oauth.py", "see auth.py") is False
    assert _named_in("src/pkg/auth.py", "nothing named here") is False


# ---- B161 review: the provenance label must not itself state something false -----------------


def _modules(*files: str, language: str = "python") -> FactStore:
    b = FactBatch()
    for i, f in enumerate(files):
        b.add_node(
            Node(id=f"x:{i}", kind=NodeKind.MODULE, name=f, language=language, provenance=Provenance(f, 1, 9))
        )
    return FactStore(b)


async def test_b161_a_ticket_naming_a_file_outside_the_suffix_list_is_the_tickets() -> None:
    """`_named_in` read the ticket through `named_paths`, which knows no `.js`: a file the ticket
    named was labelled "the intake spec (a model wrote it)"."""
    report = await build_rca(
        "Fix the parser in calls.js.",
        store=_modules("src/calls.js", language="javascript"),
        source_text="The parser in calls.js drops arguments.",
    )

    assert report.fault_origin == "ticket"
    named = next(h for h in report.hypotheses if "calls.js" in h.claim)
    assert "named by the ticket" in named.claim


async def test_b161_no_ticket_text_is_not_the_ticket() -> None:
    """A hand-written `--spec` keeps no ticket text. Nothing can be checked, so nothing is claimed."""
    report = await build_rca("Login fails; see auth.py.", store=FactStore(_graph()), source_text="")

    assert report.fault_origin == "unchecked"
    named = next(h for h in report.hypotheses if "auth.py" in h.claim)
    assert "named by the ticket" not in named.claim
    assert "a model wrote it" not in named.claim
    assert "no ticket text" in named.claim


async def test_b161_each_named_file_carries_its_own_origin() -> None:
    """The fault line called `a.py` the spec's; the hypothesis called `a.py`, `b.py` the ticket's."""
    report = await build_rca(
        "Touch a.py and b.py.", store=_modules("a.py", "b.py"), source_text="The bug is in b.py."
    )

    assert (report.fault_module, report.fault_origin) == ("a.py", "spec")
    named = next(h for h in report.hypotheses if "a.py" in h.claim)
    assert "`b.py` — named by the ticket" in named.claim
    assert "`a.py` — named by the intake spec" in named.claim


async def test_b161_hypotheses_are_listed_in_confidence_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.churn.recently_changed_files", lambda root, **k: {"auth.py"})
    report = await build_rca("Login fails; see auth.py.", store=FactStore(_graph()), root="/repo")

    rank = {"high": 0, "medium": 1, "low": 2}
    order = [rank[h.confidence] for h in report.hypotheses]
    assert len(order) >= 2 and order == sorted(order)


def test_b161_named_in_takes_any_suffix_and_either_length() -> None:
    from orchestrator.sdlc.rca import _named_in

    assert _named_in("src/calls.js", "the parser in calls.js.") is True
    assert _named_in("pkg/auth.py", "see svc/pkg/auth.py") is True  # a sub-root plan
    assert _named_in("src/pkg/auth.py", r"see pkg\auth.py") is True
    assert _named_in("src/pkg/auth.py", "see mypkg/auth.py") is False
    assert _named_in("src/calls.js", "see calls.jsx") is False


def test_rca_reads_the_fused_ranking_two_hundred_deep_in_one_search(tmp_path: Any) -> None:
    """sdlc-search-use D11 (SSPN-390): at 50 hits, 4 of 9 fix files' first hits were out of RCA's reach on
    two graded tickets. One search, 200 deep; still at most ten files listed."""
    from orchestrator.sdlc import rca
    from orchestrator.sdlc.localize import localize_trace

    rel = "src/app/impact.py"
    (tmp_path / "src/app").mkdir(parents=True)
    (tmp_path / rel).write_text("def blast_radius(store):\n    return 1\n", encoding="utf-8")
    b = FactBatch()
    b.add_node(Node("py:blast_radius", NodeKind.FUNCTION, "blast_radius", "python", Provenance(rel, 1, 2)))
    store = FactStore(b)
    calls: list[dict[str, Any]] = []

    class _Hit:
        def __init__(self, node: Node) -> None:
            self.node, self.found_by = node, "content (#1)"

    class _Fake:
        model = "fake"

        def search(self, text: str, *, limit: int = 50, extra_seeds: Any = ()) -> list[_Hit]:
            calls.append({"limit": limit, "extra_seeds": list(extra_seeds)})
            return [_Hit(n) for n in store.nodes]

    problem = "`src/app/impact.py` reports nothing for a changed file"
    loc = localize_trace(problem, store=store)
    files, evidence, model = rca._ranked_candidates(problem, loc, store, tmp_path, _Fake())  # type: ignore[arg-type]
    assert calls == [{"limit": 200, "extra_seeds": ["py:blast_radius"]}]
    assert files == [rel] and len(evidence) == 1 and model == "fake"
