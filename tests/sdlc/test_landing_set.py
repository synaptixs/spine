"""The landing *set* is not the display list (SSPN-181 / B88).

#575 made `build_investigation()` list up to ten distinct files where it listed ten symbols (six or
seven files). That list is for a reader. Four consumers use it as "the files this change lands in":
the validity gate's context-size check, the blast radius, the churn reading and the `in_evidence`
badge. On 40 recorded real autorun tickets the wider set flipped 6 from PROCEED to TOO_BIG, and
dropping weak rows — the first idea — fixed none of them, because weak rows are rare. The fix keeps
the display at ten files and gives every consumer one definition of the set: the files of the first
ten non-weak rows, which is what the old ten-symbol cap produced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.autorun import RunContext, _stage_investigate
from orchestrator.sdlc.builddoc import build_plan
from orchestrator.sdlc.evidence import evidence_from_parts
from orchestrator.sdlc.investigate import build_investigation
from orchestrator.sdlc.landings import Landing
from orchestrator.sdlc.validity import Verdict, _named_file_bytes, assess

BUDGET = 200_000
# Five names that each carry exactly the ticket's two words, so the ticket names every one of them
# whole and none is a weak hit (a hit on words that also name other files is weak; a whole name is not).
NAMES = (
    "def order_handler():\n    return 0",
    "def handler_order():\n    return 1",
    "def orderHandler():\n    return 2",
    "class OrderHandler:\n    pass",
    "class HandlerOrder:\n    pass",
)
TICKET = "order handler"


def _files_of(landings: list[Landing]) -> list[str]:
    seen: list[str] = []
    for land in landings:
        name = land.where.rsplit(":", 1)[0]
        if name not in seen:
            seen.append(name)
    return seen


def _repo(tmp_path: Path, *, files: int = 12, per_file: int = 5, pad_bytes: int = 45_000) -> FactStore:
    """``files`` modules of ~``pad_bytes`` each, every name matching 'order handler' whole and equally."""
    pad = "# " + "x" * 78 + "\n"
    for i in range(files):
        body = "\n\n".join(NAMES[:per_file])
        (tmp_path / f"f{i:02d}.py").write_text(body + "\n" + pad * (pad_bytes // len(pad)), encoding="utf-8")
    return FactStore(RepoCodeExtractor().extract(tmp_path))


def _landing(where: str, *, weak: bool = False) -> Landing:
    return Landing(name=where, where=where, kind="Function", callers=0, module="m", weak=weak)


def _row(where: str, *, weak: bool = False) -> dict[str, Any]:
    return {"name": where, "where": where, "kind": "Function", "callers": 0, "module": "m", "weak": weak}


def test_the_set_is_the_files_of_the_first_ten_non_weak_rows() -> None:
    from orchestrator.sdlc.landings import LANDING_SET_ROWS, landing_set

    rows = [_landing(f"a.py:{i}") for i in range(3)] + [_landing("weak.py:1", weak=True)]
    rows += [_landing(f"b.py:{i}") for i in range(3)] + [_landing(f"c.py:{i}") for i in range(3)]
    rows += [_landing(f"d.py:{i}") for i in range(3)] + [_landing("e.py:1")]

    assert LANDING_SET_ROWS == 10
    # Ten non-weak rows are a.py x3, b.py x3, c.py x3 and d.py's first: e.py is the eleventh.
    assert landing_set(rows) == ("a.py", "b.py", "c.py", "d.py")


def test_a_weak_row_never_enters_the_set() -> None:
    from orchestrator.sdlc.landings import landing_set

    assert landing_set([_landing("only.py:1", weak=True)]) == ()
    assert landing_set([_landing("w.py:1", weak=True), _landing("s.py:2")]) == ("s.py",)


def test_the_set_keeps_first_seen_order_and_each_file_once() -> None:
    from orchestrator.sdlc.landings import landing_set

    rows = [_landing("z.py:1"), _landing("a.py:1"), _landing("z.py:9")]
    assert landing_set(rows) == ("z.py", "a.py")


def test_graph_mode_keeps_ten_files_to_show_and_a_smaller_set_to_measure() -> None:
    """`Evidence.landing` is what design and codegen are told; `Evidence.files` is what the gate,
    the blast radius, churn and the badge measure. They used to be the same ten files."""
    rows = [_row(f"f{i:02d}.py:{j + 1}") for i in range(10) for j in range(3)]  # 30 rows, 10 files

    evidence = evidence_from_parts(
        title="t", problem="p", issue_type="Bug", investigate={"landing": rows}, rca={}, blast={}
    )

    assert len({fact.file for fact in evidence.landing}) == 10
    assert len(evidence.landing) == 30
    assert evidence.files == ("f00.py", "f01.py", "f02.py", "f03.py")


def test_the_autorun_fallback_hands_the_gate_the_set_not_the_display_list(tmp_path: Path) -> None:
    """The measured flip, reproduced: ten files over the refuse ratio, four files under the budget.

    Same ticket, same graph, same budget — and the verdict depends only on which of the two lists
    the gate was given, which is the whole of B88.
    """
    store = _repo(tmp_path)
    spec = {"title": TICKET, "summary": TICKET}
    shown = _files_of(build_investigation(TICKET, TICKET, store=store, root=tmp_path).landing)
    shown_bytes, _ = _named_file_bytes(tmp_path, shown)
    assert len(shown) == 10 and shown_bytes > 1.5 * BUDGET, "premise: the display list is over budget"

    ctx = RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        issue_key="SPN-1",
        spec=spec,
    )
    _stage_investigate(ctx, store=store, emit=lambda _s: None)

    assert ctx.landing == ["f00.py", "f01.py", "f02.py", "f03.py"]
    assert len(ctx.landing_facts) == 30, "what design and codegen are told is untouched"
    verdict = assess(
        spec, store=store, landing=ctx.landing, issue_type="Bug", root=tmp_path, context_budget=BUDGET
    ).verdict
    assert verdict is Verdict.PROCEED
    over = assess(spec, store=store, landing=shown, issue_type="Bug", root=tmp_path, context_budget=BUDGET)
    assert over.verdict is Verdict.TOO_BIG, "the display list alone is what flips it"


def test_the_fallback_uses_the_one_definition_of_the_set(tmp_path: Path) -> None:
    from orchestrator.sdlc.landings import landing_set

    store = _repo(tmp_path, pad_bytes=0)
    ctx = RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        issue_key="SPN-1",
        spec={"title": TICKET, "summary": TICKET},
    )
    _stage_investigate(ctx, store=store, emit=lambda _s: None)
    inv = build_investigation(TICKET, TICKET, store=store, root=tmp_path)

    assert ctx.landing == list(landing_set(inv.landing))


def test_build_passes_the_set_to_criteria_binding_as_its_evidence_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`in_evidence` is the strongest badge in the criteria section; ten files, however weakly
    matched, used to be allowed to earn it."""
    from orchestrator.cli.build import _facts_for_spec
    from orchestrator.intake import pkg_evidence
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.pkg import criteria_binding

    store = _repo(tmp_path, pad_bytes=0)
    captured: dict[str, Any] = {}
    real = criteria_binding.bind_criteria

    def spy(*args: Any, **kwargs: Any) -> Any:
        captured["evidence_files"] = tuple(kwargs["evidence_files"])
        return real(*args, **kwargs)

    monkeypatch.setattr(criteria_binding, "bind_criteria", spy)
    spec = FeatureSpec(intent_id="i", title=TICKET, summary=TICKET, description=TICKET)
    base = pkg_evidence.from_store(store, where="./repo")

    _facts_for_spec(base, store, tmp_path, None, spec)

    inv = build_investigation(TICKET, TICKET, store=store, root=tmp_path)
    assert len(_files_of(inv.landing)) == 10
    assert captured["evidence_files"] == ("f00.py", "f01.py", "f02.py", "f03.py")


