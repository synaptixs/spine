"""The build document renders the shape the record specifies, and says what it lacks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.builddoc import (
    _criteria_block,
    _mermaid_blast,
    build_plan,
    persist,
    plan_dir,
    render_build_md,
)

# The twelve titles of docs/specs/build-document.md §3, in order. This list is the
# contract: a section renamed or reordered here is renamed for every ticket.
SECTIONS = [
    "1. Requirement",
    "2. Intent",
    "3. Root cause",
    "4. PKG — what the graph knows",
    "5. Blast radius",
    "6. Design",
    "7. Files",
    "8. Acceptance criteria",
    "9. Facts the generator needs",
    "10. Codegen prompt",
    "11. Token usage & cost",
    "12. Confidence",
]


class _Landing:
    def __init__(self, name: str, where: str) -> None:
        self.name, self.where = name, where
        self.kind, self.callers, self.module = "Function", 3, "pkg.mod"


class _Investigation:
    def __init__(self, landing: list[_Landing] | None = None) -> None:
        self.landing = landing or []
        self.areas = ["pkg.mod"]


class _Assessment:
    def __init__(self, verdict: Any = "PROCEED") -> None:
        self.verdict = verdict
        self.findings: list[Any] = []


def _spec(**over: Any) -> dict[str, Any]:
    spec = {
        "intent_id": "TCK-1",
        "title": "A ticket",
        "summary": "Something is broken.",
        "user_story": "As a user I want it fixed.",
        "acceptance_criteria": ["It stops crashing.", "It says why."],
        "proposed_criteria": [],
        "met_criteria": {},
    }
    spec.update(over)
    return spec


def _design(**over: Any) -> dict[str, Any]:
    design = {
        "approach": "Wrap the call.",
        "files_to_touch": ["src/a.py"],
        "risks": ["Heuristic design."],
        "test_strategy": "Cover each criterion.",
        "llm": False,
        "blast_radius": {
            "grounded": True,
            "call_graph_available": True,
            "modules": [
                {
                    "ref": "src/a.py",
                    "importers": 1,
                    "importer_names": ["pkg.b"],
                    "hotspots": [{"name": "helper", "where": "src/a.py:10", "callers": 4, "transitive": 6}],
                }
            ],
            "unverified_references": [],
        },
    }
    design.update(over)
    return design


def _render(tmp_path: Path, spec: dict[str, Any] | None = None, **over: Any) -> str:
    return render_build_md(
        spec or _spec(),
        investigation=over.pop("investigation", _Investigation()),
        design=over.pop("design", _design()),
        validity=over.pop("validity", _Assessment()),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
        **over,
    )


def test_all_twelve_sections_render_in_order(tmp_path: Path) -> None:
    md = _render(tmp_path)
    found = re.findall(r"^## (.+)$", md, flags=re.MULTILINE)
    assert found == SECTIONS


def test_every_section_carries_a_provenance_label(tmp_path: Path) -> None:
    """§1 is decorative unless the label is actually on the page, under every heading."""
    md = _render(tmp_path)
    for block in md.split("\n## ")[1:]:
        heading, rest = block.split("\n", 1)
        assert rest.lstrip().startswith("*"), f"section {heading!r} has no provenance label"


def test_unbuilt_sections_say_so_rather_than_vanishing(tmp_path: Path) -> None:
    """Section 9 has no phase, and section 3 has nothing to localize in this fixture."""
    md = _render(tmp_path)
    for section in ("3. Root cause", "9. Facts"):
        body = md.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
        assert "not established" in body


def test_header_stamps_the_commit_it_was_derived_at(tmp_path: Path) -> None:
    """A plan approved at one commit and built at another is a document that *was* true."""
    assert "**Derived at:** `abc1234`" in _render(tmp_path)


def test_verdict_renders_its_value_not_its_repr(tmp_path: Path) -> None:
    from orchestrator.sdlc.validity import Verdict

    md = _render(tmp_path, validity=_Assessment(Verdict.PROCEED))
    assert "**Validity:** PROCEED" in md
    assert "Verdict.PROCEED" not in md


# ---- section 8: three states ----------------------------------------------


def test_already_met_criterion_keeps_its_place_and_its_evidence() -> None:
    spec = _spec(met_criteria={"It says why.": "a.py:10 already prints it"})
    block = _criteria_block(spec)
    assert "**stated · already met**" in block
    assert "a.py:10 already prints it" in block
    # It is not deleted: a narrowed list is how six criteria became four unnoticed.
    assert "It says why." in block
    assert "1 of 2 stated criteria already satisfied" in block


def test_proposed_criteria_are_labelled_model() -> None:
    block = _criteria_block(_spec(proposed_criteria=["Also log it."]))
    assert "proposed *(model)*" in block


def test_met_criteria_naming_an_unknown_criterion_is_surfaced() -> None:
    """A typo'd key must not silently mark nothing — that reads as 'no criteria are met'."""
    block = _criteria_block(_spec(met_criteria={"not a real criterion": "somewhere"}))
    assert "Warning:" in block


