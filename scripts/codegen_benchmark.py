"""G2 acceptance benchmark: create- AND edit-based tickets, production arm.

Broadens the 3-ticket A/B (``codegen_ab.py``) into the acceptance benchmark
the trust ladder needs: 10 realistic tickets against THIS repo — half create
new modules, half must MODIFY existing ones via the anchored-edits form
(Track 2.3). One arm only: the production configuration (PKG-grounded,
implement → tests → refine loop → CI-parity preflight).

Acceptance per ticket = generated tests pass AND preflight (ruff + format +
mypy --strict, repo config) passes AND the change fits:

  - create tickets: lands inside the package, imports the real model, no
    tracked file clobbered;
  - edit tickets: the named target file IS modified, and no parallel
    non-test module was created instead.

Read-only with respect to the repo — all writes land in throwaway git
worktrees under /tmp. Cost is accounted per ticket via RecordingLLMClient.

Usage:
    uv run python scripts/codegen_benchmark.py            # full sample
    BENCH_TICKETS=EDIT-STATS-1,NEW-RUNREPORT-1 uv run python scripts/codegen_benchmark.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from orchestrator.core.env import load_local_env  # noqa: E402
from orchestrator.core.llm import (  # noqa: E402
    LiteLLMClient,
    LLMError,
    RecordingLLMClient,
)
from orchestrator.evals.graders import (  # noqa: E402
    read_source,
    reused_existing_symbols,
    run_held_out_tests,
    semgrep_findings,
)
from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter  # noqa: E402
from orchestrator.sdlc.grounding import PKGCodegenGrounder  # noqa: E402
from orchestrator.sdlc.preflight import (  # noqa: E402
    _JSON_CHECKS,
    Baseline,
    PreflightBaselineError,
    SubprocessPreflightRunner,
)


@dataclass(frozen=True)
class Ticket:
    key: str
    kind: str  # "create" | "edit"
    spec: dict[str, Any]
    # Edit tickets: repo-relative files that MUST be modified for acceptance.
    must_edit: list[str] = field(default_factory=list)
    # Independent grading (persona-skill measurement P0): a hidden reference suite
    # (filename → content) the model NEVER sees, run against its implementation.
    # Independent acceptance = this suite passes — the headroom a skill needs to
    # show up, since it judges edge/error/boundary cases a thin solution misses.
    # Populated per-skill in P1; empty here keeps the stock benchmark unchanged.
    held_out_tests: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Held-out reference suites for the G2 set.
#
# Until 2026-08-15 these ten tickets carried NO held-out tests, so `tests=PASS`
# meant *the model's own tests passed* — the model wrote both the implementation
# and its grader. That is not the SWE-bench signal, which uses human-authored
# FAIL_TO_PASS tests, and it made the `resolved` number self-graded and
# inflatable (see docs/specs/codegen-model-comparison-results.md).
#
# The model never sees these. `preflight` and `fit` were already objective; this
# closes the last self-graded signal.
#
# `create` tickets deliberately do NOT pin a module path — choosing the right
# home is part of what grounding is being measured on — so their suites locate
# the function by scanning the package, and assert *invariants* rather than an
# exact rendering. A held-out test that demanded one specific Markdown layout
# would fail a correct implementation that formatted it differently, which would
# make the benchmark worse rather than better.
# --------------------------------------------------------------------------

_HO_FIND = '''
import importlib, pkgutil


def _find(pkg_name, func_name):
    """Locate a function the model placed somewhere inside `pkg_name`."""
    pkg = importlib.import_module(pkg_name)
    for m in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module(f"{pkg_name}.{m.name}")
        except Exception:
            continue
        fn = getattr(mod, func_name, None)
        if callable(fn):
            return fn
    raise AssertionError(f"{func_name} not found anywhere in {pkg_name}")
'''

_HO_EDIT_STATS = """
from orchestrator.pkg.stats import FunctionCallFrequency, mean_call_count


def _f(name, count):
    return FunctionCallFrequency(node_id=f"py:{name}", name=name, call_count=count)


def test_empty_is_zero():
    assert mean_call_count([]) == 0.0


def test_arithmetic_mean():
    assert mean_call_count([_f("a", 1), _f("b", 2), _f("c", 3)]) == 2.0


def test_single_element():
    assert mean_call_count([_f("a", 7)]) == 7.0


def test_non_integral_mean():
    assert abs(mean_call_count([_f("a", 1), _f("b", 2)]) - 1.5) < 1e-9


def test_returns_a_float():
    assert isinstance(mean_call_count([_f("a", 1)]), float)


def test_does_not_mutate_input():
    xs = [_f("a", 1), _f("b", 2)]
    mean_call_count(xs)
    assert [x.call_count for x in xs] == [1, 2]
"""

_HO_EDIT_LEDGER = """
from orchestrator.core.llm.recording import StageUsage


def test_zero_calls_is_zero_not_zero_division():
    assert StageUsage(stage="s", calls=0, cost_usd=1.0).cost_per_call == 0.0


def test_divides_cost_by_calls():
    assert StageUsage(stage="s", calls=4, cost_usd=2.0).cost_per_call == 0.5


def test_zero_cost_is_zero():
    assert StageUsage(stage="s", calls=3, cost_usd=0.0).cost_per_call == 0.0


def test_is_a_property_not_a_method():
    assert isinstance(type(StageUsage(stage="s")).__dict__["cost_per_call"], property)
"""

_HO_EDIT_BUDGET = """
import math

from orchestrator.core.llm.budget import RunBudget


def test_disabled_enforcement_is_infinite():
    assert math.isinf(RunBudget(max_cost_usd=0.0).remaining())
    assert math.isinf(RunBudget(max_cost_usd=-1.0).remaining())


def test_full_budget_when_nothing_spent():
    assert RunBudget(max_cost_usd=10.0).remaining("r") == 10.0


def test_subtracts_the_runs_spend():
    assert RunBudget(max_cost_usd=10.0, spent_usd={"r": 4.0}).remaining("r") == 6.0


def test_clamped_at_zero_when_overspent():
    assert RunBudget(max_cost_usd=10.0, spent_usd={"r": 25.0}).remaining("r") == 0.0


def test_other_runs_spend_is_not_counted():
    assert RunBudget(max_cost_usd=10.0, spent_usd={"other": 5.0}).remaining("r") == 10.0
"""

_HO_EDIT_DIFF = """
from orchestrator.codereview.diff_utils import count_added_lines

PATCH = "--- a/f.py\\n+++ b/f.py\\n@@ -1,2 +1,3 @@\\n ctx\\n+one\\n+two\\n-gone\\n"


def test_none_is_zero():
    assert count_added_lines(None) == 0


def test_empty_is_zero():
    assert count_added_lines("") == 0


def test_counts_only_added_lines():
    assert count_added_lines(PATCH) == 2


def test_does_not_count_the_file_header():
    assert count_added_lines("--- a/x\\n+++ b/x\\n+one\\n") == 1


def test_no_additions_is_zero():
    assert count_added_lines("--- a/x\\n+++ b/x\\n-gone\\n ctx\\n") == 0
"""

_HO_EDIT_VERIFIER = """
from orchestrator.codereview.verifiers import Finding, Severity, findings_by_severity


def _f(sev, msg):
    return Finding(verifier_id="v", rule="r", severity=sev, path="p.py", line=1, message=msg)


def test_empty_input_is_empty_dict():
    assert findings_by_severity([]) == {}


def test_severities_with_no_findings_are_absent():
    assert set(findings_by_severity([_f(Severity.NIT, "a")])) == {Severity.NIT}


def test_groups_by_severity():
    out = findings_by_severity([_f(Severity.BLOCKER, "b"), _f(Severity.NIT, "n")])
    assert [x.message for x in out[Severity.BLOCKER]] == ["b"]
    assert [x.message for x in out[Severity.NIT]] == ["n"]


def test_preserves_input_order_within_a_group():
    out = findings_by_severity([_f(Severity.NIT, "1"), _f(Severity.NIT, "2"), _f(Severity.NIT, "3")])
    assert [x.message for x in out[Severity.NIT]] == ["1", "2", "3"]
"""

_HO_NEW_GRAPHMD = (
    _HO_FIND
    + """
from orchestrator.pkg.stats import FunctionCallFrequency, GraphStats


def _stats():
    return GraphStats(
        node_counts={}, edge_counts={}, total_nodes=3, total_edges=2,
        top_called_functions=[FunctionCallFrequency(node_id="py:alpha", name="alpha", call_count=5)],
    )


def test_returns_non_empty_markdown():
    out = _find("orchestrator.pkg", "render_stats_markdown")(_stats())
    assert isinstance(out, str) and out.strip()


def test_reports_the_totals():
    out = _find("orchestrator.pkg", "render_stats_markdown")(_stats())
    assert "3" in out and "2" in out


def test_names_the_most_called_function():
    out = _find("orchestrator.pkg", "render_stats_markdown")(_stats())
    assert "alpha" in out


def test_handles_an_empty_graph():
    out = _find("orchestrator.pkg", "render_stats_markdown")(
        GraphStats(node_counts={}, edge_counts={}, total_nodes=0, total_edges=0, top_called_functions=[])
    )
    assert isinstance(out, str)
"""
)

_HO_NEW_LEDGERMD = (
    _HO_FIND
    + """
from orchestrator.core.llm.recording import StageUsage, TokenLedger


def _ledger():
    led = TokenLedger()
    led.stages["intake"] = StageUsage(
        stage="intake", calls=2, prompt_tokens=100, completion_tokens=50, cost_usd=0.25
    )
    led.stages["codegen"] = StageUsage(
        stage="codegen", calls=1, prompt_tokens=10, completion_tokens=5, cost_usd=0.75
    )
    return led


def _render():
    return _find("orchestrator.core.llm", "render_ledger_markdown")(_ledger())


def test_returns_non_empty_markdown():
    assert isinstance(_render(), str) and _render().strip()


def test_has_a_row_per_stage():
    out = _render()
    assert "intake" in out and "codegen" in out


def test_has_a_total_row():
    assert "TOTAL" in _render().upper()


def test_reports_the_token_counts():
    out = _render()
    assert "100" in out and "50" in out
"""
)

_HO_NEW_DRIFTMD = (
    _HO_FIND
    + """
