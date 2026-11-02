"""P6 of SSPN-297 (SSPN-303): the options table both surfaces render, from one composition.

``openspec options`` and the MCP ``requirements_options`` hand the same inputs to
``sdlc.option_report``, so they cannot disagree. The table is rendered every time, never stored;
untrusted text in it is made inert; untraced calls sit beside the caller floor, never in it."""

from __future__ import annotations

import shlex
from pathlib import Path

from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.unbound import UnboundCall, UnboundIndex
from orchestrator.sdlc import ideation as idn
from orchestrator.sdlc import option_measures as om
from orchestrator.sdlc import option_report as orp

_PROPOSAL = (
    "# Proposal: Validate\n\n## Why\nInput is not validated.\n"
    "### Problem\nBad input reaches the handler.\n### Users\n- Operators\n"
    "### Outcome\nBad input is refused.\n\n## What Changes\nValidate it.\n### Non-goals\n- UI\n"
)


def _change(tmp_path: Path, options: list[op.Option] | None = None) -> rq.LoadedChange:
    directory = tmp_path / "openspec" / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(_PROPOSAL)
    change = rq.load_change(str(directory))
    if options:
        op.write_options(change, options)
    return change


_OPTS = [
    op.Option(
        title="Reuse validate", approach="Call it from the handler.", anchors=("validate",), source="user"
    ),
    op.Option(title="A new checker", approach="Write one.", creates=("app/check.py",), source="user"),
]


def _node(nid: str, name: str, file: str, kind: NodeKind = NodeKind.FUNCTION) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, 1))


def _batch() -> FactBatch:
    b = FactBatch()
    for n in (
        _node("py:core", "core", "core.py", NodeKind.MODULE),
        _node("py:core.validate", "validate", "core.py"),
        _node("py:web.handler", "handler", "web.py"),
    ):
        b.add_node(n)
    b.add_edge(Edge("py:core", "py:core.validate", EdgeKind.CONTAINS, Provenance("core.py", 1)))
    b.add_edge(Edge("py:web.handler", "py:core.validate", EdgeKind.CALLS, Provenance("web.py", 2)))
    return b


def _unbound(count: int) -> UnboundIndex:
    return UnboundIndex.from_calls(
        UnboundCall("py:web.other", "validate", "web.py", 10 + n, "obj") for n in range(count)
    )


def _inputs(batch: FactBatch | None = None, **kw: object) -> orp.RepoInputs:
    fields: dict[str, object] = {
        "batch": batch if batch is not None else _batch(),
        "root": None,
        "trusted": True,
        "unbound": _unbound(2),
        "unbound_note": "",
    }
    fields.update(kw)
    return orp.RepoInputs(**fields)  # type: ignore[arg-type]


# ---- the lookup moved, not copied -----------------------------------------------------------------


def test_the_untraced_lookup_is_one_class_shared_with_the_tools() -> None:
    import ast
    import inspect

    from orchestrator.plugin import server

    tree = ast.parse(inspect.getsource(server))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "_Untraced"]
    built = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "UntracedLookup" in built and "_Untraced" not in built


# ---- the report -----------------------------------------------------------------------------------


def test_a_change_with_no_options_says_how_to_get_some(tmp_path: Path) -> None:
    report = orp.options_report(_change(tmp_path))
    assert report["options_present"] is False and report["options"] == []
    assert report["recommendation"] is None
    assert "## Options" in report["next"] and "--generate" in report["next"]
    assert "## Options" in orp.render_markdown(report)


def test_without_a_repository_every_option_is_ungrounded_and_unranked(tmp_path: Path) -> None:
    report = orp.options_report(_change(tmp_path, _OPTS))
    assert report["grounding"] == "ungrounded"
    assert [o["measure"]["grounding"] for o in report["options"]] == ["ungrounded", "ungrounded"]
    assert report["recommendation"]["recommended"] is None
    assert all(o["unresolved_calls"] is None for o in report["options"])
    md = orp.render_markdown(report)
    assert "| 1 | Reuse validate | user | — | — | — | — | — | — | — |" in md
    assert "ungrounded" in md


