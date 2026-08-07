"""The validity gate refuses a criterion that contradicts a documented repo invariant (SSPN-31).

Every case goes through ``assess()`` — the entry point the run agent calls — so the wiring
between the new ``_check_invariants`` check and the verdict is exercised, not just the helper.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import FactBatch, FactStore
from orchestrator.sdlc.validity import Verdict, assess


def _store() -> FactStore:
    """An empty graph: these cases are about the criteria text, not what the repo contains."""
    return FactStore(FactBatch())


def _spec(*criteria: str, proposed: list[Any] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"acceptance_criteria": list(criteria)}
    if proposed is not None:
        spec["proposed_criteria"] = proposed
    return spec


# The criterion that started the ticket, VERBATIM — phrased entirely as output shape, so it
# names the JSON key "regressions" and never the `regression` command. This is the case the
# check must refuse; the reworded one below is kept as an extra, never as the only case.
_GENERATED_AT = (
    "Given `--format json` is passed, the JSON output is a top-level object with two keys: "
    '"meta" (object containing at minimum "generated_at" as an ISO-8601 UTC timestamp string) '
    'and "regressions" (array of objects)'
)

# The same requirement reworded to name the command — the fixture the check used to be tested
# against. Kept so both phrasings stay covered.
_GENERATED_AT_NAMING_THE_COMMAND = (
    "`orchestrator regression --format json` includes `meta.generated_at` as an "
    "ISO-8601 UTC timestamp in its JSON output"
)


def test_generated_at_criterion_is_refused_as_criteria_wrong() -> None:
    result = assess(_spec(_GENERATED_AT), store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert result.proceed is False
    assert [f.check for f in result.findings] == ["repo-invariant"]


def test_the_reworded_criterion_naming_the_command_is_refused_too() -> None:
    result = assess(_spec(_GENERATED_AT_NAMING_THE_COMMAND), store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert [f.check for f in result.findings] == ["repo-invariant"]


@pytest.mark.parametrize(
    "criterion",
    [
        'the JSON output lists the "regressions" array with a generated_at timestamp',
        "the regression's JSON report carries a generated_at timestamp",
        "the regressions' report payload includes the current time",
    ],
)
def test_surface_names_match_plural_and_possessive_inflections(criterion: str) -> None:
    result = assess(_spec(criterion), store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert result.findings[0].check == "repo-invariant"


def test_a_nondeterminism_word_alone_still_proceeds() -> None:
    """The two-signal design stays: 'timestamp' with no named surface is not a breach."""
    result = assess(_spec("the JSON output includes a generated_at timestamp"), store=_store())

    assert result.verdict is Verdict.PROCEED
    assert result.findings == []


def test_finding_evidence_names_the_invariant_it_breaks() -> None:
    result = assess(_spec(_GENERATED_AT_NAMING_THE_COMMAND), store=_store())

    evidence = result.findings[0].evidence
    assert "CLAUDE.md invariant 2" in evidence
    assert "deterministic" in evidence
    # The offending criterion travels with the evidence, so the reader sees what was refused.
    assert "meta.generated_at" in evidence
    assert "regression" in result.findings[0].detail


@pytest.mark.parametrize(
    "criterion",
    [
        "the `understand` command writes a `generated_at` timestamp into its JSON output",
        "`orchestrator state` output includes the current time in its report",
        "the episteme knowledge base markdown files contain a generated_at timestamp",
        "the comprehension report payload includes a random uuid4 per run",
    ],
)
def test_deterministic_surfaces_reject_clock_and_randomness(criterion: str) -> None:
    result = assess(_spec(criterion), store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert result.findings[0].check == "repo-invariant"


@pytest.mark.parametrize(
    "criterion",
    [
        "the `understand` run emits a log line with a timestamp for each file scanned",
        "the HTTP response header carries a generated_at timestamp",
        "the Jira issue comment includes an ISO-8601 timestamp of the run",
        "the audit log row records created_at for every state change",
    ],
)
def test_non_deterministic_sinks_are_not_flagged(criterion: str) -> None:
    result = assess(_spec(criterion), store=_store())

    assert result.verdict is Verdict.PROCEED
    assert result.findings == []


def test_timestamp_without_a_deterministic_surface_is_fine() -> None:
    result = assess(
        _spec("the PR body includes an ISO-8601 timestamp of when the run finished"),
        store=_store(),
    )

    assert result.verdict is Verdict.PROCEED


def test_proposed_criteria_are_checked_too() -> None:
    spec = _spec(
        "`orchestrator understand` prints a module count",
        proposed=[{"criterion": _GENERATED_AT, "why": "nice for cache busting"}],
    )

    result = assess(spec, store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert "generated_at" in result.findings[0].evidence


def test_proposed_criteria_may_be_plain_strings() -> None:
    spec = _spec("something harmless", proposed=[_GENERATED_AT])

    result = assess(spec, store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG


def test_clean_spec_still_proceeds_with_no_findings() -> None:
    spec = _spec(
        "`orchestrator understand` output is byte-identical across two runs on the same commit",
        "the state file records the module count",
        proposed=["the episteme index lists every table"],
    )

    result = assess(spec, store=_store())

    assert result.verdict is Verdict.PROCEED
    assert result.findings == []
    assert result.proceed is True


def test_check_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic string matching: no LLM, no socket. Opening one fails the test."""

    def _no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the validity gate must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    result = assess(_spec(_GENERATED_AT), store=_store())

    assert result.verdict is Verdict.CRITERIA_WRONG


