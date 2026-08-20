"""The research pass and the stages that consume it (Phase 2a).

Phase 1 ran these nodes in shadow and threw the answers away. Here they are the run's research,
so what matters is that the downstream stages *use* them — a fact nothing reads changes nothing,
which was the whole argument for this phase.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.autorun import RunContext, _research_pass, _stage_investigate, _write_case


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
    return FactStore(b)


def _ctx(tmp_path: Path, criteria: list[str] | None = None) -> RunContext:
    return RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        issue_key="SPN-1",
        spec={
            "title": "render handler fail",
            "summary": "render and handler fail",
            "acceptance_criteria": criteria or [],
        },
    )


async def test_the_research_nodes_run_for_real_and_land_in_the_case(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)

    assert ctx.evidence is not None
    assert {n.node for n in ctx.case.nodes} == {"n_investigate", "n_rca", "n_blast_radius"}
    assert all(n.digest for n in ctx.case.nodes), "every tool node records what it produced"
    assert (ctx.artifacts_dir / "evidence.md").is_file()
    assert (ctx.artifacts_dir / "evidence.json").is_file()


async def test_investigate_reads_the_evidence_instead_of_deriving_it_again(tmp_path: Path) -> None:
    """Re-deriving would give the run two views of the same question and no way to know which
    one design was built on — the defect this phase closes, in miniature."""
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _stage_investigate(ctx, store=_store(), emit=lambda _s: None)

    stage = next(s for s in ctx.stages if s.name == "investigate")
    assert stage.artifact.endswith("evidence.md"), "the brief is the Evidence, not a second render"
    assert "from the graph" in stage.detail


async def test_the_landing_facts_survive_the_stage_boundary(tmp_path: Path) -> None:
    """`ctx.landing` keeps file paths, which is what `assess` consumes. `landing_facts` keeps the
    symbol, `file:line`, kind, caller count and module — what design and codegen used to lose."""
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _stage_investigate(ctx, store=_store(), emit=lambda _s: None)

    assert ctx.landing, "files, for the gate and the context budget"
    assert ctx.landing_facts, "and the whole fact, for everything downstream"
    fact = ctx.landing_facts[0]
    assert fact.where and ":" in fact.where
    assert fact.kind and fact.module


async def test_criteria_are_bound_and_written(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, criteria=["`render` returns a string", "`GhostWidget` is removed"])
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)

    assert ctx.criteria is not None
    assert len(ctx.criteria.bound) == 1
    assert len(ctx.criteria.unbound) == 1
    assert ctx.criteria.parks is True
    assert (ctx.artifacts_dir / "criteria.md").is_file()


async def test_the_case_is_written_on_every_path(tmp_path: Path) -> None:
    """A record that only appears on success cannot tell "clean" from "never ran"."""
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _write_case(ctx)

    case = json.loads((ctx.artifacts_dir / "case.json").read_text())
    assert case["mode"] == "graph"
    assert case["digest"]
    assert [n["node"] for n in case["nodes"]] == ["n_investigate", "n_rca", "n_blast_radius"]


async def test_the_imperative_path_still_derives_its_own_view(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Kept for one release. A migration with no way back is one nobody can roll back at 2am."""
    monkeypatch.setenv("SPINE_SDLC_IMPERATIVE", "1")
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Bug", emit=lambda _s: None)
    _stage_investigate(ctx, store=_store(), emit=lambda _s: None)

    stage = next(s for s in ctx.stages if s.name == "investigate")
    assert stage.artifact.endswith("investigation.md")
    assert ctx.case.mode == "imperative"


async def test_broken_research_never_takes_the_run_down(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=object(), issue_type="Bug", emit=lambda _s: None)

    assert ctx.evidence is None
    failed = ctx.case.result("n_investigate")
    assert failed is not None and failed.status == "failed"
    assert ctx.passed is True