def test_returns_non_empty_markdown_and_names_the_mentions():
    import importlib, pkgutil

    fn = None
    for pkg_name in ("orchestrator.pkg", "orchestrator.knowledge"):
        try:
            fn = _find(pkg_name, "render_drift_markdown")
            break
        except AssertionError:
            continue
    assert fn is not None, "render_drift_markdown not found"

    class F:
        def __init__(self, page_title, mention, kind):
            self.page_title = page_title
            self.mention = mention
            self.kind = kind

    out = fn([F("Design", "missing_symbol", "symbol"), F("Design", "gone.py", "file")])
    assert isinstance(out, str) and out.strip()
    assert "missing_symbol" in out and "gone.py" in out
    assert "Design" in out
"""
)

_HO_NEW_SEVSUMMARY = (
    _HO_FIND
    + """
from orchestrator.codereview.verifiers import Finding, Severity


def _f(sev):
    return Finding(verifier_id="v", rule="r", severity=sev, path="p.py", line=1, message="m")


def _fn():
    for pkg_name in ("orchestrator.codereview", "orchestrator"):
        try:
            return _find(pkg_name, "summarise_findings")
        except AssertionError:
            continue
    raise AssertionError("summarise_findings not found")


def test_returns_a_single_line_string():
    out = _fn()([_f(Severity.BLOCKER)])
    assert isinstance(out, str) and out.strip()
    assert "\\n" not in out.strip()


def test_empty_is_a_clean_review_message():
    out = _fn()([])
    assert isinstance(out, str) and out.strip()


def test_counts_each_severity_present():
    out = _fn()([_f(Severity.BLOCKER), _f(Severity.BLOCKER), _f(Severity.WARNING)])
    assert "2" in out and "1" in out


def test_highest_severity_is_reported_first():
    out = _fn()([_f(Severity.NIT), _f(Severity.BLOCKER)]).lower()
    assert out.index("block") < out.index("nit")
"""
)

_HO_NEW_APPROVALMD = (
    _HO_FIND
    + """
def test_renders_each_request_with_its_fields():
    fn = None
    for pkg_name in ("orchestrator.approval", "orchestrator.registry", "orchestrator.notify"):
        try:
            fn = _find(pkg_name, "render_approvals_markdown")
            break
        except AssertionError:
            continue
    assert fn is not None, "render_approvals_markdown not found"

    class R:
        def __init__(self, title, risk, state, created_at):
            self.title = title
            self.risk_classification = risk
            self.risk = risk
            self.state = state
            self.created_at = created_at

    out = fn([R("Deploy the thing", "high", "pending", "2026-01-01T00:00:00Z")])
    assert isinstance(out, str) and out.strip()
    assert "Deploy the thing" in out
    assert "pending" in out
