"""Acceptance criteria bound to the graph (Phase 2a, defect 4).

The rule that matters is not "does it bind" but **what is allowed to stop a run**. A binding
rule that parks everything would pass every parity check and be useless, so most of this file is
about the criteria that must *not* park.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from orchestrator.pkg import FactStore
from orchestrator.sdlc.criteria_binding import bind_criteria

_GRAPH_SRC = """
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance


def graph():
    b = FactBatch()
    for nid, kind, name, f, line in (
        ("py:report", NodeKind.MODULE, "report.py", "report.py", 1),
        ("py:report.render", NodeKind.FUNCTION, "render", "report.py", 10),
        ("py:report.OrderTotals", NodeKind.TYPE, "OrderTotals", "report.py", 30),
        ("py:web", NodeKind.MODULE, "web.py", "web.py", 1),
        ("py:web.render", NodeKind.FUNCTION, "render", "web.py", 5),
        # Two files whose paths both end in "report.py". `DocReconciler` holds provenance files
        # in a `set`, so a mention matching more than one comes back in hash order — without a
        # tie there is nothing for a seed to reorder, and the stability test below passes on
        # code that is genuinely unstable.
        ("py:sub.report", NodeKind.MODULE, "report.py", "sub/report.py", 1),
        ("py:sub.report.fmt", NodeKind.FUNCTION, "fmt", "sub/report.py", 4),
    ):
        b.add_node(Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(f, line)))
    return b
"""

exec(compile(_GRAPH_SRC, "<graph-fixture>", "exec"))  # noqa: S102 — shared verbatim with the subprocess


def _store() -> FactStore:
    return FactStore(graph())  # type: ignore[name-defined]  # noqa: F821 — defined by the exec above


def _one(text: str, **kwargs: object):  # type: ignore[no-untyped-def]
    return bind_criteria({"acceptance_criteria": [text]}, store=_store(), **kwargs).rows[0]  # type: ignore[arg-type]


def test_a_bound_criterion_carries_a_symbol_and_a_file_line() -> None:
    """The point of binding: a criterion nobody can locate is a test nobody can write."""
    row = _one("`OrderTotals` is recalculated on save")
    assert row.status == "bound"
    assert not row.parks
    anchor = row.anchors[0]
    assert anchor.symbol == "OrderTotals"
    assert anchor.where == "report.py:30"


def test_an_anchor_inside_the_evidence_is_flagged() -> None:
    """Binding *where the ticket lands* is stronger than binding a same-named symbol on the
    other side of the repo — `render` exists in two modules here."""
    row = _one("`OrderTotals` is recalculated", evidence_files=("report.py",))
    assert row.anchors[0].in_evidence is True
    elsewhere = _one("`OrderTotals` is recalculated", evidence_files=("nowhere.py",))
    assert elsewhere.anchors[0].in_evidence is False


def test_a_criterion_naming_code_that_does_not_exist_parks_the_run() -> None:
    """The false-premise case. Code built to it passes its own tests and is still wrong — the
    same argument `assess()` already makes for a false count."""
    row = _one("`GhostWidget` is removed")
    assert row.status == "unbound"
    assert row.parks
    assert row.claims == ("GhostWidget",)


@pytest.mark.parametrize(
    ("criterion", "why"),
    [
        ("GitHubActions runs the tests", "CamelCase prose is not a code claim"),
        ("set ORCHESTRATOR_MEMORY_BANK_DIR first", "ALL-CAPS is an env var, not a symbol"),
        ("use the `ruff` tool", "a plain backticked word is a tool name"),
        ("the report should be readable", "prose names nothing checkable"),
        ("performance must not regress", "prose names nothing checkable"),
    ],
)
def test_what_must_never_park_a_run(criterion: str, why: str) -> None:
    """Most acceptance criteria are prose. A rule that parked on all of them would pass every
    parity check in Phase 2a's gate and be useless in production."""
    row = _one(criterion)
    assert not row.parks, f"{why}: {criterion!r} parked the run"
    assert row.status == "no-claim"


def test_a_spec_with_no_criteria_binds_nothing_and_parks_nothing() -> None:
    binding = bind_criteria({}, store=_store())
    assert binding.rows == ()
    assert binding.parks is False


def test_every_criterion_appears_in_the_render_not_just_the_failures() -> None:
    """Listing only failures leaves a reader unable to tell "all bound" from "none checked"."""
    binding = bind_criteria(
        {"acceptance_criteria": ["`render` returns a string", "`Ghost` is gone", "be readable"]},
        store=_store(),
    )
    rendered = binding.render()
    assert "`render` returns a string" in rendered
    assert "`Ghost` is gone" in rendered
    assert "be readable" in rendered
    assert "1 bound · 1 unbound · 1 not a code claim" in rendered


def test_the_binding_is_stable_across_hash_seeds() -> None:
    """`DocReconciler` collects provenance files in a `set`, so file anchors come out in hash
    order. Unsorted, two identical runs produce different bindings — the `state` bug of 3.19.0
    in a new place, and invisible in-process because the seed is fixed per process.
    """
    script = "\n".join(
        [
            "import json",
            "from orchestrator.pkg import FactStore",
            _GRAPH_SRC,
            "from orchestrator.sdlc.criteria_binding import bind_criteria",
            'spec = {"acceptance_criteria": ["`render` returns a string", "`report.py` is touched"]}',
            "b = bind_criteria(spec, store=FactStore(graph()))",
            "print(json.dumps(b.to_dict(), sort_keys=True))",
        ]
    )
    seen = set()
    for seed in ("0", "1", "42", "12345", "99991"):
        out = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        )
        seen.add(json.dumps(json.loads(out.stdout.strip().splitlines()[-1]), sort_keys=True))
    assert len(seen) == 1, f"criterion binding is not stable across hash seeds: {len(seen)} results"