def test_spec_with_no_criteria_says_the_judge_has_nothing_to_verify() -> None:
    assert "nothing for the acceptance judge" in _criteria_block(_spec(acceptance_criteria=[]))


# ---- section 4: the brief's own trustworthiness ---------------------------


def test_brief_that_names_none_of_the_changed_files_is_called_noise(tmp_path: Path) -> None:
    """The SSPN-49 finding, made deterministic: lexical retrieval that missed the work."""
    inv = _Investigation([_Landing("elsewhere", "src/registry/api.py:1")])
    md = _render(tmp_path, investigation=inv)
    assert "names none of the files this ticket will change" in md


def test_brief_that_agrees_with_the_design_says_so(tmp_path: Path) -> None:
    inv = _Investigation([_Landing("helper", "src/a.py:10")])
    md = _render(tmp_path, investigation=inv)
    assert "agrees with the design" in md


# ---- section 5: the diagram -----------------------------------------------


def test_mermaid_keeps_paths_readable() -> None:
    """Sanitising labels turned `src/orchestrator/cli.py` into words nobody recognises."""
    diagram = _mermaid_blast(_design()["blast_radius"])
    assert '"src/a.py"' in diagram
    assert "src a py" not in diagram


def test_mermaid_declares_every_node_before_its_edges() -> None:
    """md.js renders a subset: a node first declared inside an edge line falls back to <pre>."""
    diagram = _mermaid_blast(_design()["blast_radius"])
    lines = [ln.strip() for ln in diagram.splitlines()]
    declared: set[str] = set()
    for line in lines:
        decl = re.match(r"^(n\d+)\[", line)
        if decl:
            declared.add(decl.group(1))
            continue
        edge = re.match(r"^(n\d+) --> (n\d+)$", line)
        if edge:
            assert {edge.group(1), edge.group(2)} <= declared, f"undeclared node in {line!r}"


def test_no_diagram_rather_than_a_wrong_one() -> None:
    assert _mermaid_blast({"modules": []}) == ""


def test_containment_reports_test_only_importers(tmp_path: Path) -> None:
    md = _render(
        tmp_path,
        design=_design(
            blast_radius={
                "grounded": True,
                "call_graph_available": True,
                "modules": [
                    {"ref": "src/a.py", "importers": 1, "importer_names": ["tests.test_a"], "hotspots": []}
                ],
                "unverified_references": [],
            }
        ),
    )
    assert "only importers are tests" in md


def test_missing_call_graph_is_stated_not_implied_zero(tmp_path: Path) -> None:
    md = _render(
        tmp_path,
        design=_design(
            blast_radius={
                "grounded": True,
                "call_graph_available": False,
                "modules": [{"ref": "src/a.py", "importers": 0, "importer_names": [], "hotspots": []}],
                "unverified_references": [],
            }
        ),
    )
    assert "module-level impact only" in md


# ---- section 3: root cause -------------------------------------------------


class _Hypothesis:
    def __init__(self, claim: str, confidence: str = "medium") -> None:
        self.claim, self.confidence, self.evidence = claim, confidence, ("because",)


class _RCA:
    def __init__(self, **over: Any) -> None:
        self.exception = over.get("exception", "ConnectError: refused")
        self.fault_site = over.get("fault_site", "")
        self.fault_module = over.get("fault_module", "src/a.py")
        self.recently_changed = over.get("recently_changed", False)
        self.hypotheses = over.get("hypotheses", [_Hypothesis("something broke")])


def test_root_cause_renders_the_exception_and_ranked_hypotheses(tmp_path: Path) -> None:
    md = _render(tmp_path, rca=_RCA())
    assert "`ConnectError: refused`" in md
    assert "**[medium]** something broke" in md
    assert "hypotheses, not a verdict" in md