"""
)


TICKETS: list[Ticket] = [
    # ---- edit-based: the feature lives INSIDE an existing module ----------
    Ticket(
        key="EDIT-STATS-1",
        kind="edit",
        must_edit=["src/orchestrator/pkg/stats.py"],
        held_out_tests={"test_mean_call_count_ho.py": _HO_EDIT_STATS},
        spec={
            "title": "Mean call count for PKG graph statistics",
            "summary": (
                "Extend the existing module src/orchestrator/pkg/stats.py with a "
                "module-level function mean_call_count(frequencies: "
                "list[FunctionCallFrequency]) -> float returning the arithmetic mean "
                "of the call_count values, or 0.0 for an empty list. Modify that "
                "existing file; do not create a new module."
            ),
            "technical_notes": (
                "Surgical addition alongside the existing median_call_count helper in "
                "src/orchestrator/pkg/stats.py. Tests go in a test file importing "
                "orchestrator.pkg.stats. Full type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "mean_call_count is added to the existing src/orchestrator/pkg/stats.py",
                "mean_call_count([]) returns 0.0",
                "the input list is not mutated",
            ],
        },
    ),
    Ticket(
        key="EDIT-LEDGER-1",
        kind="edit",
        must_edit=["src/orchestrator/core/llm/recording.py"],
        held_out_tests={"test_cost_per_call_ho.py": _HO_EDIT_LEDGER},
        spec={
            "title": "StageUsage.cost_per_call property",
            "summary": (
                "Extend the existing StageUsage dataclass in "
                "src/orchestrator/core/llm/recording.py with a read-only property "
                "cost_per_call returning cost_usd / calls, or 0.0 when calls == 0. "
                "Modify that existing file; do not create a new module."
            ),
            "technical_notes": (
                "Match the style of the existing total_tokens property on StageUsage. "
                "Tests import orchestrator.core.llm (StageUsage is exported). Full "
                "type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "StageUsage.cost_per_call is added inside the existing class in "
                "src/orchestrator/core/llm/recording.py",
                "cost_per_call returns cost_usd divided by calls",
                "cost_per_call returns 0.0 when calls is 0",
            ],
        },
    ),
    Ticket(
        key="EDIT-BUDGET-1",
        kind="edit",
        must_edit=["src/orchestrator/core/llm/budget.py"],
        held_out_tests={"test_remaining_ho.py": _HO_EDIT_BUDGET},
        spec={
            "title": "RunBudget.remaining helper",
            "summary": (
                "Extend the existing RunBudget dataclass in "
                "src/orchestrator/core/llm/budget.py with a method "
                "remaining(run_id: str | None = None) -> float returning "
                "max_cost_usd minus the run's spend, clamped at 0.0; when "
                "enforcement is disabled (max_cost_usd <= 0) return float('inf'). "
                "Modify that existing file; do not create a new module."
            ),
            "technical_notes": (
                "Reuse the existing spent() lookup semantics (run_id defaults to the "
                "active run). Tests import orchestrator.core.llm.budget. Full type "
                "annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "RunBudget.remaining is added inside the existing class in "
                "src/orchestrator/core/llm/budget.py",
                "remaining is max_cost_usd - spent, never negative",
                "remaining returns float('inf') when max_cost_usd <= 0",
            ],
        },
    ),
    Ticket(
        key="EDIT-DIFF-1",
        kind="edit",
        must_edit=["src/orchestrator/codereview/diff_utils.py"],
        held_out_tests={"test_count_added_ho.py": _HO_EDIT_DIFF},
        spec={
            "title": "count_added_lines diff helper",
            "summary": (
                "Extend the existing module src/orchestrator/codereview/diff_utils.py "
                "with a function count_added_lines(patch: str | None) -> int that "
                "returns how many added lines a unified-diff patch contains (0 for "
                "None or empty). Modify that existing file; do not create a new "
                "module."
            ),
            "technical_notes": (
                "Build on the existing iter_added_lines generator in the same file — "
                "do not re-parse hunk headers. Tests import "
                "orchestrator.codereview.diff_utils. Full type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "count_added_lines is added to the existing src/orchestrator/codereview/diff_utils.py",
                "it counts exactly the lines iter_added_lines yields",
                "None or empty patch returns 0",
            ],
        },
    ),
    Ticket(
        key="EDIT-VERIFIER-1",
        kind="edit",
        must_edit=["src/orchestrator/codereview/verifiers.py"],
        held_out_tests={"test_by_severity_ho.py": _HO_EDIT_VERIFIER},
        spec={
            "title": "Group review findings by severity",
            "summary": (
                "Extend the existing module src/orchestrator/codereview/verifiers.py "
                "with a function findings_by_severity(findings: list[Finding]) -> "
                "dict[Severity, list[Finding]] grouping findings by their severity, "
                "preserving input order within each group; severities with no "
                "findings are absent from the dict. Modify that existing file; do "
                "not create a new module."
            ),
            "technical_notes": (
                "Reuse the existing Finding and Severity types defined in the same "
                "file. Tests import orchestrator.codereview.verifiers. Full type "
                "annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "findings_by_severity is added to the existing src/orchestrator/codereview/verifiers.py",
                "findings keep their input order inside each severity group",
                "an empty findings list yields an empty dict",
            ],
        },
    ),
    # ---- create-based: a new module inside the package --------------------
    Ticket(
        key="NEW-GRAPHMD-1",
        kind="create",
        held_out_tests={"test_graphmd_ho.py": _HO_NEW_GRAPHMD},
        spec={
            "title": "Render GraphStats as a Markdown report",
            "summary": (
                "Add a new module in the orchestrator.pkg package with a function "
                "render_stats_markdown(stats) that renders an "
                "orchestrator.pkg.stats.GraphStats as a Markdown report: node counts "
                "by kind, edge counts by kind, totals, and a most-called-functions "
                "table."
            ),
            "technical_notes": (
                "Consume the existing GraphStats / FunctionCallFrequency types from "
                "orchestrator.pkg.stats; do not redefine them. Full type "
                "annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "render_stats_markdown(stats) returns a Markdown string",
                "node and edge counts render one line per kind",
                "top-called functions render as a table, most-called first",
                "empty stats render without raising",
            ],
        },
    ),
    Ticket(
        key="NEW-LEDGERMD-1",
        kind="create",
        held_out_tests={"test_ledgermd_ho.py": _HO_NEW_LEDGERMD},
        spec={
            "title": "Render a token ledger as a Markdown table",
            "summary": (
                "Add a new module with a function render_ledger_markdown(ledger) that "
                "renders the per-stage token ledger (orchestrator.core.llm.TokenLedger) "
                "as a Markdown table: one row per stage plus a TOTAL row — columns for "
                "stage, calls, prompt tokens, completion tokens, total tokens and cost "
                "in USD."
            ),
            "technical_notes": (
                "Reuse the existing TokenLedger / StageUsage accounting classes from "
                "orchestrator.core.llm; do not define your own usage classes. Full "
                "type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "render_ledger_markdown(ledger) returns a Markdown table string",
                "one row per recorded stage, in insertion order",
                "a final TOTAL row sums calls, tokens and cost",
                "cost is formatted with 4 decimal places",
            ],
        },
    ),
    Ticket(
        key="NEW-DRIFTMD-1",
        kind="create",
        held_out_tests={"test_driftmd_ho.py": _HO_NEW_DRIFTMD},
        spec={
            "title": "Render doc-drift findings as a Markdown report",
            "summary": (
                "The doc-semantic layer produces drift findings when documentation "
                "references symbols or files the code doesn't define. Add a renderer "
                "that turns a list of those findings into a Markdown report grouped by "
                "page title, with one bullet per finding showing the mention and its "
                "kind."
            ),
            "technical_notes": (
                "Consume this repository's existing doc-drift finding type from the "
                "orchestrator.pkg docs module; do not re-implement mention extraction "
                "or reconciliation. Full type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "render_drift_markdown(findings) returns a Markdown string",
                "findings are grouped under a heading per page title",
                "each finding renders as a bullet naming the mention and its kind",
                "an empty findings list yields a short 'no drift' message",
            ],
        },
    ),
    Ticket(
        key="NEW-APPROVALMD-1",
        kind="create",
        held_out_tests={"test_approvalmd_ho.py": _HO_NEW_APPROVALMD},
        spec={
            "title": "Render pending approvals as a Markdown digest",
            "summary": (
                "Add a new module with a function render_approvals_markdown(requests) "
                "that renders a list of orchestrator.approval.ApprovalRequest objects "
                "as a Markdown digest: one section per request with title, risk "
                "classification, state, and created_at; ordered as given."
            ),
            "technical_notes": (
                "Consume the existing ApprovalRequest model from orchestrator.approval; "
                "do not redefine it. Full type annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "render_approvals_markdown(requests) returns a Markdown string",
                "each request renders its title, risk classification and state",
                "an empty list yields a short 'no pending approvals' message",
            ],
        },
    ),
    Ticket(
        key="NEW-SEVSUMMARY-1",
        kind="create",
        held_out_tests={"test_sevsummary_ho.py": _HO_NEW_SEVSUMMARY},
        spec={
            "title": "Summarise code-review findings for a PR comment",
            "summary": (
                "Add a new module with a function summarise_findings(findings) that "
                "turns a list of orchestrator.codereview.verifiers.Finding objects "
                "into a short plain-text summary: counts per severity (blockers "
                "first) and the worst severity, e.g. '2 blockers, 1 warning - "
                "verdict: request changes'."
            ),
            "technical_notes": (
                "Reuse the existing Finding / Severity types and the worst_severity "
                "helper from orchestrator.codereview.verifiers. Full type "
                "annotations; ruff-clean."
            ),
            "acceptance_criteria": [
                "summarise_findings(findings) returns a one-line summary string",
                "counts are reported per severity, highest severity first",
                "an empty list yields a clean-review message",
            ],
        },
    ),
]


# ---------------------------------------------------------------------------
# Per-skill task sets (persona-skill measurement P1)
# ---------------------------------------------------------------------------
#
# Signal-bearing tickets, one set per candidate skill, each shipping a HELD-OUT
# reference suite the model never sees (``Ticket.held_out_tests``). Independent
# acceptance = that suite passes (graders.run_held_out_tests) — the headroom the
# first A/B lacked.
#
# Design rules these obey (so the measurement isn't circular):
#   * Pinned interface, under-specified-but-complete contract. Each spec pins the
#     importable symbol (``orchestrator.bench_<x>``) so the held-out suite can
#     import it deterministically, while the requirements are demanding enough
#     (edges, untrusted input, a reusable helper) that a thin/naive solution
#     misses cases. The headroom lives in the contract, not in hidden tricks.
#   * Authored from real failure modes, NOT from the skill text — nothing here
#     echoes a skill's guidance string (no "test boundary values", no "validate
#     input" cribbed from the prompts). Teaching-to-the-test is the thing to avoid.
#   * The held-out suite is the independent judge; keep its assertions to the
#     stated contract so a correct impl passes and a thin one fails.
#
# Run an arm with:  EVAL_SKILL=<id> EVAL_TASKSET=<id> uv run python scripts/codegen_benchmark.py
# (baseline = same EVAL_TASKSET with EVAL_SKILL unset). P2 sizes this up to
# ~8-12 tickets x 3 repeats; these initial sets are the proving ground and are
# trivially extensible (append a Ticket).

_TS_DURATION = (
    "import pytest\n"
    "from orchestrator.bench_duration import parse_duration\n\n\n"
    "def test_happy() -> None:\n"
    "    assert parse_duration('1h30m') == 5400\n"
    "    assert parse_duration('45s') == 45\n"
    "    assert parse_duration('1h30m15s') == 5415\n\n\n"
    "def test_case_and_whitespace() -> None:\n"
    "    assert parse_duration('2H') == 7200\n"
    "    assert parse_duration('  10m  ') == 600\n\n\n"
    "def test_minutes_over_sixty_allowed() -> None:\n"
    "    assert parse_duration('90m') == 5400\n\n\n"
    "def test_empty_is_zero() -> None:\n"
    "    assert parse_duration('') == 0\n\n\n"
    "def test_invalid_raises() -> None:\n"
    "    for bad in ('abc', '10', '5x', '-3s'):\n"
    "        with pytest.raises(ValueError):\n"
    "            parse_duration(bad)\n"
)

_TS_INTLIST = (
    "import pytest\n"
    "from orchestrator.bench_intlist import parse_int_list\n\n\n"
    "def test_happy() -> None:\n"
    "    assert parse_int_list('1,2,3') == [1, 2, 3]\n"
    "    assert parse_int_list(' 1 , 2 ') == [1, 2]\n"
    "    assert parse_int_list('-5,3') == [-5, 3]\n\n\n"
    "def test_empty_and_trailing_comma() -> None:\n"
    "    assert parse_int_list('') == []\n"
    "    assert parse_int_list('   ') == []\n"
    "    assert parse_int_list('1,2,') == [1, 2]\n\n\n"
    "def test_internal_empty_and_nonint_raise() -> None:\n"
    "    for bad in ('1,,2', '1,x,3', 'a'):\n"
    "        with pytest.raises(ValueError):\n"
    "            parse_int_list(bad)\n"
)

_TS_TRUNCATE = (
    "import pytest\n"
    "from orchestrator.bench_truncate import truncate_middle\n\n\n"
    "def test_short_is_unchanged() -> None:\n"
    "    assert truncate_middle('hello', 10) == 'hello'\n"
    "    assert truncate_middle('x', 1) == 'x'\n\n\n"
    "def test_truncates_to_exact_length_with_ellipsis() -> None:\n"
    "    out = truncate_middle('abcdefghij', 5)\n"
    "    assert len(out) == 5\n"
    "    assert out.startswith('a') and out.endswith('j')\n"
    "    assert '\\u2026' in out\n\n\n"
    "def test_min_length_is_just_ellipsis() -> None:\n"
    "    assert truncate_middle('abcdef', 1) == '\\u2026'\n\n\n"
    "def test_zero_or_negative_raises() -> None:\n"
    "    with pytest.raises(ValueError):\n"
    "        truncate_middle('abc', 0)\n"
)

_TS_PERCENTILE = (
    "import pytest\n"
    "from orchestrator.bench_percentile import percentile\n\n\n"
    "def test_linear_interpolation() -> None:\n"
    "    assert percentile([1, 2, 3, 4], 50) == 2.5\n\n\n"
    "def test_min_max_and_unsorted() -> None:\n"
    "    assert percentile([3, 1, 2], 0) == 1\n"
    "    assert percentile([3, 1, 2], 100) == 3\n\n\n"
    "def test_single_value() -> None:\n"
    "    assert percentile([5], 50) == 5\n\n\n"
    "def test_does_not_mutate_input() -> None:\n"
    "    data = [3, 1, 2]\n"
    "    percentile(data, 50)\n"
    "    assert data == [3, 1, 2]\n\n\n"
    "def test_empty_and_out_of_range_raise() -> None:\n"
    "    with pytest.raises(ValueError):\n"
    "        percentile([], 50)\n"
    "    for p in (-1, 101):\n"
    "        with pytest.raises(ValueError):\n"
    "            percentile([1, 2], p)\n"
)

_TS_INTERVALS = (
    "import pytest\n"
    "from orchestrator.bench_intervals import merge_intervals\n\n\n"
    "def test_merges_overlapping() -> None:\n"
    "    assert merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]\n\n\n"
    "def test_sorts_and_merges_adjacent() -> None:\n"
    "    assert merge_intervals([(8, 10), (1, 3)]) == [(1, 3), (8, 10)]\n"
    "    assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]\n\n\n"
    "def test_empty_and_single() -> None:\n"
    "    assert merge_intervals([]) == []\n"
    "    assert merge_intervals([(1, 5)]) == [(1, 5)]\n\n\n"
    "def test_does_not_mutate_and_validates() -> None:\n"
    "    data = [(1, 3), (2, 6)]\n"
    "    merge_intervals(data)\n"
    "    assert data == [(1, 3), (2, 6)]\n"
    "    with pytest.raises(ValueError):\n"
    "        merge_intervals([(5, 1)])\n"
)

_TS_SLUG = (
    "from orchestrator.bench_slug import slugify\n\n\n"
    "def test_basic() -> None:\n"
    "    assert slugify('Hello, World!') == 'hello-world'\n"
    "    assert slugify('Foo_Bar 123') == 'foo-bar-123'\n\n\n"
    "def test_collapses_and_strips() -> None:\n"
    "    assert slugify('  a  b  ') == 'a-b'\n"
    "    assert slugify('--x--') == 'x'\n\n\n"
    "def test_empty_and_all_symbols() -> None:\n"
    "    assert slugify('') == ''\n"
    "    assert slugify('!!!') == ''\n"
)

_SEC_SAFEJOIN = (
    "import os\n"
    "import pytest\n"
    "from orchestrator.bench_safejoin import safe_join\n\n\n"
    "def test_normal_join_stays_within_base() -> None:\n"
    "    assert safe_join('/srv/data', 'a/b.txt') == os.path.normpath('/srv/data/a/b.txt')\n\n\n"
    "def test_parent_traversal_rejected() -> None:\n"
    "    for bad in ('../etc/passwd', 'a/../../etc'):\n"
    "        with pytest.raises(ValueError):\n"
    "            safe_join('/srv/data', bad)\n\n\n"
    "def test_absolute_user_path_rejected() -> None:\n"
    "    with pytest.raises(ValueError):\n"
    "        safe_join('/srv/data', '/etc/passwd')\n"
)

_SEC_HTML = (
    "from orchestrator.bench_htmlcomment import render_comment\n\n\n"
    "def test_renders_author_and_body() -> None:\n"
    "    out = render_comment('alice', 'hello')\n"
    "    assert 'alice' in out and 'hello' in out\n\n\n"
    "def test_escapes_injected_markup() -> None:\n"
    "    out = render_comment('x', '<script>alert(1)</script>')\n"
    "    assert '<script>' not in out\n"
    "    assert '&lt;script&gt;' in out\n\n\n"
    "def test_escapes_ampersand_but_keeps_structure() -> None:\n"
    "    out = render_comment('a&b', 'x')\n"
    "    assert '&amp;' in out\n"
)

_SEC_MASK = (
    "from orchestrator.bench_mask import mask_secrets\n\n\n"
    "def test_masks_aws_key() -> None:\n"
    "    out = mask_secrets('key AKIAIOSFODNN7EXAMPLE here')\n"
    "    assert 'AKIAIOSFODNN7EXAMPLE' not in out and '***' in out\n\n\n"
    "def test_masks_bearer_and_assignment() -> None:\n"
    "    assert 'abc123def' not in mask_secrets('Authorization: Bearer abc123def')\n"
    "    assert 'hunter2' not in mask_secrets('password=hunter2')\n\n\n"
    "def test_plain_text_unchanged_and_idempotent() -> None:\n"
    "    assert mask_secrets('hello world') == 'hello world'\n"
    "    once = mask_secrets('token=AKIAIOSFODNN7EXAMPLE')\n"
    "    assert mask_secrets(once) == once\n"
)

_SEC_IDENT = (
    "import pytest\n"
    "from orchestrator.bench_identifier import validate_identifier\n\n\n"
    "def test_valid_passes_through() -> None:\n"
    "    assert validate_identifier('user_42') == 'user_42'\n"
    "    assert validate_identifier('a-b') == 'a-b'\n\n\n"
    "def test_injection_and_empty_rejected() -> None:\n"
    "    for bad in ('', 'a;DROP TABLE', '../etc', 'a b'):\n"
    "        with pytest.raises(ValueError):\n"
    "            validate_identifier(bad)\n\n\n"
    "def test_length_cap() -> None:\n"
    "    with pytest.raises(ValueError):\n"
    "        validate_identifier('x' * 65)\n"
)

_SEC_REDIRECT = (
    "import pytest\n"
    "from orchestrator.bench_redirect import safe_redirect\n\n\n"
    "def test_relative_path_allowed() -> None:\n"
    "    assert safe_redirect('/dashboard', set()) == '/dashboard'\n\n\n"
    "def test_allowed_host_absolute_ok() -> None:\n"
    "    url = 'https://app.example.com/x'\n"
    "    assert safe_redirect(url, {'app.example.com'}) == url\n\n\n"
    "def test_open_redirect_vectors_rejected() -> None:\n"
    "    for bad in ('//evil.com', 'https://evil.com', 'javascript:alert(1)'):\n"
    "        with pytest.raises(ValueError):\n"
    "            safe_redirect(bad, {'app.example.com'})\n"
)

_SEC_SHELLARG = (
    "import pytest\n"
    "from orchestrator.bench_grepcmd import build_grep_command\n\n\n"
    "def test_returns_arg_list_not_shell_string() -> None:\n"
    "    cmd = build_grep_command('a;rm -rf /', 'f.txt')\n"
    "    assert isinstance(cmd, list)\n"
    "    assert 'a;rm -rf /' in cmd  # the metachars stay one literal arg\n"
    "    assert cmd[-1] == 'f.txt'\n\n\n"
    "def test_stops_option_parsing() -> None:\n"
    "    assert '--' in build_grep_command('-v', 'f.txt')\n\n\n"
    "def test_empty_pattern_rejected() -> None:\n"
    "    with pytest.raises(ValueError):\n"
    "        build_grep_command('', 'f.txt')\n"
)

_CONV_DIFFSTAT = (
    "from orchestrator.bench_diffstat import added_line_count\n\n\n"
    "_PATCH = '@@ -1,2 +1,3 @@\\n ctx\\n+added one\\n+added two\\n-removed\\n'\n\n\n"
    "def test_counts_added_lines() -> None:\n"
    "    assert added_line_count(_PATCH) == 2\n\n\n"
    "def test_empty_patch_is_zero() -> None:\n"
    "    assert added_line_count('') == 0\n\n\n"
    "def test_context_and_removed_only_is_zero() -> None:\n"
    "    assert added_line_count('@@ -1,1 +1,1 @@\\n ctx\\n-gone\\n') == 0\n"
)

_CONV_SEVSUM = (
    "from orchestrator.bench_sevsummary import headline_severity\n"
    "from orchestrator.codereview.verifiers import Finding, Severity\n\n\n"
    "def _f(sev: Severity) -> Finding:\n"
    "    return Finding('v', 'r', sev, 'a.py', 1, 'm')\n\n\n"
    "def test_returns_worst() -> None:\n"
    "    assert headline_severity([_f(Severity.NIT), _f(Severity.BLOCKER)]) == 'blocker'\n"
    "    assert headline_severity([_f(Severity.NIT)]) == 'nit'\n\n\n"
    "def test_empty_is_none_label() -> None:\n"
    "    assert headline_severity([]) == 'none'\n"
)

_CONV_FINDINGFMT = (
    "from orchestrator.bench_findingfmt import format_finding\n"
    "from orchestrator.codereview.verifiers import Finding, Severity\n\n\n"
    "def test_formats_finding() -> None:\n"
    "    f = Finding('v', 'r', Severity.BLOCKER, 'a.py', 10, 'boom')\n"
    "    assert format_finding(f) == 'blocker: a.py:10 boom'\n"
)

_CONV_MEDIAN = (
    "from orchestrator.bench_callmedian import median_calls\n"
    "from orchestrator.pkg.stats import FunctionCallFrequency\n\n\n"
    "def _freq(n: int) -> FunctionCallFrequency:\n"
    "    return FunctionCallFrequency('id', 'name', n)\n\n\n"
    "def test_median_of_call_counts() -> None:\n"
    "    assert median_calls([_freq(1), _freq(3), _freq(2)]) == 2\n\n\n"
    "def test_empty_is_zero() -> None:\n"
    "    assert median_calls([]) == 0.0\n"
)

_CONV_USAGELINE = (
    "from orchestrator.bench_usageline import usage_line\n"
    "from orchestrator.core.llm.recording import StageUsage\n\n\n"
    "def test_uses_total_tokens() -> None:\n"
    "    u = StageUsage(stage='codegen', calls=2, prompt_tokens=10, completion_tokens=5)\n"
    "    line = usage_line(u)\n"
    "    assert 'codegen' in line\n"
    "    assert '15' in line  # prompt + completion, via StageUsage.total_tokens\n"
)


# External-repo task set (see scripts/bench_tickets_ontomesh.py). Kept in its own module
# because it targets a codebase Spine did not write: the tickets, and the held-out suites
# that grade them, are specific to ontomesh and must not be mistaken for the G2 set.
def _ontomesh_tickets() -> list[Ticket]:
    from bench_tickets_ontomesh import build

    return list(build(Ticket))


SKILL_TASKSETS: dict[str, list[Ticket]] = {
    # test-strategy: edge-heavy pure functions. A demanding contract (empty, zero,
    # negative, boundary, ordering, non-mutation, error paths) leaves headroom a
    # happy-path-only implementation falls into; the held-out suite is the judge.
    "test-strategy": [
        Ticket(
            key="TS-DURATION-1",
            kind="create",
            held_out_tests={"test_duration.py": _TS_DURATION},
            spec={
                "title": "Parse a compact duration string to seconds",
                "summary": (
                    "Create a new module importable as orchestrator.bench_duration "
                    "(file src/orchestrator/bench_duration.py) exposing "
                    "parse_duration(text: str) -> int. It parses a compact duration "
                    "written with the units h, m, s (e.g. '1h30m15s') into a total "
                    "number of seconds. Any subset of units may appear. Parsing is "
                    "case-insensitive and ignores surrounding whitespace."
                ),
                "technical_notes": (
                    "The empty string parses to 0 seconds. A value with no unit, an "
                    "unknown unit, or a negative number is invalid input and must "
                    "raise ValueError. Pure standard library; full type annotations; "
                    "ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_duration import parse_duration`",
                    "parse_duration('1h30m15s') == 5415",
                    "the empty string returns 0",
                    "malformed input raises ValueError",
                ],
            },
        ),
        Ticket(
            key="TS-INTLIST-1",
            kind="create",
            held_out_tests={"test_intlist.py": _TS_INTLIST},
            spec={
                "title": "Parse a comma-separated list of integers",
                "summary": (
                    "Create a new module importable as orchestrator.bench_intlist "
                    "(file src/orchestrator/bench_intlist.py) exposing "
                    "parse_int_list(text: str) -> list[int]. It splits on commas and "
                    "parses each item as an integer, ignoring whitespace around each "
                    "item. Negative integers are allowed."
                ),
                "technical_notes": (
                    "An empty or whitespace-only string yields the empty list. A single "
                    "trailing comma is tolerated. An empty item between two commas, or "
                    "any item that is not an integer, is invalid and must raise "
                    "ValueError. Pure standard library; full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_intlist import parse_int_list`",
                    "parse_int_list(' 1 , 2 ') == [1, 2]",
                    "the empty string returns []",
                    "a trailing comma is ignored but an internal empty item raises ValueError",
                ],
            },
        ),
        Ticket(
            key="TS-TRUNCATE-1",
            kind="create",
            held_out_tests={"test_truncate.py": _TS_TRUNCATE},
            spec={
                "title": "Truncate a string in the middle to a maximum length",
                "summary": (
                    "Create a new module importable as orchestrator.bench_truncate "
                    "(file src/orchestrator/bench_truncate.py) exposing "
                    "truncate_middle(text: str, max_len: int) -> str. When text is no "
                    "longer than max_len it is returned unchanged; otherwise it is "
                    "shortened to exactly max_len characters by keeping a prefix and a "
                    "suffix joined by a single ellipsis character '…' (which counts "
                    "toward max_len)."
                ),
                "technical_notes": (
                    "When the character budget for prefix+suffix is odd, give the extra "
                    "character to the prefix. max_len of exactly 1 yields just the "
                    "ellipsis. max_len below 1 is invalid and must raise ValueError. "
                    "Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_truncate import truncate_middle`",
                    "a string no longer than max_len is returned unchanged",
                    "a longer string becomes exactly max_len chars containing the ellipsis",
                    "max_len < 1 raises ValueError",
                ],
            },
        ),
        Ticket(
            key="TS-PERCENTILE-1",
            kind="create",
            held_out_tests={"test_percentile.py": _TS_PERCENTILE},
            spec={
                "title": "Percentile of a list of numbers (linear interpolation)",
                "summary": (
                    "Create a new module importable as orchestrator.bench_percentile "
                    "(file src/orchestrator/bench_percentile.py) exposing "
                    "percentile(values: list[float], p: float) -> float. It returns the "
                    "p-th percentile (0 <= p <= 100) using linear interpolation between "
                    "the two closest ranks. The values need not be sorted."
                ),
                "technical_notes": (
                    "p == 0 is the minimum and p == 100 the maximum. The input list "
                    "must not be mutated. An empty list, or p outside [0, 100], is "
                    "invalid and must raise ValueError. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_percentile import percentile`",
                    "percentile([1, 2, 3, 4], 50) == 2.5",
                    "the input list is not mutated",
                    "an empty list or out-of-range p raises ValueError",
                ],
            },
        ),
        Ticket(
            key="TS-INTERVALS-1",
            kind="create",
            held_out_tests={"test_intervals.py": _TS_INTERVALS},
            spec={
                "title": "Merge overlapping integer intervals",
                "summary": (
                    "Create a new module importable as orchestrator.bench_intervals "
                    "(file src/orchestrator/bench_intervals.py) exposing "
                    "merge_intervals(intervals: list[tuple[int, int]]) -> "
                    "list[tuple[int, int]]. It merges overlapping and adjacent closed "
                    "intervals and returns them sorted by start, non-overlapping. "
                    "Adjacent intervals (one's end equals the next's start) merge."
                ),
                "technical_notes": (
                    "The input may be unsorted and must not be mutated. The empty list "
                    "returns the empty list. A tuple whose start exceeds its end is "
                    "invalid and must raise ValueError. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_intervals import merge_intervals`",
                    "merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]",
                    "adjacent intervals merge and the input is not mutated",
                    "a tuple with start > end raises ValueError",
                ],
            },
        ),
        Ticket(
            key="TS-SLUG-1",
            kind="create",
            held_out_tests={"test_slug.py": _TS_SLUG},
            spec={
                "title": "Slugify a string for use in a URL",
                "summary": (
                    "Create a new module importable as orchestrator.bench_slug "
                    "(file src/orchestrator/bench_slug.py) exposing slugify(text: str) "
                    "-> str. It lowercases the text and replaces every run of "
                    "non-alphanumeric ASCII characters with a single hyphen, then "
                    "strips any leading or trailing hyphens."
                ),
                "technical_notes": (
                    "Only ASCII letters and digits are kept; everything else (including "
                    "underscores and punctuation) is a separator. Text that is empty or "
                    "contains no alphanumeric characters slugifies to the empty string. "
                    "Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_slug import slugify`",
                    "slugify('Hello, World!') == 'hello-world'",
                    "runs of separators collapse to one hyphen and edges are stripped",
                    "an all-symbol string slugifies to ''",
                ],
            },
        ),
    ],
    # security-aware-coding: each function handles untrusted input where the naive
    # implementation is unsafe. The held-out suite asserts the safe behavior;
    # semgrep findings on the diff are a supporting signal.
    "security-aware-coding": [
        Ticket(
            key="SEC-SAFEJOIN-1",
            kind="create",
            held_out_tests={"test_safejoin.py": _SEC_SAFEJOIN},
            spec={
                "title": "Join a user path under a base directory safely",
                "summary": (
                    "Create a new module importable as orchestrator.bench_safejoin "
                    "(file src/orchestrator/bench_safejoin.py) exposing "
                    "safe_join(base: str, user_path: str) -> str. It returns the "
                    "normalized path of user_path resolved under base, guaranteed to "
                    "stay inside base."
                ),
                "technical_notes": (
                    "user_path comes from an untrusted caller. A user_path that escapes "
                    "base (via '..' segments) or that is an absolute path must be "
                    "rejected with ValueError. Normalize with the standard library and "
                    "verify containment before returning. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_safejoin import safe_join`",
                    "a normal nested path resolves under base",
                    "a '..' traversal or an absolute user_path raises ValueError",
                ],
            },
        ),
        Ticket(
            key="SEC-HTML-1",
            kind="create",
            held_out_tests={"test_htmlcomment.py": _SEC_HTML},
            spec={
                "title": "Render a user comment as HTML",
                "summary": (
                    "Create a new module importable as orchestrator.bench_htmlcomment "
                    "(file src/orchestrator/bench_htmlcomment.py) exposing "
                    "render_comment(author: str, body: str) -> str. It returns an HTML "
                    "snippet that shows the author in bold followed by the body, e.g. "
                    "'<b>author</b>: body'."
                ),
                "technical_notes": (
                    "author and body are untrusted user input and must not be able to "
                    "inject markup — escape them so any HTML special characters they "
                    "contain render as text. The surrounding <b> structure is not "
                    "escaped. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_htmlcomment import render_comment`",
                    "the author and body text appear in the output",
                    "HTML metacharacters in the inputs are escaped (no injectable markup)",
                ],
            },
        ),
        Ticket(
            key="SEC-MASK-1",
            kind="create",
            held_out_tests={"test_mask.py": _SEC_MASK},
            spec={
                "title": "Redact secrets from a log line",
                "summary": (
                    "Create a new module importable as orchestrator.bench_mask "
                    "(file src/orchestrator/bench_mask.py) exposing "
                    "mask_secrets(text: str) -> str. It replaces secret values in a log "
                    "line with '***', leaving the rest of the text intact."
                ),
                "technical_notes": (
                    "Redact three forms: an AWS access key id (starts with 'AKIA' "
                    "followed by uppercase/digits), a bearer token ('Bearer <token>'), "
                    "and a key=value assignment where the key is 'password' or "
                    "'api_key'. The function is idempotent (masking already-masked text "
                    "changes nothing). Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_mask import mask_secrets`",
                    "an AWS key, a bearer token, and a password= value are each replaced",
                    "text with no secret is returned unchanged and masking is idempotent",
                ],
            },
        ),
        Ticket(
            key="SEC-IDENT-1",
            kind="create",
            held_out_tests={"test_identifier.py": _SEC_IDENT},
            spec={
                "title": "Validate a resource identifier (allow-list)",
                "summary": (
                    "Create a new module importable as orchestrator.bench_identifier "
                    "(file src/orchestrator/bench_identifier.py) exposing "
                    "validate_identifier(name: str) -> str. It returns name unchanged "
                    "if it is a valid identifier, otherwise raises ValueError."
                ),
                "technical_notes": (
                    "name is used to build a query/path, so validate by allow-list, not "
                    "by trying to strip bad characters: permit only ASCII letters, "
                    "digits, underscore and hyphen, with length between 1 and 64 "
                    "inclusive. Anything else is rejected. Full type annotations; "
                    "ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_identifier import validate_identifier`",
                    "a valid identifier is returned unchanged",
                    "empty, over-long, or metacharacter-bearing input raises ValueError",
                ],
            },
        ),
        Ticket(
            key="SEC-REDIRECT-1",
            kind="create",
            held_out_tests={"test_redirect.py": _SEC_REDIRECT},
            spec={
                "title": "Validate a post-login redirect target",
                "summary": (
                    "Create a new module importable as orchestrator.bench_redirect "
                    "(file src/orchestrator/bench_redirect.py) exposing "
                    "safe_redirect(target: str, allowed_hosts: set[str]) -> str. It "
                    "returns target if it is a safe redirect destination, otherwise "
                    "raises ValueError."
                ),
                "technical_notes": (
                    "target is untrusted. Allow a relative path that begins with a "
                    "single '/'. Allow an absolute http(s) URL only if its host is in "
                    "allowed_hosts. Reject protocol-relative targets ('//host'), other "
                    "schemes (e.g. javascript:), and external hosts — these are open "
                    "redirects. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_redirect import safe_redirect`",
                    "a '/'-relative path and an allowed-host absolute URL are accepted",
                    "'//evil.com', other schemes, and external hosts raise ValueError",
                ],
            },
        ),
        Ticket(
            key="SEC-SHELLARG-1",
            kind="create",
            held_out_tests={"test_grepcmd.py": _SEC_SHELLARG},
            spec={
                "title": "Build a grep command from an untrusted pattern",
                "summary": (
                    "Create a new module importable as orchestrator.bench_grepcmd "
                    "(file src/orchestrator/bench_grepcmd.py) exposing "
                    "build_grep_command(pattern: str, path: str) -> list[str]. It "
                    "returns the argument vector for running grep, with path last."
                ),
                "technical_notes": (
                    "pattern is untrusted and may contain shell metacharacters, so "
                    "return an argument list (never a single shell string, never set "
                    "shell=True downstream) and place a '--' before the pattern so it "
                    "can't be read as an option. An empty pattern is invalid and must "
                    "raise ValueError. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_grepcmd import build_grep_command`",
                    "the return value is a list with the pattern as one literal element",
                    "a '--' guard precedes the pattern and an empty pattern raises ValueError",
                ],
            },
        ),
    ],
    # convention-digest: each feature has an existing repo helper that the correct
    # solution reuses instead of reinventing. Independent acceptance = held-out
    # behavior passes; the symbol-reuse grader is the convention signal (did the
    # change import the existing helper, or grow a parallel one?).
    "convention-digest": [
        Ticket(
            key="CONV-DIFFSTAT-1",
            kind="create",
            held_out_tests={"test_diffstat.py": _CONV_DIFFSTAT},
            spec={
                "title": "Count added lines in a unified diff",
                "summary": (
                    "Create a new module importable as orchestrator.bench_diffstat "
                    "(file src/orchestrator/bench_diffstat.py) exposing "
                    "added_line_count(patch: str) -> int — the number of added lines in "
                    "a unified-diff patch (0 for an empty patch)."
                ),
                "technical_notes": (
                    "This repo already parses unified diffs: "
                    "orchestrator.codereview.diff_utils provides an iter_added_lines "
                    "generator that handles hunk headers and +/- prefixes. Build on it "
                    "rather than re-parsing the diff. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_diffstat import added_line_count`",
                    "it returns the count of added (+) lines, reusing the existing diff helper",
                    "an empty patch returns 0",
                ],
            },
        ),
        Ticket(
            key="CONV-SEVSUM-1",
            kind="create",
            held_out_tests={"test_sevsummary.py": _CONV_SEVSUM},
            spec={
                "title": "Headline severity of a set of review findings",
                "summary": (
                    "Create a new module importable as orchestrator.bench_sevsummary "
                    "(file src/orchestrator/bench_sevsummary.py) exposing "
                    "headline_severity(findings) -> str: the value of the most severe "
                    "finding's severity, or 'none' when there are no findings."
                ),
                "technical_notes": (
                    "Use the existing Finding and Severity types and the worst_severity "
                    "helper from orchestrator.codereview.verifiers — do not reimplement "
                    "severity ranking. Return the worst severity's string value. Full "
                    "type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_sevsummary import headline_severity`",
                    "it returns the worst severity's value via the existing helper",
                    "an empty list returns 'none'",
                ],
            },
        ),
        Ticket(
            key="CONV-FINDINGFMT-1",
            kind="create",
            held_out_tests={"test_findingfmt.py": _CONV_FINDINGFMT},
            spec={
                "title": "Format a review finding as a one-line string",
                "summary": (
                    "Create a new module importable as orchestrator.bench_findingfmt "
                    "(file src/orchestrator/bench_findingfmt.py) exposing "
                    "format_finding(finding) -> str rendering a finding as "
                    "'<severity>: <path>:<line> <message>'."
                ),
                "technical_notes": (
                    "Accept the existing Finding dataclass from "
                    "orchestrator.codereview.verifiers — do not define your own finding "
                    "type. Use the severity's string value. Full type annotations; "
                    "ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_findingfmt import format_finding`",
                    "format_finding(Finding(..., BLOCKER, 'a.py', 10, 'boom')) == 'blocker: a.py:10 boom'",
                    "it reuses the existing Finding type rather than redefining it",
                ],
            },
        ),
        Ticket(
            key="CONV-MEDIAN-1",
            kind="create",
            held_out_tests={"test_callmedian.py": _CONV_MEDIAN},
            spec={
                "title": "Median call count over function frequencies",
                "summary": (
                    "Create a new module importable as orchestrator.bench_callmedian "
                    "(file src/orchestrator/bench_callmedian.py) exposing "
                    "median_calls(freqs) -> float: the median of the call_count values "
                    "across a list of function-call frequencies (0.0 for an empty list)."
                ),
                "technical_notes": (
                    "The pkg stats module already computes this: reuse "
                    "median_call_count and the FunctionCallFrequency type from "
                    "orchestrator.pkg.stats instead of reimplementing the median. Full "
                    "type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_callmedian import median_calls`",
                    "it returns the median call_count via the existing stats helper",
                    "an empty list returns 0.0",
                ],
            },
        ),
        Ticket(
            key="CONV-USAGELINE-1",
            kind="create",
            held_out_tests={"test_usageline.py": _CONV_USAGELINE},
            spec={
                "title": "One-line token-usage summary for a stage",
                "summary": (
                    "Create a new module importable as orchestrator.bench_usageline "
                    "(file src/orchestrator/bench_usageline.py) exposing "
                    "usage_line(usage) -> str: a short line naming the stage and its "
                    "total token count, e.g. 'codegen: 15 tokens'."
                ),
                "technical_notes": (
                    "Accept the existing StageUsage dataclass from "
                    "orchestrator.core.llm.recording and use its total_tokens property "
                    "for the count — do not re-add prompt and completion tokens by "
                    "hand. Full type annotations; ruff-clean."
                ),
                "acceptance_criteria": [
                    "importable as `from orchestrator.bench_usageline import usage_line`",
                    "the line names the stage and its total token count",
                    "the total comes from StageUsage.total_tokens (reused, not recomputed)",
                ],
            },
        ),
    ],
}


def taskset(skill_id: str) -> list[Ticket]:
    """The signal-bearing tickets for ``skill_id`` (P1), or empty if unknown.

    ``ontomesh`` is the external-repo set and is loaded lazily: its held-out suites import
    that repository's modules, so it is only meaningful with ``BENCH_REPO`` pointing there.
    """
    if skill_id == "ontomesh":
        return _ontomesh_tickets()
    return SKILL_TASKSETS.get(skill_id, [])


MODEL = os.getenv("SDLC_CODEGEN_MODEL", "claude-sonnet-4-6")
MAX_REFINES = int(os.getenv("BENCH_MAX_REFINES", "3"))
# Production runs each stage under a Temporal retry policy (3 attempts); the
# benchmark mirrors it so one garbage emission doesn't sink a ticket.
STAGE_ATTEMPTS = 3


async def _stage(call: Any, /, **kwargs: Any) -> Any:
    last: Exception | None = None
    for attempt in range(STAGE_ATTEMPTS):
        try:
            return await call(**kwargs)
        except (CodegenError, LLMError) as exc:
            last = exc
            if attempt < STAGE_ATTEMPTS - 1:
                print(f"  stage retry {attempt + 1}/{STAGE_ATTEMPTS - 1}: {str(exc)[:90]}")
    assert last is not None
    raise last


def make_worktree(name: str, repo_root: Path = REPO) -> Path:
    path = Path(tempfile.mkdtemp(prefix=f"codegen-bench-{name}-")) / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(path), "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        check=True,
    )
    return path


def drop_worktree(path: Path, repo_root: Path = REPO) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )


def run_pytest(workdir: Path, test_files: list[str]) -> tuple[bool, str]:
    """Run only the *generated* tests, inside the worktree."""
    if not test_files:
        return False, "no test files were generated"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workdir / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *test_files],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(workdir),
        check=False,
    )
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


def _rel(files: list[str], root: Path) -> list[str]:
    return [str(Path(f).resolve().relative_to(root.resolve())) for f in files]


def _modified_tracked(root: Path) -> list[str]:
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout
    return [line[3:] for line in status.splitlines() if line[:2].strip().startswith("M")]


def _package_roots(root: Path) -> list[str]:
    """Directory prefixes that count as "inside the package", for THIS repo.

    Was hardcoded to `src/orchestrator/`, which is correct for Spine and silently
    unsatisfiable anywhere else. On `synaptixs/ontomesh` — flat modules under `src/` — every
    `create` ticket failed this check in BOTH arms, so grounded and ungrounded looked
    identical and the run read as "grounding has no effect on an external repo". That was
    the harness, not the models.

    Derived from layout: `src/<pkg>/` when the repo nests a package under src, `src/` when
    it keeps modules flat there, plus any top-level package directory.
    """
    roots: list[str] = []
    src = root / "src"
    if src.is_dir():
        roots.extend(f"src/{d.name}/" for d in src.iterdir() if d.is_dir() and (d / "__init__.py").exists())
        # `src/` itself counts whenever the repo also keeps modules flat there. ontomesh has
        # BOTH nested packages and flat modules; listing only the packages would reject a
        # module placed beside `db_introspector.py`, which is where its tickets belong.
        if any(src.glob("*.py")):
            roots.append("src/")
    roots.extend(
        f"{d.name}/"
        for d in root.iterdir()
        if d.is_dir() and (d / "__init__.py").exists() and d.name not in {"tests", "test"}
    )
    return list(dict.fromkeys(roots)) or ["src/"]


def _importable_names(root: Path) -> set[str]:
    """Module names this repo actually defines — the thing a new module must import.

    Replaces a regex for `from orchestrator\\.`, which asserted Spine's own package name.
    Reading the target's real module names means "did it reuse what exists" is answered from
    the repository rather than from an assumption about it.
    """
    names: set[str] = set()
    for base in (root / "src", root):
        if not base.is_dir():
            continue
        for p in base.glob("*.py"):
            if not p.name.startswith("_"):
                names.add(p.stem)
        for d in base.iterdir():
            if d.is_dir() and (d / "__init__.py").exists():
                names.add(d.name)
    return names - {"tests", "test", "setup", "conftest"}


def grade(ticket: Ticket, written: list[str], root: Path) -> tuple[bool, dict[str, bool]]:
    """Objective fit checks. Returns (fit, per-check breakdown)."""
    rel = _rel([f for f in written if Path(f).exists()], root)
    source = "\n".join(Path(f).read_text(encoding="utf-8") for f in written if Path(f).exists())
    modified = _modified_tracked(root)
    new_non_test_modules = [
        p for p in rel if p.endswith(".py") and not Path(p).name.startswith("test_") and p not in modified
    ]

    if ticket.kind == "edit":
        checks = {
            "edits the named target": all(t in modified for t in ticket.must_edit),
            "no parallel module created": not new_non_test_modules,
            "no unrelated file modified": all(
                m in ticket.must_edit or Path(m).name.startswith("test_") or m.startswith("tests/")
                for m in modified
            ),
        }
    else:
        pkg_roots = _package_roots(root)
        known = _importable_names(root)
        imported = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.M))
        # A relative import (`from .x`) is by definition inside the package.
        reuses_repo_code = bool(re.search(r"^\s*from\s+\.", source, re.M)) or any(
            name.split(".")[0] in known for name in imported
        )
        checks = {
            "placed inside the package": any(p.startswith(tuple(pkg_roots)) for p in new_non_test_modules),
            "imports the real model": reuses_repo_code,
            "no tracked file clobbered": not modified,
        }
    return all(checks.values()), checks


# Phase 2b: which design the codegen prompt is conditioned on.
#
#   none          — no design stage at all. What Run 2 measured, and the default, so that
#                   run stays reproducible from this file.
#   deterministic — the skeleton `produce_design` writes with no model.
#   llm           — `_llm_design`, with the fact fields supplied from Evidence and the output
#                   checked by `design_validator` before it is allowed near codegen.
#
# `deterministic` vs `llm` is the comparison the promotion rule asks for. `none` is the third
# arm, and it answers a different question: whether a design stage earns its tokens at all.
DESIGN_ARMS = ("none", "deterministic", "llm")


async def build_design(
    ticket: Ticket,
    llm: RecordingLLMClient,
    *,
    arm: str,
    repo_root: Path,
    model: str | None,
) -> tuple[str, dict[str, Any]]:
    """Return the design text handed to codegen, plus what to record about it.

    A design the validator refuses returns **no text and `rejected: True`**. That is not an
    abort — the model answered, the answer named code that does not exist, and the production
    pipeline parks the run. Counting it as a pass would measure a design nobody would ship;
    counting it as an abort would hide the failure mode this arm exists to expose.
    """
    if arm == "none":
        return "", {"design_arm": arm}

    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc.design import produce_design, render_design_md
    from orchestrator.sdlc.design_validator import validate_design
    from orchestrator.sdlc.evidence import build_evidence

    batch = load_or_extract(repo_root)
    store = FactStore(batch)
    evidence = await build_evidence(
        str(ticket.spec.get("title", "")),
        str(ticket.spec.get("summary", "")),
        store=store,
        root=repo_root,
    )
    design = await produce_design(
        ticket.spec,
        overview=build_overview(batch),
        store=store,
        # The model writes `approach`, `interfaces`, `data_changes` and `test_strategy`; the
        # fact fields come from Evidence and are not the model's to invent.
        llm=llm if arm == "llm" else None,
        root=repo_root,
        blast_radius=evidence.blast_radius,
    )
    validation = validate_design(design, store=store, root=repo_root)
    # `produce_design` catches every exception and returns the deterministic design, so an
    # `llm` arm whose model call failed silently measures the skeleton and reports it as the
    # model's work. That is the "6 tickets never reached the model" accounting error in a new
    # place, and it happened on the very first real call this path made (a JSON parse failure).
    # A fallback is the *absence* of a measurement, and is recorded as one.
    fell_back = arm == "llm" and not design.get("llm")
    if fell_back:
        print("  design:    FELL BACK to the deterministic design — this run measures nothing")
    meta: dict[str, Any] = {
        "design_arm": arm,
        "design_fell_back": fell_back,
        "design_llm": bool(design.get("llm")),
        "design_files": len(design.get("files_to_touch") or []),
        "design_rejected": not validation.ok,
        "design_findings": [f.named for f in validation.findings],
    }
    if not validation.ok:
        print(f"  design:    REJECTED — {', '.join(meta['design_findings'])}")
        return "", meta
    print(f"  design:    {meta['design_files']} file(s), llm={meta['design_llm']}")
    return render_design_md(ticket.spec, design), meta


async def run_ticket(
    ticket: Ticket,
    llm: RecordingLLMClient,
    grounder: PKGCodegenGrounder | None,
    *,
    agentic: bool = False,
    repo_root: Path = REPO,
    model: str | None = None,
    eval_skill: str | None = None,
    baseline: Baseline | None = None,
    design_arm: str = "none",
) -> dict[str, Any]:
    # Skill A/B hook (persona+skill measurement): the candidate skill's guidance is
    # injected into the conditioning seam, which is phase-aware (Skill.phases) — the
    # skill reaches the phase it declares (test-strategy → author_tests/refine;
    # security/convention → implement/refine), so a baseline run vs a skill-on run
    # is a controlled comparison on the same tickets. The A/B runner passes the arm
    # explicitly (model + eval_skill); standalone use falls back to env so
    # `EVAL_SKILL=<id> ... codegen_benchmark.py` still works.
    if eval_skill is None:
        eval_skill = os.getenv("EVAL_SKILL", "").strip()
    design_text, design_meta = "", {"design_arm": design_arm}
    adapter = LLMCodegenAdapter(
        llm,
        model=model or MODEL,
        grounder=grounder,
        agentic=agentic,
        skills=[eval_skill] if eval_skill else None,
    )
    preflight = SubprocessPreflightRunner()
    workdir = make_worktree(ticket.key.lower(), repo_root)
    key = ticket.key
    cost_before = llm.ledger.total().cost_usd
    print(f"\n=== {key} ({ticket.kind}) → {workdir}")
    try:
        with llm.stage(key):
            if design_arm != "none":
                design_text, design_meta = await build_design(
                    ticket, llm, arm=design_arm, repo_root=repo_root, model=model
                )
                adapter = LLMCodegenAdapter(
                    llm,
                    model=model or MODEL,
                    grounder=grounder,
                    agentic=agentic,
                    skills=[eval_skill] if eval_skill else None,
                    design=design_text,
                )
            impl = await _stage(adapter.implement, spec=ticket.spec, path=str(workdir), issue_key=key)
            print(f"  implement: {_rel(impl.files, workdir)} — {impl.summary[:120]}")
            tests = await _stage(adapter.author_tests, spec=ticket.spec, path=str(workdir), issue_key=key)
            print(f"  tests:     {_rel(tests.files, workdir)} — {tests.summary[:120]}")

            written = [*impl.files, *tests.files]
            test_files = [f for f in written if Path(f).name.startswith("test_")]
            passed, out = run_pytest(workdir, test_files)
            pre_ok = False
            refines = 0
            while refines <= MAX_REFINES:
                if passed:
                    pre = await preflight.run(path=str(workdir), baseline=baseline)
                    pre_ok = pre.passed
                    if pre_ok:
                        break
                    out = pre.output
                    print(f"  preflight: FAIL — {out.splitlines()[-1] if out else ''}")
                else:
                    print(f"  pytest:    FAIL — {out.splitlines()[-1] if out else ''}")
                if refines == MAX_REFINES:
                    break
                refines += 1
                print(f"  refine:    ({refines}/{MAX_REFINES}) …")
                fix = await adapter.refine(spec=ticket.spec, path=str(workdir), issue_key=key, failures=out)
                written.extend(fix.files)
                test_files = [f for f in written if Path(f).name.startswith("test_")]
                passed, out = run_pytest(workdir, test_files)

        fit, checks = grade(ticket, written, workdir)
        for label, ok in checks.items():
            print(f"  {label}: {ok}")
        accepted = passed and pre_ok and fit

        # Independent grading (P0): graders the model did NOT author. The held-out
        # suite IS the independent-acceptance headline — the spec's literal
        # definition ("independent acceptance = held-out tests pass"). It's
        # self-contained: a suite that imports and exercises the impl already
        # requires the code to compile and run, so it isn't gated on the
        # self-graded `passed`/`fit`. semgrep + symbol-reuse are supporting
        # per-skill signals. All best-effort — a missing held-out suite or no
        # semgrep degrades to "no signal", never a crash.
        impl_paths = [Path(f) for f in written if not Path(f).name.startswith("test_")]
        held = run_held_out_tests(workdir, ticket.held_out_tests)
        independent_accepted = held.passed if held.ran else None
        findings = semgrep_findings(impl_paths)
        reuse_ok = reused_existing_symbols(read_source(impl_paths))
        if held.ran:
            print(
                f"  held-out:  {'PASS' if held.passed else 'FAIL'}"
                f"{'' if held.passed else ' — ' + (held.output.splitlines()[-1] if held.output else '')}"
            )
        if findings is not None:
            print(f"  semgrep:   {findings} finding(s)")
        print(f"  reuse:     {'yes' if reuse_ok else 'no'}")
        print(
            f"  result:    tests={'PASS' if passed else 'FAIL'} "
            f"preflight={'PASS' if pre_ok else 'FAIL'} fit={'yes' if fit else 'no'} "
            f"→ {'ACCEPTED' if accepted else 'REJECTED'}"
            + (
                f" · independent={'ACCEPTED' if independent_accepted else 'REJECTED'}"
                if independent_accepted is not None
                else ""
            )
        )
        return {
            "ticket": key,
            "kind": ticket.kind,
            "tests_pass": passed,
            "preflight_pass": pre_ok,
            "fit": fit,
            "refines": refines,
            "accepted": accepted,
            "held_out_ran": held.ran,
            "held_out_pass": held.passed if held.ran else None,
            "independent_accepted": independent_accepted,
            "semgrep_findings": findings,
            "reuse_ok": reuse_ok,
            "aborted": False,
            "cost_usd": llm.ledger.total().cost_usd - cost_before,
            **design_meta,
        }
    except (CodegenError, LLMError) as exc:
        # These two are NOT the same event, and conflating them biases the result.
        #
        #   CodegenError — "the model returned something we can't turn into files": a
        #     literal `</content>` tag in generated Python, no `files` list, edits that
        #     will not apply. The model answered; the answer was unusable. That is a
        #     **genuine failure** and must count in the denominator.
        #   LLMError — timeout, auth, a rejected request: no usable response ever arrived.
        #     Nothing about the model's ability was observed, so it is **not measured**.
        #
        # Marking both "aborted" excludes the model's worst outputs from the denominator,
        # which inflates whichever arm fails more. Measured 2026-08-16: every such failure
        # landed in the *ungrounded* arms, so treating them as unmeasured would have
        # flattered the control and understated the grounding effect — the precise claim
        # this benchmark exists to test.
        is_infra = isinstance(exc, LLMError) and not isinstance(exc, CodegenError)
        print(f"  {'ABORTED (not measured)' if is_infra else 'FAILED (model output unusable)'}: {exc}")
        return {
            "ticket": key,
            "kind": ticket.kind,
            "tests_pass": False,
            "preflight_pass": False,
            "fit": False,
            "refines": 0,
            "accepted": False,
            "held_out_ran": bool(ticket.held_out_tests),
            "held_out_pass": False if ticket.held_out_tests else None,
            "independent_accepted": False if ticket.held_out_tests else None,
            "semgrep_findings": None,
            "reuse_ok": False,
            # An infrastructure abort is *not a score of zero* — it is the absence of a
            # measurement. Two runs on 2026-08-15 reported 4/10 and 3/10 that were really
            # "6-8 of 10 tickets never reached the model", indistinguishable from real
            # results. A model that answered unusably is the opposite: a real zero.
            "aborted": is_infra,
            "cost_usd": llm.ledger.total().cost_usd - cost_before,
            **design_meta,
        }
    finally:
        drop_worktree(workdir, repo_root)


async def main() -> None:
    load_local_env(str(REPO / ".env"))
    # EVAL_TASKSET=<skill-id> swaps in that skill's signal-bearing task set (P1)
    # instead of the stock G2 tickets; pair with EVAL_SKILL=<skill-id> for the
    # treatment arm (leave EVAL_SKILL unset for the baseline arm).
    taskset_id = os.getenv("EVAL_TASKSET", "").strip()
    if taskset_id:
        tickets = taskset(taskset_id)
        if not tickets:
            raise SystemExit(f"unknown EVAL_TASKSET {taskset_id!r}; choose one of {sorted(SKILL_TASKSETS)}")
    elif os.getenv("BENCH_ALL"):
        # BENCH_ALL=1 runs every ticket in the file — the 10 G2 tickets plus all three
        # skill task sets — for **27 tickets instead of 10**.
        #
        # Sample size was the second reliability gap: at n=10 a single ticket moves the
        # headline by 10 points, and the 95% interval on a 10-ticket arm is far too wide to
        # separate anything. The cheapest honest fix is not to author 40 more tickets, it is
        # to stop ignoring the 17 that already exist — the `TS-*`, `SEC-*` and `CONV-*` sets
        # were built for skill measurement and **already carry held-out suites**, so they
        # arrive with the objective grading the G2 set only just gained.
        #
        # They are not a like-for-like extension: they were written to leave headroom for a
        # specific skill, so absolute rates across the full set are not comparable to a
        # G2-only run. Use BENCH_ALL for grounded-vs-ungrounded contrasts (both arms see the
        # same tickets, so the contrast holds) and quote G2-only for the headline rate.
        seen: set[str] = set()
        tickets = []
        for t in [*TICKETS, *(t for ts in SKILL_TASKSETS.values() for t in ts)]:
            if t.key not in seen:
                seen.add(t.key)
                tickets.append(t)
    else:
        tickets = TICKETS
    selected = os.getenv("BENCH_TICKETS")
    if selected:
        keys = {k.strip() for k in selected.split(",") if k.strip()}
        tickets = [t for t in tickets if t.key in keys]
    arm = f"treatment (EVAL_SKILL={os.getenv('EVAL_SKILL')})" if os.getenv("EVAL_SKILL") else "baseline"
    llm = RecordingLLMClient(LiteLLMClient(request_timeout_seconds=300.0))
    if taskset_id:
        print(f"task set: {taskset_id} · arm: {arm}")
    print(f"model: {MODEL} · refine cap: {MAX_REFINES} · tickets: {[t.key for t in tickets]}")
    # BENCH_NO_GROUNDING=1 runs the ungrounded control arm: identical tickets, model and
    # refine cap, with the PKG context withheld. `LLMCodegenAdapter` already accepts
    # `grounder=None`, so this withholds context without changing the codegen path.
    ungrounded = bool(os.getenv("BENCH_NO_GROUNDING"))

    # BENCH_DESIGN selects which design conditions the codegen prompt — see DESIGN_ARMS.
    # Defaults to "none", which is what Run 2 measured, so this file still reproduces it.
    design_arm = (os.getenv("BENCH_DESIGN") or "none").strip().lower()
    if design_arm not in DESIGN_ARMS:
        raise SystemExit(f"BENCH_DESIGN={design_arm!r} is not one of {DESIGN_ARMS}")

    # BENCH_REPO points the whole run at a DIFFERENT repository. Every ticket already
    # threads `repo_root` (worktree, preflight, grounder), so only this binding was
    # missing.
    #
    # This exists because the ten stock tickets are Spine-authored, against Spine's own
    # code, with the grounding tuned here — a home-field advantage that scaling the ticket
    # count does nothing to remove. An external repo is the only thing that removes it, and
    # a number measured on one is worth more than a larger number measured on ours.
    #
    # The tickets must suit the repo: pointing BENCH_REPO at flask while running tickets
    # that patch `orchestrator.pkg.stats` measures nothing. Pair it with EVAL_TASKSET (or
    # BENCH_TICKETS) naming a task set authored for that repo.
    bench_repo = Path(os.getenv("BENCH_REPO", "")).expanduser().resolve() if os.getenv("BENCH_REPO") else REPO
    if bench_repo != REPO:
        if not (bench_repo / ".git").exists():
            raise SystemExit(f"BENCH_REPO={bench_repo} is not a git repository (worktrees are required)")
        print(f"target repo: {bench_repo}  [EXTERNAL — tickets must be authored for it]")

    # Preflight baseline: what the target repo's own tools already report, captured once.
    #
    # Without it the gate asks "is this repository clean?" instead of "is this change
    # clean?" — and on any codebase carrying a lint/type backlog every ticket fails before
    # the model contributes a line. Measured on `synaptixs/ontomesh`: 3,378 pre-existing
    # findings, which would have produced 0/50 in *both* arms and a run that cost money and
    # measured nothing.
    #
    # A failure here stops the run. A missing baseline does not weaken the gate, it makes
    # every downstream number meaningless — and a silent pass reads exactly like a result.
    try:
        baseline = await SubprocessPreflightRunner().capture_baseline(path=str(bench_repo))
    except PreflightBaselineError as exc:
        raise SystemExit(
            f"\n*** STOPPING — preflight baseline could not be captured for {bench_repo}:\n"
            f"    {exc}\n"
            "    Without a baseline, `mergeable` cannot distinguish the model's errors from\n"
            "    the repo's own. This is not a result. Fix the cause and re-run."
        ) from exc
    print(f"preflight baseline: {baseline.describe()}")
    if baseline.skipped:
        print(
            f"  !! gate is WEAKER here — {', '.join(baseline.skipped)} could not run in this repo.\n"
            "     `mergeable` below means the remaining checks only; do not compare it to a\n"
            "     run where every tool participated."
        )

    print("\ngrounding: OFF (BENCH_NO_GROUNDING)" if ungrounded else "\nbuilding PKG …")
    grounder = None if ungrounded else PKGCodegenGrounder.from_repo(bench_repo)

    print(f"design arm: {design_arm}")
    results = [
        await run_ticket(t, llm, grounder, repo_root=bench_repo, baseline=baseline, design_arm=design_arm)
        for t in tickets
    ]

    print("\n=== G2 acceptance summary ===")
    print(
        f"  {'ticket':<18} {'kind':<7} {'tests':<6} {'prefl':<6} {'fit':<4} {'ref':<4} {'cost':<8} accepted"
    )
    for r in results:
        print(
            f"  {r['ticket']:<18} {r['kind']:<7} "
            f"{'PASS' if r['tests_pass'] else 'FAIL':<6} "
            f"{'PASS' if r['preflight_pass'] else 'FAIL':<6} "
            f"{'yes' if r['fit'] else 'no':<4} {r['refines']:<4} "
            f"${r['cost_usd']:<7.2f} {'YES' if r['accepted'] else 'NO'}"
        )
    for kind in ("create", "edit"):
        rows = [r for r in results if r["kind"] == kind]
        if rows:
            ok = sum(1 for r in rows if r["accepted"])
            print(f"\n  {kind} acceptance: {ok}/{len(rows)}")
    total_ok = sum(1 for r in results if r["accepted"])
    total = llm.ledger.total()
    print(f"  overall acceptance: {total_ok}/{len(results)}")
    # Independent acceptance — the headline for the skill A/B — reported only over
    # tickets that shipped a held-out suite (else it's not a real signal).
    graded = [r for r in results if r.get("independent_accepted") is not None]
    if graded:
        ind_ok = sum(1 for r in graded if r["independent_accepted"])
        print(f"  independent (held-out) acceptance: {ind_ok}/{len(graded)}")
    scanned = [r for r in results if r.get("semgrep_findings") is not None]
    if scanned:
        print(f"  total semgrep findings: {sum(r['semgrep_findings'] for r in scanned)}")
    print(f"  reuse-of-existing-symbols: {sum(1 for r in results if r.get('reuse_ok'))}/{len(results)}")
    print(f"  total cost: ${total.cost_usd:.2f} · {total.calls} calls · {total.total_tokens} tokens")

    # --- Provenance: what actually produced these numbers -------------------------
    # A benchmark figure without this block is not reproducible. Each line closes a
    # specific way an earlier run could have been quoted misleadingly.

    # (a) The model that ANSWERED, not the alias requested. `claude-opus-5` and
    #     `gpt-5.6-sol` are moving aliases; the dated snapshot behind them changes.
    #     Caveat, verified: both providers echo the alias rather than a dated snapshot, so
    #     this catches substitution/fallback but does NOT pin weights. Quote the run date.
    served = ", ".join(sorted({m for st in llm.ledger.ordered() for m in st.models})) or "(none)"
    print(f"  served model(s): {served}  [alias, not a pinned snapshot]")

    # (b) What `mergeable` MEANT here. Acceptance folds in this repo's own preflight
    #     config, so the bar is not portable: the same patch can be mergeable in one
    #     repo and not another. Cross-repo comparison needs this stated, not assumed.
    gate_tools = ", ".join(t for t, _ in _JSON_CHECKS if t not in baseline.skipped)
    print(f"  acceptance gate: tests + preflight ({gate_tools} @ {bench_repo.name}) + fit")
    print(f"  preflight baseline: {baseline.describe()}  [only NEW findings fail a ticket]")

    # (c) Temperature is NOT pinned, and cannot be. `claude-opus-5` accepts only its own
    #     fixed value and rejects every other (see litellm_client's retry path), so a
    #     pinned temperature is impossible across the model set. Run-to-run variance is
    #     therefore irreducible here — a 3-ticket swing between identical passes was
    #     observed on 2026-08-15 — and the honest response is to report variance across
    #     repeated passes rather than to claim determinism the models will not give.
    print("  temperature: provider default (not pinned — opus-5 rejects a pinned value)")

    # Aborts are reported last, loudly, and as a non-zero exit — an aborted ticket is a
    # missing measurement, not a failed one, and an arm containing any is not a result.
    aborted = [r["ticket"] for r in results if r.get("aborted")]
    print(f"  aborted (NOT measured): {len(aborted)}/{len(results)}")
    if aborted:
        print(
            f"\n  *** THIS ARM IS NOT A RESULT — {len(aborted)} of {len(results)} tickets never "
            f"reached the model: {', '.join(aborted)}.\n"
            "      The acceptance figures above are 'how many tickets avoided the failure',\n"
            "      not model performance. Fix the cause and re-run before quoting anything."
        )
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