def _weak_repo(tmp_path: Path) -> FactStore:
    """Three files whose names each carry one more word than the ticket: every hit shares only
    'order' and 'handler', words that name symbols in all three files, so every row is weak."""
    for i in range(3):
        (tmp_path / f"w{i}.py").write_text(
            f"def order_handler_{SUFFIXES[i]}():\n    return {i}\n", encoding="utf-8"
        )
    return FactStore(RepoCodeExtractor().extract(tmp_path))


SUFFIXES = ("alpha", "bravo", "charlie")


@pytest.mark.parametrize(
    ("issue_type", "verdict", "finding"),
    [
        ("Bug", Verdict.UNLOCALIZED, "nothing in the graph matches this bug's words"),
        ("Story", Verdict.PROCEED, "names no file"),
    ],
)
def test_an_all_weak_ticket_lands_nowhere_in_the_fallback_as_in_graph_mode(
    tmp_path: Path, issue_type: str, verdict: Verdict, finding: str
) -> None:
    """Weak files used to count as located in the fallback, so a ticket that matched on nothing
    specific read as PROCEED; graph mode, `design` and `builddoc` already dropped them. One
    behaviour now: the set is empty and the gate says why (a Bug is parked, a Story is told)."""
    store = _weak_repo(tmp_path)
    spec = {"title": TICKET, "summary": TICKET}
    inv = build_investigation(TICKET, TICKET, store=store, root=tmp_path)
    assert inv.landing and all(land.weak for land in inv.landing), "premise: every row is weak"

    ctx = RunContext(
        run_id="r1",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        issue_key="SPN-1",
        spec=spec,
    )
    _stage_investigate(ctx, store=store, emit=lambda _s: None)
    assert ctx.landing == []

    result = assess(
        spec, store=store, landing=ctx.landing, issue_type=issue_type, root=tmp_path, context_budget=BUDGET
    )
    assert result.verdict is verdict
    assert any(finding in f.detail for f in result.findings)


