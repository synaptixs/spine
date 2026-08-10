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
    md = _render(tmp_path)
    for section in ("3. Root cause", "9. Facts", "11. Token usage", "12. Confidence"):
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