def test_root_cause_is_omitted_when_nothing_localized(tmp_path: Path) -> None:
    """A feature ticket, or a bug with no failure text — omitted rather than padded."""
    md = _render(tmp_path, rca=_RCA(exception="", fault_module="", hypotheses=[]))
    body = md.split("## 3. Root cause", 1)[1].split("\n## ", 1)[0]
    assert "not established" in body
    assert "Hypotheses" not in body


def test_a_stated_file_does_not_claim_a_fault_site(tmp_path: Path) -> None:
    md = _render(tmp_path, rca=_RCA())
    assert "named by the ticket, not localized to a line" in md


def test_consequence_rules_things_out_only_when_a_site_resolved(tmp_path: Path) -> None:
    """The template requires a Consequence line; narrowing scope on a guess is worse."""
    located = _render(tmp_path, rca=_RCA(fault_site="f at src/a.py:10"))
    assert "A change elsewhere is out of scope" in located

    named_only = _render(tmp_path, rca=_RCA())
    assert "nothing inside `src/a.py` is ruled out yet" in named_only


def test_recent_change_is_flagged(tmp_path: Path) -> None:
    assert "changed recently" in _render(tmp_path, rca=_RCA(recently_changed=True))


# ---- section 5, fourth block: the evidence --------------------------------


def _evidence(**over: Any) -> dict[str, Any]:
    ev = {
        "call_graph_available": True,
        "symbols": 3,
        "covered": ["kept"],
        "uncovered": ["gap_a", "gap_b"],
        "endpoints": [("fetch", "calls GET /v1/runs")],
        "regression": ["tests.test_a"],
        "docs": ["README.md"],
        "history": ["abc1234 2026-08-01 did a thing"],
    }
    ev.update(over)
    return ev


def test_evidence_reports_coverage_endpoints_regression_history_and_docs(tmp_path: Path) -> None:
    md = _render(tmp_path, evidence=_evidence())
    assert "2 of 3 symbol(s) in the files this ticket changes are reached by no test" in md
    assert "calls GET /v1/runs" in md
    assert "`tests.test_a`" in md
    assert "did a thing" in md
    assert "`README.md`" in md


def test_evidence_lands_inside_section_5(tmp_path: Path) -> None:
    """05b is part of the blast radius, not a thirteenth section."""
    md = _render(tmp_path, evidence=_evidence())
    section5 = md.split("## 5. Blast radius", 1)[1].split("\n## ", 1)[0]
    assert "**Evidence:**" in section5


def test_evidence_comes_after_the_three_prose_blocks(tmp_path: Path) -> None:
    md = _render(tmp_path, evidence=_evidence())
    order = [md.index(m) for m in ("**Reading it:**", "**Containment:**", "**Caveat:**", "**Evidence:**")]
    assert order == sorted(order)


def test_missing_call_graph_makes_coverage_unknown_not_zero(tmp_path: Path) -> None:
    md = _render(tmp_path, evidence=_evidence(call_graph_available=False, covered=[], uncovered=[]))
    assert "unknown, not zero" in md


def test_no_joined_endpoint_is_reported_as_silence(tmp_path: Path) -> None:
    """An f-string path yields no edge; that is not the same as calling nothing."""
    md = _render(tmp_path, evidence=_evidence(endpoints=[]))
    assert "silence rather than absence" in md


def test_repeated_symbol_names_are_shown_once(tmp_path: Path) -> None:
    md = _render(tmp_path, evidence=_evidence(uncovered=["_go", "_go", "other"]))
    assert md.count("`_go`") == 1
    assert "3 of 4 symbol(s)" in md


def test_backticks_in_a_commit_subject_do_not_break_the_list(tmp_path: Path) -> None:
    md = _render(tmp_path, evidence=_evidence(history=["abc1234 2026-08-01 feat: `sdlc plan` ships"]))
    assert "feat: sdlc plan ships" in md


def test_evidence_is_absent_when_nothing_was_collected(tmp_path: Path) -> None:
    assert "**Evidence:**" not in _render(tmp_path)


