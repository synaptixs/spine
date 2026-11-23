"""G2 PR-B, phase B2: the files a plan proposes say how sure the plan is of them.

B165 (D8, decided label-only on 2026-11-23), B175 + D6 (the "renders UI" graph fact), B171's honest
line (D7). Synthetic fixtures; the field report's UI-task shape is a ticket whose plan names only a
service file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import FactStore, RepoCodeExtractor
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.builddoc import build_plan, collect_evidence, render_build_md
from tests.sdlc.test_builddoc import _Assessment, _design, _Investigation, _spec


def _section(md: str, title: str) -> str:
    return md.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


# ---- D6: the TS/JS front-end marks a module that renders UI ------------------------------------------------


def _extract(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _ui(batch: FactBatch) -> dict[str, bool]:
    return {n.id: n.ui for n in batch.nodes if n.kind is NodeKind.MODULE and not n.external}


def test_d6_jsx_in_a_tsx_file_is_ui_and_a_tsx_of_helpers_is_not(tmp_path: Path) -> None:
    ui = _ui(
        _extract(
            tmp_path,
            {
                "src/screens/login.tsx": "export function Login() { return <div><b /></div>; }\n",
                "src/util/math.tsx": (
                    'export function lt<T>(a: T, b: T) { return a < b; }\nexport const c = "</x>";\n'
                ),
                "src/services/api.ts": "export function get() { return 1; }\n",
            },
        )
    )

    assert ui == {"ts:src/screens/login": True, "ts:src/util/math": False, "ts:src/services/api": False}


def test_d6_a_javascript_component_is_ui_through_the_inherited_reader(tmp_path: Path) -> None:
    ui = _ui(
        _extract(
            tmp_path,
            {
                "src/Button.jsx": "export const Button = () => <button>ok</button>;\n",
                "src/fetch.js": "export function fetchAll() { return []; }\n",
            },
        )
    )

    assert ui == {"ts:src/Button": True, "ts:src/fetch": False}


def test_d6_a_lone_self_closing_element_is_ui(tmp_path: Path) -> None:
    ui = _ui(_extract(tmp_path, {"src/Icon.tsx": "export const Icon = () => <img />;\n"}))

    assert ui == {"ts:src/Icon": True}


def test_d6_the_fact_survives_the_cache_round_trip() -> None:
    from orchestrator.pkg.persistence import facts_from_dict, facts_to_dict

    b = FactBatch()
    b.add_node(Node("ts:a", NodeKind.MODULE, "a", "typescript", Provenance("a.tsx", 1), ui=True))
    b.add_node(Node("ts:b", NodeKind.MODULE, "b", "typescript", Provenance("b.ts", 1)))
    payload = facts_to_dict(b)

    assert [n.get("ui") for n in payload["nodes"]] == [True, None]  # absent when unset
    assert {n.id: n.ui for n in facts_from_dict(payload).nodes} == {"ts:a": True, "ts:b": False}


def test_d6_a_contradicted_ui_label_fails_the_corpus_case(tmp_path: Path) -> None:
    import json

    from orchestrator.pkg.accuracy import CorpusError, score_case

    (tmp_path / ".repo").mkdir()
    (tmp_path / ".repo/a.ts").write_text("export function f() { return 1; }\n", encoding="utf-8")
    (tmp_path / "expected.json").write_text(
        json.dumps(
            {
                "language": "typescript",
                "case": "x",
                "root": ".repo",
                "nodes": [{"id": "ts:a", "kind": "Module", "ui": True}, {"id": "ts:a.f", "kind": "Function"}],
                "edges": [{"src": "ts:a", "dst": "ts:a.f", "kind": "CONTAINS"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="UI fact contradicts"):
        score_case(tmp_path)


# ---- D6 in the plan: collect_evidence and §6 -------------------------------------------------------------


def _ui_store() -> FactStore:
    b = FactBatch()
    for i, (f, ui) in enumerate(
        (
            ("src/screens/login.tsx", True),
            ("src/screens/signup.tsx", True),
            ("src/services/session.ts", False),
        )
    ):
        b.add_node(Node(f"m:{i}", NodeKind.MODULE, f, "typescript", Provenance(f, 1, 9), ui=ui))
    return FactStore(b)


def test_d6_evidence_counts_ui_modules_in_the_graph_and_among_the_listed_files(tmp_path: Path) -> None:
    ev = collect_evidence(_ui_store(), files=["src/services/session.ts"], root=tmp_path)
    assert ev["ui_in_graph"] == 2
    assert ev["ui_listed"] == []

    ev = collect_evidence(_ui_store(), files=["src/screens/login.tsx"], root=tmp_path)
    assert ev["ui_listed"] == ["src/screens/login.tsx"]


def _render(tmp_path: Path, **over: Any) -> str:
    return render_build_md(
        over.pop("spec", _spec()),
        investigation=_Investigation(),
        design=over.pop("design", _design()),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
        **over,
    )


_SPINNER = _spec(
    title="Show a spinner while signing in",
    summary="Show a loading spinner on the sign-in and sign-up screens while the request runs.",
)


def test_b175_a_ui_ticket_with_no_ui_module_listed_is_flagged_in_section_6(tmp_path: Path) -> None:
    md = _render(
        tmp_path,
        spec=_SPINNER,
        design=_design(files_to_touch=["src/services/session.ts"]),
        evidence={"ui_listed": [], "ui_in_graph": 2},
    )
    six = _section(md, "6. Design")

    assert "**No UI module listed.**" in six
    assert "2 module(s)" in six
    assert "no file of the ticket's kind is listed" in _section(md, "12. Confidence")


def test_b175_no_flag_when_a_ui_module_is_listed_or_the_graph_has_no_ui_fact(tmp_path: Path) -> None:
    listed = _render(
        tmp_path, spec=_SPINNER, evidence={"ui_listed": ["src/screens/login.tsx"], "ui_in_graph": 2}
    )
    no_fact = _render(tmp_path, spec=_SPINNER, evidence={"ui_listed": [], "ui_in_graph": 0})

    assert "No UI module listed" not in listed
    assert "No UI module listed" not in no_fact


async def test_b175_the_field_ui_task_shape_end_to_end(tmp_path: Path) -> None:
    """A real extraction and a real plan: a UI ticket whose spec names only the service file."""
    files = {
        "src/screens/login/index.tsx": (
            "export function Login() { return <form><button>go</button></form>; }\n"
        ),
        "src/screens/signup/index.tsx": "export function SignUp() { return <form />; }\n",
        "src/services/session.ts": "export function signIn(user: string) { return user.length > 0; }\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    spec = {
        "intent_id": "T-528",
        "title": "Show a spinner while signing in",
        "summary": "Show a loading spinner while signIn in src/services/session.ts runs.",
        "acceptance_criteria": ["A spinner shows while the request runs; add a test."],
    }
    md = await build_plan(spec, root=tmp_path, issue_type="Task", language="typescript")

    assert "**No UI module listed.**" in _section(md, "6. Design")
    confidence = _section(md, "12. Confidence")
    assert "— high**" not in confidence
    for reason in ("fallback template", "no file of the ticket's kind", "lists no test file"):
        assert reason in confidence


# ---- B165 (label-only): a ticket-named path is labelled, never split -----------------------------------


async def test_b165_each_ticket_named_path_is_labelled_confirm_before_editing(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/walk.py").write_text("def walk():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg/docs.py").write_text("def read():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Docs walker skips files",
        "summary": "The walker in pkg/walk.py is fine; the bug is in pkg/docs.py.",
        "acceptance_criteria": [],
    }
    md = await build_plan(
        spec, root=tmp_path, source_text="The walker in pkg/walk.py is fine; the bug is in pkg/docs.py."
    )
    seven = _section(md, "7. Files")

    # Label-only (D8 outcome): both stay listed, each says how it got there.
    assert "| `pkg/walk.py` |" in seven and "| `pkg/docs.py` |" in seven
    assert "- `pkg/walk.py` — named by the ticket — confirm before editing" in seven
    assert "- `pkg/docs.py` — named by the ticket — confirm before editing" in seven
    assert "2 listed file(s) are here because the ticket or its spec names them — confirm before editing" in (
        _section(md, "6. Design")
    )


async def test_b165_a_path_only_the_spec_names_says_so(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/docs.py").write_text("def read():\n    return 1\n", encoding="utf-8")
    spec = {"intent_id": "T-1", "title": "Docs", "summary": "Fix pkg/docs.py.", "acceptance_criteria": []}
    md = await build_plan(spec, root=tmp_path, source_text="The docs reader drops sections.")

    assert (
        "- `pkg/docs.py` — named by the intake spec (a model wrote it), not by the ticket"
        " — confirm before editing" in md
    )


def test_b165_a_named_path_the_design_does_not_list_is_not_labelled(tmp_path: Path) -> None:
    """A model-written design may drop a path the spec names; only listed files carry the label."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/docs.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg/walk.py").write_text("x = 1\n", encoding="utf-8")
    md = _render(
        tmp_path,
        spec=_spec(summary="See pkg/docs.py and pkg/walk.py."),
        design=_design(files_to_touch=["pkg/docs.py"], llm=True),
        source_text="See pkg/docs.py and pkg/walk.py.",
    )

    assert "- `pkg/docs.py` — named by the ticket — confirm before editing" in md
    assert "- `pkg/walk.py`" not in md