def test_each_option_is_measured_and_the_rule_recommends_one(tmp_path: Path) -> None:
    report = orp.options_report(_change(tmp_path, _OPTS), _inputs())
    first, second = report["options"]
    assert first["number"] == 1 and first["title"] == "Reuse validate"
    assert first["measure"]["resolved"] == ["validate"]
    assert first["measure"]["callers"] == {"at_least": 1}
    assert report["recommendation"]["recommended"] == "Reuse validate"
    assert report["question"] == rq.CHOICE_QUESTION
    assert "Which option do we take?" in report["next"]
    md = orp.render_markdown(report)
    assert om.RULE in md and om.BIAS in md
    assert "**Recommended:** Option 1 — Reuse validate" in md


def test_untraced_calls_sit_beside_the_caller_floor_never_in_it(tmp_path: Path) -> None:
    report = orp.options_report(_change(tmp_path, _OPTS), _inputs(unbound=_unbound(3)))
    first = report["options"][0]
    assert first["unresolved_calls"] == {"count": 3, "functions": 1}
    assert first["measure"]["callers"] == {"at_least": 1}  # not 1 + 3
    # No anchors is ungrounded: unknown, never a clean zero.
    assert report["options"][1]["unresolved_calls"] is None
    assert "ungrounded" in report["options"][1]["unresolved_calls_note"]
    assert "| 1 | Reuse validate | user | 1 of 1 | 1 | 3 |" in orp.render_markdown(report)


def test_untraced_calls_not_loaded_are_unknown_not_zero(tmp_path: Path) -> None:
    report = orp.options_report(_change(tmp_path, _OPTS), _inputs(unbound=None, unbound_note="OSError"))
    first = report["options"][0]
    assert first["unresolved_calls"] is None
    assert "not available (OSError)" in first["unresolved_calls_note"]
    assert "| 1 | Reuse validate | user | 1 of 1 | 1 | n/a |" in orp.render_markdown(report)


def test_untraced_calls_of_an_untracked_language_are_unknown_not_zero(tmp_path: Path) -> None:
    b = FactBatch()
    b.add_node(Node("go:core.Validate", NodeKind.FUNCTION, "validate", "go", Provenance("core.go", 1)))
    report = orp.options_report(_change(tmp_path, _OPTS), _inputs(batch=b))
    assert report["options"][0]["unresolved_calls"] is None
    assert "not tracked for go" in report["options"][0]["unresolved_calls_note"]


def test_option_text_in_the_table_is_inert(tmp_path: Path) -> None:
    hostile = op.Option(title="x | y <img src=z> @someone", approach="a", source="user")
    report = orp.options_report(_change(tmp_path, [hostile, _OPTS[1]]))
    md = orp.render_markdown(report)
    assert "x \\| y &lt;img src=z&gt;" in md
    assert "<img" not in md


def test_the_same_inputs_give_the_same_bytes(tmp_path: Path) -> None:
    change = _change(tmp_path, _OPTS)
    first = orp.render_markdown(orp.options_report(change, _inputs()))
    second = orp.render_markdown(orp.options_report(change, _inputs()))
    assert first == second


