from __future__ import annotations

from orchestrator.runtime.verifiers import (
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
    combine_results,
)


def _r(outcome: VerifierOutcome, *, n_failures: int = 0, elapsed: float = 1.0) -> VerifierResult:
    failures = tuple(
        VerifierFailure(verifier_id="v", rule="r", field="f", message=f"m{i}", severity=outcome)
        for i in range(n_failures)
    )
    return VerifierResult(verifier_id="v", outcome=outcome, failures=failures, elapsed_ms=elapsed)


def test_combine_empty_returns_pass() -> None:
    result = combine_results([])
    assert result.outcome is VerifierOutcome.PASS
    assert result.failures == ()


def test_combine_takes_worst_outcome_fail_wins() -> None:
    result = combine_results([_r(VerifierOutcome.PASS), _r(VerifierOutcome.WARN), _r(VerifierOutcome.FAIL)])
    assert result.outcome is VerifierOutcome.FAIL


def test_combine_warn_wins_over_pass() -> None:
    assert (
        combine_results([_r(VerifierOutcome.PASS), _r(VerifierOutcome.WARN)]).outcome is VerifierOutcome.WARN
    )


def test_combine_concatenates_failures_and_elapsed() -> None:
    result = combine_results(
        [
            _r(VerifierOutcome.WARN, n_failures=2, elapsed=1.5),
            _r(VerifierOutcome.FAIL, n_failures=1, elapsed=2.5),
        ]
    )
    assert len(result.failures) == 3
    assert result.elapsed_ms == 4.0


def test_to_state_value_round_trip() -> None:
    r = _r(VerifierOutcome.FAIL, n_failures=1)
    payload = r.to_state_value()
    assert payload["outcome"] == "fail"
    assert payload["failures"][0]["rule"] == "r"
