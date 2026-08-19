"""The shadow pass: the SDLC graph runs beside the imperative pipeline and compares.

Shadow means nothing here decides anything — no stage is recorded, no verdict changes, and a
failure inside it must not take the run down. What it *must* do is notice when the graph and
the pipeline disagree, and say so on every path including the clean one.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.autorun import (
    RunContext,
    _shadow_compare,
    _shadow_pass,
    _write_shadow_report,
)


def _n(nid: str, kind: NodeKind, name: str, file: str, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _store() -> FactStore:
    b = FactBatch()
    for node in (
        _n("py:report", NodeKind.MODULE, "report.py", "report.py"),
        _n("py:web", NodeKind.MODULE, "web.py", "web.py"),
        _n("py:report.render", NodeKind.FUNCTION, "render", "report.py", 10),
        _n("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5),
    ):
        b.add_node(node)
    b.add_edge(Edge("py:report", "py:report.render", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web.handler", "py:report.render", EdgeKind.CALLS, Provenance("web.py", 6)))
    return b if isinstance(b, FactStore) else FactStore(b)


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        spec={"title": "render handler fail", "summary": "render and handler fail"},
    )


async def test_the_shadow_pass_writes_evidence_and_runs_every_tool(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)

    assert ctx.shadow["enabled"] is True
    assert ctx.shadow["valid"] is True, ctx.shadow.get("failures")
    assert set(ctx.shadow["nodes"]) == {
        "sdlc.investigate",
        "sdlc.rca",
        "sdlc.blast_radius",
        "sdlc.validity",
    }
    assert (ctx.artifacts_dir / "evidence.md").is_file()
    assert (ctx.artifacts_dir / "evidence.json").is_file()


async def test_agreement_is_not_a_divergence(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    actual = ctx.shadow["nodes"]["sdlc.investigate"]["value"]

    _shadow_compare(ctx, "n_investigate", actual, emit=lambda _s: None)
    assert ctx.shadow["divergences"] == []


async def test_a_disagreement_is_reported(tmp_path: Path) -> None:
    """The whole point of the phase. If this cannot fail, the shadow proves nothing — which is
    why the test mutates a real tool result rather than asserting on a hand-built pair."""
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    actual = json.loads(json.dumps(ctx.shadow["nodes"]["sdlc.investigate"]["value"]))
    actual["landing"].append(
        {"name": "ghost", "where": "nowhere.py:1", "kind": "Function", "callers": 0, "module": "x"}
    )

    said: list[str] = []
    _shadow_compare(ctx, "n_investigate", actual, emit=said.append)

    assert len(ctx.shadow["divergences"]) == 1
    divergence = ctx.shadow["divergences"][0]
    assert divergence["node"] == "n_investigate"
    assert divergence["stage_digest"] != divergence["tool_digest"]
    assert any("DIVERGENCE" in line for line in said)


async def test_a_divergence_does_not_change_the_run(tmp_path: Path) -> None:
    """A failed stage flips ``ctx.passed`` and would change the outcome — which would make this
    a second pipeline rather than a shadow of the first."""
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _shadow_compare(
        ctx, "n_investigate", {"landing": [], "areas": [], "grounded": True}, emit=lambda _s: None
    )

    assert ctx.shadow["divergences"], "the divergence was recorded"
    assert ctx.stages == [], "no stage was recorded by the shadow"
    assert ctx.passed is True


async def test_the_report_is_written_even_when_clean(tmp_path: Path) -> None:
    """A file that only appears on failure cannot tell "clean" from "never ran", and that
    ambiguity is how a silent skip reads as a pass."""
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _write_shadow_report(ctx)

    report = json.loads((ctx.artifacts_dir / "shadow.json").read_text())
    assert report["divergence_count"] == 0
    assert report["comparisons"] == ["n_investigate", "n_validity"]


async def test_the_kill_switch_disables_it(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SPINE_IR_SHADOW", "0")
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)

    assert ctx.shadow == {"enabled": False}
    assert not (ctx.artifacts_dir / "evidence.json").exists()
    # A disabled shadow must also not compare, or it would read every stage as a divergence
    # against nothing.
    _shadow_compare(ctx, "n_investigate", {"anything": True}, emit=lambda _s: None)
    assert "divergences" not in ctx.shadow


async def test_a_broken_shadow_never_takes_the_run_down(tmp_path: Path) -> None:
    """`_shadow_pass` catches everything on purpose: a run must not fail because the graph that
    is not driving it could not be built."""
    ctx = _ctx(tmp_path)
    await _shadow_pass(ctx, store=object(), issue_type="Bug", emit=lambda _s: None)

    assert "error" in ctx.shadow
    assert ctx.passed is True
