"""Per-skill task sets are well-formed (persona-skill measurement P1).

These are eval *fixtures* — the held-out suites are the independent judges in
P2's A/B, so a malformed one (won't parse, imports the wrong module, duplicate
id) would silently corrupt a measurement. This locks their shape without spending
a single LLM call.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IMPORT_RE = re.compile(r"from\s+(orchestrator\.bench_\w+)\s+import\s+(\w+)")


def _load_benchmark() -> ModuleType:
    import sys  # noqa: PLC0415

    for p in (str(_REPO / "src"), str(_REPO / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(
        "codegen_benchmark", _REPO / "scripts" / "codegen_benchmark.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so frozen dataclasses can resolve their own module.
    sys.modules["codegen_benchmark"] = mod
    spec.loader.exec_module(mod)
    return mod


_BENCH = _load_benchmark()
_TASKSETS = _BENCH.SKILL_TASKSETS
_ALL_TICKETS = [t for tickets in _TASKSETS.values() for t in tickets]


def test_tasksets_cover_the_three_candidate_skills() -> None:
    from orchestrator.catalog.skills import default_skills

    candidates = {"test-strategy", "security-aware-coding", "convention-digest"}
    assert set(_TASKSETS) == candidates
    # every keyed skill is a real (authored) Skill artifact
    skill_ids = {s.id for s in default_skills()}
    assert candidates <= skill_ids


def test_taskset_accessor() -> None:
    assert _BENCH.taskset("test-strategy") is _TASKSETS["test-strategy"]
    assert _BENCH.taskset("does-not-exist") == []


def test_every_set_has_multiple_tickets() -> None:
    for skill_id, tickets in _TASKSETS.items():
        assert len(tickets) >= 3, f"{skill_id} task set is too thin to beat noise"


def test_ticket_ids_are_globally_unique() -> None:
    ids = [t.key for t in _ALL_TICKETS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("ticket", _ALL_TICKETS, ids=lambda t: t.key)
def test_ticket_ships_a_parseable_held_out_suite(ticket: Any) -> None:
    held = ticket.held_out_tests
    assert held, f"{ticket.key} has no held-out suite — no independent signal"
    for name, source in held.items():
        assert name.startswith("test_") and name.endswith(".py")
        ast.parse(source)  # raises SyntaxError if malformed → fails the test
        assert "def test_" in source


@pytest.mark.parametrize("ticket", _ALL_TICKETS, ids=lambda t: t.key)
def test_held_out_import_target_is_pinned_in_the_contract(ticket: Any) -> None:
    """The module the held-out suite imports must be the one the spec pins — else
    the suite can't import the model's output and the measurement is structurally
    broken regardless of model quality."""
    spec_text = " ".join(
        [
            ticket.spec.get("summary", ""),
            ticket.spec.get("technical_notes", ""),
            *ticket.spec.get("acceptance_criteria", []),
        ]
    )
    for source in ticket.held_out_tests.values():
        targets = _IMPORT_RE.findall(source)
        assert targets, f"{ticket.key} held-out suite imports no orchestrator.bench_* module"
        for module, _symbol in targets:
            assert module in spec_text, (
                f"{ticket.key}: held-out imports {module} but the spec does not pin it"
            )


def test_convention_tickets_name_a_reuse_target() -> None:
    """Each convention-digest ticket must point at an existing helper to reuse —
    that's the whole signal (reuse vs reinvent)."""
    for ticket in _TASKSETS["convention-digest"]:
        notes = ticket.spec.get("technical_notes", "")
        # names a real orchestrator module to build on (not the new bench_ module)
        assert re.search(r"orchestrator\.(codereview|pkg|core)\b", notes), ticket.key
