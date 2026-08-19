"""The validator on design's output edge (Phase 2b).

The rule it has to get right is not "does this reference exist" — designs legitimately name
things they are about to create. It is *"you can build a new house on a real street, but not on
a street that does not exist."* Most of this file is the legal cases it must not refuse, because
a validator that refuses every create would be caught by no gate and break every real ticket.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.design_validator import validate_design


def _store() -> FactStore:
    b = FactBatch()
    for nid, kind, name, file, line in (
        (
            "py:orchestrator.sdlc.design",
            NodeKind.MODULE,
            "orchestrator.sdlc.design",
            "src/orchestrator/sdlc/design.py",
            1,
        ),
        (
            "py:orchestrator.sdlc.design.produce_design",
            NodeKind.FUNCTION,
            "produce_design",
            "src/orchestrator/sdlc/design.py",
            20,
        ),
        (
            "py:orchestrator.pkg.store",
            NodeKind.MODULE,
            "orchestrator.pkg.store",
            "src/orchestrator/pkg/store.py",
            1,
        ),
    ):
        b.add_node(Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line)))
    return FactStore(b)


@pytest.mark.parametrize(
    ("design", "why"),
    [
        ({"files_to_touch": ["src/orchestrator/sdlc/design.py"]}, "a file the graph holds"),
        ({"files_to_touch": ["src/orchestrator/sdlc/new_thing.py"]}, "a new file in a real directory"),
        ({"interfaces": ["orchestrator.sdlc.design.NewHelper"]}, "a new symbol in a real module"),
        ({"interfaces": ["def render(items: list[str]) -> str"]}, "a bare signature names no address"),
        ({"interfaces": ["OrderTotals"]}, "a bare name says nothing about where it lives"),
        ({"data_changes": ["add a column to orders"]}, "prose is not a reference"),
        ({"approach": "rewrite orchestrator.invented.Thing"}, "prose fields are not mined"),
    ],
)
def test_what_the_validator_must_not_refuse(design: dict[str, object], why: str) -> None:
    """A validator that refused every create would break every real ticket, and no gate in this
    programme would catch it — parity would be perfect and the pipeline useless."""
    result = validate_design(design, store=_store())
    assert result.ok, f"{why}: refused {design}"


@pytest.mark.parametrize(
    ("design", "named"),
    [
        ({"files_to_touch": ["made_up_pkg/thing.py"]}, "made_up_pkg/thing.py"),
        ({"interfaces": ["orchestrator.invented.Thing"]}, "orchestrator.invented.Thing"),
        ({"data_changes": ["nowhere/at/all.sql"]}, "nowhere/at/all.sql"),
    ],
)
def test_a_fabricated_address_is_refused(design: dict[str, object], named: str) -> None:
    """The seam this exists to guard: a model naming a place the repository does not have."""
    result = validate_design(design, store=_store())
    assert not result.ok
    assert result.findings[0].named == named
    assert result.findings[0].detail


def test_a_greenfield_repo_is_suppressed_entirely() -> None:
    """Same grounds as `unverified_references`: when the graph holds nothing, everything is
    legitimately absent and flagging it all would be noise rather than signal."""
    result = validate_design(
        {"files_to_touch": ["anything/at/all.py"], "interfaces": ["made.up.Thing"]},
        store=FactStore(FactBatch()),
    )
    assert result.ok
    assert result.grounded is False
    assert "no grounded nodes" in result.render()


def test_a_directory_that_exists_only_on_disk_still_counts(tmp_path: Path) -> None:
    """The graph knows only directories containing extracted source. A design putting a file in
    `docs/` or `scripts/` names a real place the graph cannot see, and refusing it would be the
    validator being wrong about the repository rather than the design being wrong."""
    (tmp_path / "docs").mkdir()
    result = validate_design({"files_to_touch": ["docs/new-spec.md"]}, store=_store(), root=tmp_path)
    assert result.ok


def test_the_findings_are_ordered_not_hash_ordered() -> None:
    """Findings are rendered into an artifact and compared between runs; upstream sets would
    otherwise order them by hash and two identical runs would disagree."""
    design = {
        "files_to_touch": ["zzz_nowhere/b.py", "aaa_nowhere/a.py"],
        "interfaces": ["made.up.Thing"],
    }
    findings = validate_design(design, store=_store()).findings
    keys = [(f.field, f.named) for f in findings]
    assert keys == sorted(keys), "findings are ordered by (field, reference), not by hash"
    assert len(keys) == 3


def test_the_render_says_which_reference_and_why() -> None:
    result = validate_design({"files_to_touch": ["made_up_pkg/thing.py"]}, store=_store())
    rendered = result.render()
    assert "made_up_pkg/thing.py" in rendered
    assert "made_up_pkg" in rendered and "does not exist" in rendered


@pytest.mark.parametrize(
    "prose",
    [
        "No schema changes: nothing added to src/orchestrator/registry/db/models.py, no migrations.",
        "New test package marker tests/pkg/ (with __init__.py only if the existing subpackages use them).",
        "No change to the GraphStats dataclass in src/orchestrator/pkg/stats.py — fields are read only.",
    ],
)
def test_prose_in_a_list_field_is_not_a_reference(prose: str) -> None:
    """Verbatim `data_changes` and `interfaces` entries from the first real model design this
    validator ever saw. Each contains a path, and each was refused — three false findings on
    one design, which would have failed every ticket in the model arm and been reported as
    "the model writes designs full of invented code".

    A sentence mentioning a path is a statement *about* the repository, not a claim to touch
    something, which is exactly why `approach` and `test_strategy` are not mined at all.
    """
    result = validate_design({"data_changes": [prose], "interfaces": [prose]}, store=_store())
    assert result.ok, f"prose refused as a reference: {prose}"


async def test_the_design_model_answer_parses_out_of_a_fence() -> None:
    """`_llm_design` used `json.loads` on the raw response, which raised on the first real call
    this path ever made — the model answered with the object inside a markdown fence. The
    exception was then swallowed by `produce_design`, so the run silently returned the
    deterministic design. Reusing codegen's tolerant loader keeps one definition of "parse a
    model's JSON" and makes the failure visible when it is real.
    """
    from orchestrator.sdlc.design import _llm_design

    fenced = (
        '```json\n{"approach": "a", "files_to_touch": ["x.py"], "interfaces": [],\n'
        '"data_changes": [], "risks": [], "test_strategy": "t"}\n```'
    )

    class _Fenced:
        text = fenced

    class _LLM:
        async def complete(self, *_a: object, **_k: object) -> _Fenced:
            return _Fenced()

    design = await _llm_design({"title": "t"}, {"overview": {"modules": []}}, _LLM())
    assert design["llm"] is True
    assert design["files_to_touch"] == ["x.py"]