def test_graph_mode_gives_the_same_empty_set_for_an_all_weak_ticket(tmp_path: Path) -> None:
    from orchestrator.sdlc.evidence import _tool_investigate, landing_files

    store = _weak_repo(tmp_path)
    rows = _tool_investigate(store=store, title=TICKET, problem=TICKET, root=tmp_path)["landing"]
    assert rows and all(row["weak"] for row in rows)
    assert landing_files(rows) == ()


async def test_the_plan_hands_the_gate_the_set_not_the_display_list(tmp_path: Path) -> None:
    """`sdlc_plan` and `autorun` read the same ticket the same way: the plan used to pass up to ten
    files to the gate while autorun passed the set, so one ticket was TOO_BIG in one and PROCEED in
    the other."""
    store = _repo(tmp_path)
    shown = _files_of(build_investigation(TICKET, TICKET, store=store, root=tmp_path).landing)
    shown_bytes, _ = _named_file_bytes(tmp_path, shown)
    assert len(shown) == 10 and shown_bytes > 1.5 * BUDGET, "premise: the display list is over budget"

    doc = await build_plan({"title": TICKET, "summary": TICKET}, root=tmp_path, issue_type="Bug")

    assert "**Validity:** PROCEED" in doc


async def test_a_file_the_ticket_names_still_leads_the_plans_landing_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc import validity

    _repo(tmp_path, files=12, pad_bytes=0)
    (tmp_path / "named.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
    seen: dict[str, Any] = {}
    real = validity.assess

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen["landing"] = list(kwargs["landing"])
        return real(*args, **kwargs)

    monkeypatch.setattr(validity, "assess", spy)
    await build_plan(
        {"title": TICKET, "summary": TICKET, "description": "Change `named.py` too."},
        root=tmp_path,
        issue_type="Bug",
    )

    assert seen["landing"] == ["named.py", "f00.py", "f01.py", "f02.py", "f03.py"]