def test_reader_problems_are_reported_beside_the_table(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("# D\n\n## Options\n\n### Option 1 — Half\n- **Risks:** r\n")
    report = orp.options_report(change)
    assert any("no Approach" in p for p in report["problems"])
    assert "no Approach" in orp.render_markdown(report)


# ---- on a real repository: docs, landings, the batch left as it was --------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "core.py").write_text("def validate(x):\n    return bool(x)\n")
    (repo / "app" / "web.py").write_text(
        "from app.core import validate\n\n\ndef handler(x):\n    return validate(x)\n"
    )
    (repo / "README.md").write_text("# App\n\n## Validation\n\nInput goes through `validate` first.\n")
    return repo


def test_docs_are_measured_on_a_linked_copy_and_the_code_graph_is_untouched(tmp_path: Path) -> None:
    repo = orp.read_repo(_repo(tmp_path))
    assert repo.batch is not None
    edges_before = len(repo.batch.edges)
    report = orp.options_report(_change(tmp_path, _OPTS), repo)
    # Not a git checkout, so nothing it says is reproducible at a commit: said, not hidden.
    assert report["grounding"] == "untrusted"
    assert "untrusted" in report["markdown"]
    assert report["options"][0]["measure"]["docs"] == 1
    assert report["docs"]["read"] is True
    assert len(repo.batch.edges) == edges_before
    assert not any(e.kind is EdgeKind.MENTIONS for e in repo.batch.edges)


def test_the_generation_prompt_is_built_from_the_graph_and_its_docs(tmp_path: Path) -> None:
    repo = orp.read_repo(_repo(tmp_path))
    prompt = orp.generation_prompt(_change(tmp_path), repo)
    assert prompt.part("code").kept > 0
    assert prompt.part("docs").kept == 1
    assert prompt.part("overview").kept > 0
    user = prompt.messages[-1].content
    assert "validate" in user and "Input goes through" in user


def test_without_a_repository_the_prompt_says_it_has_no_code(tmp_path: Path) -> None:
    prompt = orp.generation_prompt(_change(tmp_path))
    assert [p.offered for p in prompt.parts if p.name != "why"] == [0, 0, 0]


def test_a_refused_generation_is_in_the_report_with_its_choice(tmp_path: Path) -> None:
    estimate = idn.CostEstimate(model="m", usd=0.2, prompt_tokens=10, output_tokens=2000, priced=True)
    refusal = idn.Refusal(
        estimate_usd=0.2,
        breaches=(idn.Breach("call_cap", 0.10, 0.2),),
        message="Not called: the estimate $0.2000 for m is over the $0.10 cap per call.",
    )
    result = idn.GenerationResult(status="refused", refusal=refusal, model="m", estimate=estimate)
    report = orp.options_report(_change(tmp_path), generation=result)
    gen = report["generation"]
    assert gen["status"] == "refused" and gen["refusal"]["choices"] == ["approve", "park"]
    assert gen["refusal"]["breaches"] == [{"limit": "call_cap", "limit_usd": 0.10, "would_be_usd": 0.2}]
    assert "Not called" in orp.render_markdown(report)


# ---- PR 2 review (SSPN-312) ------------------------------------------------------------------------


def test_with_two_options_of_one_title_the_recommendation_names_the_right_number(tmp_path: Path) -> None:
    # R4: the markdown looked the number up by title and named the first "Dup".
    change = _change(tmp_path)
    text = op.set_options(
        "# Design\n",
        [
            op.Option(title="Dup", approach="a", anchors=("ghost",), source="user"),
            op.Option(title="Dup two", approach="b", anchors=("validate",), source="user"),
        ],
    ).replace("— Dup two", "— Dup")
    change.design_path.write_text(text)
    report = orp.options_report(rq.load_change(str(change.directory)), _inputs())
    assert report["recommendation"]["number"] == 2
    assert "**Recommended:** Option 2 — Dup" in orp.render_markdown(report)


def test_the_next_command_survives_a_path_with_a_space(tmp_path: Path) -> None:
    # R10: the path was pasted raw into a command a person or an agent runs.
    directory = tmp_path / "openspec" / "changes" / "my chg"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(_PROPOSAL)
    report = orp.options_report(rq.load_change(str(directory)))
    command = report["next"].split("or run: ", 1)[1].split(" (one capped", 1)[0]
    assert shlex.split(command)[3] == str(directory.resolve())


def test_docs_never_read_are_unknown_not_zero(tmp_path: Path) -> None:
    # R11: with no checkout to read docs from, every option showed "0" docs.
    report = orp.options_report(_change(tmp_path, _OPTS), _inputs(root=None))
    assert report["docs"]["read"] is False
    assert report["options"][0]["measure"]["docs"] is None