def test_collect_evidence_reads_the_graph_for_the_changed_files(tmp_path: Path) -> None:
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.sdlc.builddoc import collect_evidence

    (tmp_path / "src.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_src.py").write_text(
        "import src\n\n\ndef test_helper():\n    assert src.helper() == 1\n", encoding="utf-8"
    )
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    ev = collect_evidence(store, files=["src.py"], root=tmp_path)
    assert "test_src" in " ".join(ev["regression"])
    assert ev["covered"] or ev["uncovered"]


# ---- sections 7 and 10 -----------------------------------------------------


def test_named_file_that_exists_is_changed_and_one_that_does_not_is_created(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    md = _render(tmp_path, design=_design(files_to_touch=["src/a.py", "src/new.py"]))
    assert "**Changed**" in md and "**Created**" in md
    assert "`src/new.py`" in md


def test_context_budget_is_stated_in_bytes_and_percent(tmp_path: Path) -> None:
    assert re.search(r"\*\*Context:\*\* [\d,]+ b of 200,000 — \d+%", _render(tmp_path))


# ---- persistence -----------------------------------------------------------


def test_persist_writes_to_a_stable_path_per_ticket(tmp_path: Path) -> None:
    path, superseded = persist("doc\n", intent_id="TCK-1", root=tmp_path)
    assert path == plan_dir(tmp_path) / "TCK-1-build.md"
    assert path.read_text(encoding="utf-8") == "doc\n"
    assert superseded is None


def test_persist_is_hidden_from_doc_ingestion(tmp_path: Path) -> None:
    """`understand` ingests markdown from disk regardless of git; doc_source skips dotdirs.

    A live plan in the working tree would become a `Doc` node and change the graph the
    next stage reads — the plan would alter the thing it is describing.
    """
    from orchestrator.pkg.doc_source import read_doc_pages

    persist("# plan\n", intent_id="TCK-1", root=tmp_path)
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    seen = " ".join(str(getattr(p, "path", p)) for p in read_doc_pages(tmp_path))
    assert "README.md" in seen
    assert "TCK-1-build.md" not in seen


def test_rewriting_an_unchanged_document_keeps_no_history(tmp_path: Path) -> None:
    persist("same\n", intent_id="TCK-1", root=tmp_path)
    _, superseded = persist("same\n", intent_id="TCK-1", root=tmp_path)
    assert superseded is None
    assert not (plan_dir(tmp_path) / "history").exists()


def test_a_changed_document_keeps_what_it_replaced_keyed_by_commit(tmp_path: Path) -> None:
    persist("**Derived at:** `aaa1111`\nold\n", intent_id="TCK-1", root=tmp_path)
    _, superseded = persist("**Derived at:** `bbb2222`\nnew\n", intent_id="TCK-1", root=tmp_path)
    assert superseded is not None
    assert superseded.name == "TCK-1-aaa1111.md"
    assert "old" in superseded.read_text(encoding="utf-8")


# ---- the approval gate -----------------------------------------------------


def _approval(digest: str, **over: Any) -> Any:
    from orchestrator.sdlc.builddoc import PlanApproval

    fields = {
        "intent_id": "TCK-1",
        "decision": "APPROVED",
        "decided_by": "falcon",
        "decided_at": "2026-08-10",
        "digest": digest,
        "commit": "abc1234",
        "note": "",
    }
    fields.update(over)
    return PlanApproval(**fields)


def test_a_plan_is_proposed_until_somebody_decides(tmp_path: Path) -> None:
    assert "**Status:** proposed" in _render(tmp_path)


def test_an_approval_names_who_and_when(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import plan_digest

    digest = plan_digest(_render(tmp_path))
    md = _render(tmp_path, approval=_approval(digest))
    assert "**approved** by falcon on 2026-08-10" in md
    assert "*(human)*" in md


def test_approving_does_not_change_what_was_approved(tmp_path: Path) -> None:
    """The digest covers the body only — otherwise the status line invalidates itself."""
    from orchestrator.sdlc.builddoc import plan_digest

    before = plan_digest(_render(tmp_path))
    after = plan_digest(_render(tmp_path, approval=_approval(before)))
    assert before == after


def test_a_plan_that_changed_since_approval_reads_as_stale(tmp_path: Path) -> None:
    md = _render(tmp_path, approval=_approval("a-digest-of-something-else"))
    assert "**stale**" in md and "changed since" in md


def test_a_rejection_is_rendered_with_its_reason(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import plan_digest

    digest = plan_digest(_render(tmp_path))
    md = _render(tmp_path, approval=_approval(digest, decision="REJECTED", note="wrong files"))
    assert "**rejected** by falcon" in md and "wrong files" in md


def test_an_approval_round_trips_through_disk(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import load_approval, save_approval

    save_approval(_approval("d1"), root=tmp_path)
    loaded = load_approval("TCK-1", root=tmp_path)
    assert loaded is not None and loaded.decided_by == "falcon" and loaded.digest == "d1"


def test_a_corrupt_approval_is_not_an_approval(tmp_path: Path) -> None:
    """A gate must fail closed: unreadable evidence is no evidence."""
    from orchestrator.sdlc.builddoc import approval_path, load_approval

    path = approval_path("TCK-1", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_approval("TCK-1", root=tmp_path) is None


@pytest.mark.asyncio
async def test_the_gate_refuses_when_no_plan_was_approved(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan

    (tmp_path / "src.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    with pytest.raises(PlanNotApprovedError, match="no approved plan"):
        await require_approved_plan(_spec(), root=tmp_path)


@pytest.mark.asyncio
async def test_the_gate_refuses_a_rejected_plan(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan, save_approval

    (tmp_path / "src.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    save_approval(_approval("whatever", decision="REJECTED", note="not yet"), root=tmp_path)
    with pytest.raises(PlanNotApprovedError, match="rejected"):
        await require_approved_plan(_spec(), root=tmp_path)


@pytest.mark.asyncio
async def test_the_gate_passes_for_the_plan_that_was_read(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import build_plan, plan_digest, require_approved_plan, save_approval

    (tmp_path / "src.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    digest = plan_digest(await build_plan(_spec(), root=tmp_path))
    save_approval(_approval(digest), root=tmp_path)
    assert (await require_approved_plan(_spec(), root=tmp_path)).decided_by == "falcon"


@pytest.mark.asyncio
async def test_the_gate_refuses_once_the_plan_has_moved_underneath_it(tmp_path: Path) -> None:
    """An approval that survives the code changing approves a document nobody read."""
    from orchestrator.sdlc.builddoc import (
        PlanNotApprovedError,
        build_plan,
        plan_digest,
        require_approved_plan,
        save_approval,
    )

    src = tmp_path / "src.py"
    src.write_text("def helper():\n    return 1\n", encoding="utf-8")
    # The spec has to name the file, or the plan does not depend on it and nothing about
    # the code could make the document stale.
    spec = _spec(summary="Something is broken in src.py.")
    save_approval(_approval(plan_digest(await build_plan(spec, root=tmp_path))), root=tmp_path)

    src.write_text("def helper():\n    return 1\n\n\ndef added():\n    return 2\n", encoding="utf-8")
    with pytest.raises(PlanNotApprovedError, match="changed since"):
        await require_approved_plan(spec, root=tmp_path)


# ---- the journey -----------------------------------------------------------


def _entry(**over: Any) -> Any:
    from orchestrator.sdlc.builddoc import JourneyEntry

    fields: dict[str, Any] = {
        "run_id": "run-1",
        "stage": "design",
        "status": "ok",
        "detail": "2 file(s) proposed",
        "at": "2026-08-10T09:00:00+00:00",
    }
    fields.update(over)
    return JourneyEntry(**fields)


def test_the_journey_renders_below_the_twelve_sections(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_entry()])
    assert md.index("## 12. Confidence") < md.index("## Journey")
    assert "**design** — 2 file(s) proposed" in md


def test_a_run_appending_does_not_invalidate_its_own_approval(tmp_path: Path) -> None:
    """The gate would otherwise refuse the very next run after the one it permitted."""
    from orchestrator.sdlc.builddoc import plan_digest

    before = plan_digest(_render(tmp_path))
    after = plan_digest(_render(tmp_path, journey=[_entry(), _entry(stage="implement")]))
    assert before == after


def test_entries_are_grouped_by_run(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_entry(), _entry(run_id="run-2", stage="implement")])
    assert "**Run `run-1`**" in md and "**Run `run-2`**" in md


def test_a_failure_is_marked_as_one(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_entry(status="failed", detail="tests red")])
    assert "✗ **design** — tests red" in md


def test_no_journey_no_section(tmp_path: Path) -> None:
    assert "## Journey" not in _render(tmp_path)


def test_the_journey_is_append_only_on_disk(tmp_path: Path) -> None:
    """No update, no delete: a later stage that could tidy an earlier one removes evidence."""
    from orchestrator.sdlc import builddoc
    from orchestrator.sdlc.builddoc import append_journey, load_journey

    append_journey(_entry(), intent_id="TCK-1", root=tmp_path)
    append_journey(_entry(stage="implement", detail="3 files"), intent_id="TCK-1", root=tmp_path)
    entries = load_journey("TCK-1", root=tmp_path)
    assert [e.stage for e in entries] == ["design", "implement"]
    assert not any(name.startswith(("update_", "delete_", "rewrite_")) for name in dir(builddoc))


def test_a_malformed_journey_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import append_journey, journey_path, load_journey

    append_journey(_entry(), intent_id="TCK-1", root=tmp_path)
    with journey_path("TCK-1", root=tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    assert len(load_journey("TCK-1", root=tmp_path)) == 1


def test_disagreement_names_both_directions() -> None:
    from orchestrator.sdlc.builddoc import design_disagreement

    drift = design_disagreement(["a.py", "b.py"], ["a.py", "c.py"])
    assert "changed but not planned: `c.py`" in drift
    assert "planned but not changed: `b.py`" in drift


def test_agreement_says_nothing() -> None:
    from orchestrator.sdlc.builddoc import design_disagreement

    assert design_disagreement(["a.py"], ["a.py"]) == ""


# ---- sections 11 and 12: cost and confidence -------------------------------


def _run_entry(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "stage": "run",
        "status": "ok",
        "detail": "PASSED",
        "tokens": 100_000,
        "usd": 1.10,
    }
    fields.update(over)
    return _entry(**fields)


def test_cost_says_it_is_an_estimate_when_nothing_has_run(tmp_path: Path) -> None:
    md = _render(tmp_path)
    assert "No measured history for this ticket" in md
    assert "(estimate)" in md


def test_cost_uses_measured_runs_when_there_are_any(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_run_entry(), _run_entry(tokens=200_000, usd=2.20)])
    assert "Measured** over 2 run(s)" in md
    assert "150,000 tokens mean" in md
    assert "$3.30 across all runs" in md


def test_a_failed_run_is_reported_as_costing_the_same(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_run_entry(status="failed", detail="FAILED")])
    assert "1 of 1 run(s) produced nothing, and cost the same" in md


def test_the_estimate_is_a_band_not_a_number(tmp_path: Path) -> None:
    """The output cap is the honest width: nobody has measured this ticket yet."""
    md = _render(tmp_path)
    assert "between 0 and 32,000 output" in md
    assert re.search(r"\$\d+\.\d\d–\d+\.\d\d", md)


def test_the_model_table_names_the_resolved_model_once(tmp_path: Path) -> None:
    md = _render(tmp_path)
    assert md.count("*(resolved)*") == 1


def test_the_cost_table_prices_a_swap_across_providers(tmp_path: Path) -> None:
    """ "What would switching cost" is unanswerable from the resolved model's neighbours."""
    from orchestrator.sdlc.builddoc import _COMPARE_PROVIDERS

    md = _render(tmp_path)
    table = md.split("| model | provider |", 1)[1].split("\n\n", 1)[0]
    assert {p for p in _COMPARE_PROVIDERS if f"| {p} |" in table} == set(_COMPARE_PROVIDERS)


def test_the_cost_table_does_not_claim_recency(tmp_path: Path) -> None:
    """The catalog carries no release date, so "latest" is not a fact available here."""
    md = _render(tmp_path)
    assert "like-for-like swap, not a ranking" in md
    # Prose only: `chatgpt-4o-latest` is a model id, not a claim about recency.
    section = md.split("## 11.", 1)[1].split("## 12.", 1)[0]
    prose = " ".join(line for line in section.splitlines() if not line.startswith("|"))
    assert "latest" not in prose and "newest" not in prose


def test_confidence_is_a_band_with_its_basis(tmp_path: Path) -> None:
    md = _render(tmp_path)
    assert re.search(r"\*\*Is the analysis right\? — (high|medium|low)\*\*", md)
    assert "A band, not a percentage" in md
    assert "| what the plan established | reading | weight |" in md


def test_a_plan_that_established_everything_scores_high(tmp_path: Path) -> None:
    from orchestrator.sdlc.validity import Verdict

    inv = _Investigation([_Landing("helper", "src/a.py:10")])
    md = _render(
        tmp_path,
        investigation=inv,
        validity=_Assessment(Verdict.PROCEED),
        rca=_RCA(fault_site="f at src/a.py:10"),
    )
    assert "**Is the analysis right? — high** (5 of 5 applicable checks" in md


def test_a_plan_that_established_little_scores_low(tmp_path: Path) -> None:
    md = _render(
        tmp_path,
        investigation=_Investigation([_Landing("elsewhere", "src/zzz.py:1")]),
        validity=_Assessment("REFUSE"),
        design=_design(
            blast_radius={
                "grounded": True,
                "call_graph_available": True,
                "modules": [],
                "unverified_references": ["src/ghost.py"],
            }
        ),
    )
    assert re.search(r"\*\*Is the analysis right\? — low\*\* \([012] of \d applicable", md)


def test_a_feature_is_not_docked_for_having_no_root_cause(tmp_path: Path) -> None:
    """A check that cannot apply is not a check this ticket failed.

    Scoring an omitted section 3 as a failure capped every enhancement a point below
    every bug, and said "a file at best" about a ticket that named no file at all.
    """
    md = _render(tmp_path, rca=_RCA(exception="", fault_module="", hypotheses=[]))
    assert "| Root cause | nothing to localize — not a bug, so nothing is owed | n/a |" in md
    assert "of 4 applicable checks" in md
    assert "a file at best" not in md


def test_the_completion_number_is_a_base_rate_not_a_guess(tmp_path: Path) -> None:
    md = _render(tmp_path, journey=[_run_entry(status="failed"), _run_entry(status="failed")])
    assert "**0 of 2 run(s)** of this ticket completed" in md
    assert "**Will an unattended run complete? — low.**" in md


def test_no_runs_means_unestablished_not_optimistic(tmp_path: Path) -> None:
    md = _render(tmp_path)
    assert "**Will an unattended run complete? — unestablished.**" in md
    assert "should be read as optimism" in md


def test_confidence_does_not_move_the_digest_between_renders(tmp_path: Path) -> None:
    """A model-written score would restamp the digest and stale every approval."""
    from orchestrator.sdlc.builddoc import plan_digest

    assert plan_digest(_render(tmp_path)) == plan_digest(_render(tmp_path))


# ---- the whole path --------------------------------------------------------


@pytest.mark.asyncio
async def test_build_plan_is_deterministic_and_touches_no_tracker(tmp_path: Path) -> None:
    """Same spec, same tree, byte-identical document — the property the history relies on."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    spec = _spec()
    first = await build_plan(spec, root=tmp_path)
    second = await build_plan(spec, root=tmp_path)
    assert first == second
    assert re.findall(r"^## (.+)$", first, flags=re.MULTILINE) == SECTIONS


# ---- the measured caveat (phase 6) ---------------------------------------


def test_the_caveat_states_measured_recall_for_a_measured_language() -> None:
    """Five phases of measurement only change an outcome if a reader sees the number.

    "counts under-report" tells a reader to be vaguely careful. "recall is 0.73" tells them
    roughly one call in four is missing, and lets them judge whether that matters here.
    """
    from orchestrator.sdlc.builddoc import _blast_prose

    prose = _blast_prose({"call_graph_available": True, "modules": []}, "python")
    assert "Measured `CALLS` recall for python is" in prose
    assert "lower bound" in prose


def test_the_caveat_says_what_the_number_was_measured_against() -> None:
    """Load-bearing: the figure comes from the extractor's fixtures, not the repo described.

    A reader who takes it as a statement about their own code has been misled by us.
    """
    from orchestrator.sdlc.builddoc import _blast_prose

    prose = _blast_prose({"call_graph_available": True, "modules": []}, "python")
    assert "not this repository" in prose


def test_an_unmeasured_language_keeps_the_original_wording() -> None:
    """None is not zero. Six of eight front-ends have no corpus, and a language nobody
    measured has not scored badly — it has not been scored."""
    from orchestrator.sdlc.builddoc import _blast_prose

    prose = _blast_prose({"call_graph_available": True, "modules": []}, "go")
    assert "per-method counts under-report" in prose
    assert "Measured `CALLS` recall" not in prose
    assert "0.00" not in prose