async def test_b165_a_design_with_no_named_path_has_no_label(tmp_path: Path) -> None:
    (tmp_path / "cart.py").write_text("def total():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Cart total",
        "summary": "The cart total is off.",
        "acceptance_criteria": [],
    }
    md = await build_plan(spec, root=tmp_path, source_text="The cart total is off.")

    assert "confirm before editing" not in md


# ---- B171 (D7): the honest line when only the named file was considered ----------------------------------


async def test_b171_only_the_named_file_considered_says_its_neighbours_were_not_searched(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/docs.py").write_text("def read():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg/walk.py").write_text(
        "from pkg.docs import read\n\n\ndef walk():\n    return read()\n", encoding="utf-8"
    )
    spec = {"intent_id": "T-1", "title": "Docs", "summary": "Fix pkg/docs.py.", "acceptance_criteria": []}
    md = await build_plan(spec, root=tmp_path, source_text="Fix pkg/docs.py.")

    assert "Only the file the ticket names was considered; its neighbours were not searched." in _section(
        md, "6. Design"
    )


async def test_b171_no_line_when_two_files_are_listed(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    for f in ("docs", "walk"):
        (tmp_path / f"pkg/{f}.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Docs",
        "summary": "Fix pkg/docs.py and pkg/walk.py.",
        "acceptance_criteria": [],
    }
    md = await build_plan(spec, root=tmp_path, source_text="Fix pkg/docs.py and pkg/walk.py.")

    assert "neighbours were not searched" not in md