def test_verdict_is_stable_across_repeated_calls() -> None:
    spec = _spec(_GENERATED_AT)
    first = assess(spec, store=_store())
    second = assess(spec, store=_store())

    assert first.verdict is second.verdict
    assert [(f.check, f.detail, f.evidence) for f in first.findings] == [
        (f.check, f.detail, f.evidence) for f in second.findings
    ]


def test_render_shows_the_refusal_and_its_evidence() -> None:
    rendered = assess(_spec(_GENERATED_AT), store=_store()).render()

    assert "**Verdict:** CRITERIA_WRONG" in rendered
    assert "repo-invariant" in rendered
    assert "CLAUDE.md invariant 2" in rendered


def test_invariant_check_runs_before_the_size_gate() -> None:
    # Many criteria *and* an invariant breach: the invariant is the one that must be reported,
    # because building to it is wrong however small the ticket is.
    spec = _spec(*[f"criterion {i}" for i in range(20)], _GENERATED_AT)

    result = assess(spec, store=_store(), max_criteria=2)

    assert result.verdict is Verdict.CRITERIA_WRONG
    assert result.findings[0].check == "repo-invariant"


# --- a spec can be right-sized and still not fit in front of the model (SSPN-43) -----
#
# `_check_size` counts criteria and modules; neither correlates with bytes. A spec naming
# six files totalling 113 KB passed PROCEED against a 40 KB budget, every file was
# excerpted, and codegen quoted an edit anchor from text it had never seen. Three runs were
# spent finding that out.


def _repo(tmp_path: Path, **sizes: int) -> Path:
    for name, size in sizes.items():
        (tmp_path / f"{name}.py").write_text("# " + "x" * max(size - 3, 1) + "\n", encoding="utf-8")
    return tmp_path


def test_a_spec_far_over_the_budget_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path, big=90_000)

    result = assess(
        _spec("exports a CSV"), store=_store(), landing=["big.py"], root=root, context_budget=40_000
    )

    assert result.verdict is Verdict.TOO_BIG
    assert result.findings[0].check == "context-budget"
    assert "2.2x over" in result.findings[0].detail


def test_a_spec_just_over_the_budget_warns_but_proceeds(tmp_path: Path) -> None:
    """Anchor-located excerpting copes at this margin; refusing would block working runs."""
    root = _repo(tmp_path, med=48_000)

    result = assess(
        _spec("exports a CSV"), store=_store(), landing=["med.py"], root=root, context_budget=40_000
    )

    assert result.verdict is Verdict.PROCEED
    assert [f.check for f in result.findings] == ["context-budget"]
    assert "excerpted" in result.findings[0].detail


def test_a_spec_inside_the_budget_says_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path, small=1_000)

    result = assess(
        _spec("exports a CSV"), store=_store(), landing=["small.py"], root=root, context_budget=40_000
    )

    assert result.verdict is Verdict.PROCEED
    assert result.findings == []


def test_the_check_is_inert_without_a_root() -> None:
    """A caller that cannot measure gets exactly the verdicts it got before."""
    result = assess(_spec("exports a CSV"), store=_store(), landing=["anything.py"])

    assert result.verdict is Verdict.PROCEED
    assert result.findings == []


def test_an_unreadable_file_does_not_block_a_run(tmp_path: Path) -> None:
    result = assess(
        _spec("exports a CSV"),
        store=_store(),
        landing=["does-not-exist.py"],
        root=tmp_path,
        context_budget=40_000,
    )

    assert result.verdict is Verdict.PROCEED