async def test_design_is_handed_the_evidence_blast_radius(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Defect 2, and the regression guard for it.

    `produce_design` used to call `impact.blast_radius` on its own `files_to_touch`, so the
    impact analysis described the files the design had *guessed at* — a faithful analysis of a
    fiction whenever the guess was wrong, and it read as verification.

    The assertion is on the **call**, not on the rendered output, and deliberately so: the
    fallback still computes a blast radius, and on a small fixture the two happen to agree, so
    an output-shaped assertion passes with the wiring removed. Verified by removing it.
    """
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc import design as design_mod
    from orchestrator.sdlc.autorun import _stage_design

    store = _store()
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=store, issue_type="Bug", emit=lambda _s: None)
    _stage_investigate(ctx, store=store, emit=lambda _s: None)

    seen: dict[str, object] = {}
    real = design_mod.produce_design

    async def _capture(*args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(design_mod, "produce_design", _capture)

    batch = FactBatch()
    for node in store.nodes:
        batch.add_node(node)
    await _stage_design(ctx, store=store, overview=build_overview(batch), emit=lambda _s: None)

    assert seen.get("blast_radius") is not None, "design computed its own impact again"
    assert seen["blast_radius"] == ctx.evidence.blast_radius, "and it must be Evidence's, not another"


async def test_a_design_naming_invented_code_parks_the_run(tmp_path: Path) -> None:
    """Phase 2b's enforcement, and the reason the promotion rule allows `design` to call a model
    at all: every other model output in this pipeline has a deterministic check downstream, and
    until now this one had none.

    The design is synthesised rather than generated — the deterministic design names real files,
    so the only way to exercise the guard is to hand it the output a model would produce on a
    bad day.
    """
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc import design as design_mod
    from orchestrator.sdlc.autorun import AutorunError, _stage_design

    store = _store()
    ctx = _ctx(tmp_path)
    ctx.approvals_dir = tmp_path / "approvals"
    await _research_pass(ctx, store=store, issue_type="Bug", emit=lambda _s: None)
    _stage_investigate(ctx, store=store, emit=lambda _s: None)

    async def _invented(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "approach": "add a widget",
            "files_to_touch": ["made_up_pkg/widget.py"],
            "interfaces": [],
            "data_changes": [],
            "risks": [],
            "test_strategy": "",
        }

    monkey = design_mod.produce_design
    design_mod.produce_design = _invented
    try:
        batch = FactBatch()
        for node in store.nodes:
            batch.add_node(node)
        try:
            await _stage_design(ctx, store=store, overview=build_overview(batch), emit=lambda _s: None)
        except AutorunError as exc:
            assert "does not exist" in str(exc)
        else:
            raise AssertionError("a design naming an invented directory reached codegen")
    finally:
        design_mod.produce_design = monkey

    stage = next(s for s in ctx.stages if s.name == "design")
    assert stage.status == "failed"
    assert ctx.case.result("n_design").status == "failed"
    assert (ctx.artifacts_dir / "design-references.md").is_file()


async def test_a_bug_gets_rca_and_an_enhancement_does_not(tmp_path: Path) -> None:
    """Phase 3, end to end. The profile is chosen from the issue type, and the difference is
    visible in the Case: a bug's `n_rca` ran, an enhancement's is recorded as *skipped for this
    issue type* — which is a different statement from "we ran it and found nothing"."""
    store = _store()

    bug = _ctx(tmp_path / "bug")
    await _research_pass(bug, store=store, issue_type="Bug", emit=lambda _s: None)
    assert bug.case.profile == "sdlc.bug"
    rca = bug.case.result("n_rca")
    assert rca is not None and rca.status == "ok" and rca.digest

    story = _ctx(tmp_path / "story")
    await _research_pass(story, store=store, issue_type="Story", emit=lambda _s: None)
    assert story.case.profile == "sdlc.enhancement"
    skipped = story.case.result("n_rca")
    assert skipped is not None and skipped.status == "skipped"
    assert "not run for issue type" in skipped.detail
    assert not skipped.digest, "a node that did not run has nothing to digest"
    # The evidence says so too, rather than carrying an empty RCA that reads as a finding.
    assert story.evidence is not None and story.evidence.rca == {}


async def test_an_unmapped_issue_type_uses_default_and_says_why(tmp_path: Path) -> None:
    said: list[str] = []
    ctx = _ctx(tmp_path)
    await _research_pass(ctx, store=_store(), issue_type="Spike", emit=said.append)

    assert ctx.case.profile == "sdlc.default"
    assert any("Spike" in line and "default" in line for line in said), said
