"""The digest a yes is bound to (SSPN-55, B12).

"The exact refusal" is only a promise if the same refusal always hashes the same, in any
process, and a different one never does.
"""

from __future__ import annotations

import subprocess
import sys

from orchestrator.sdlc.validity import Assessment, Finding, Verdict, refusal_digest


def _refusal(*, detail: str = "13 acceptance criteria — more than one change's worth") -> Assessment:
    return Assessment(
        verdict=Verdict.TOO_BIG,
        findings=[
            Finding(check="size", detail=detail, evidence="cap is 12; split the ticket"),
            Finding(check="context-budget", detail="a.py is 400 KB", evidence="budget is 300 KB"),
        ],
    )


def test_the_same_refusal_gives_the_same_digest() -> None:
    assert refusal_digest(_refusal()) == refusal_digest(_refusal())


def test_the_order_the_findings_were_found_in_does_not_matter() -> None:
    forward = _refusal()
    backward = Assessment(verdict=forward.verdict, findings=list(reversed(forward.findings)))

    assert refusal_digest(forward) == refusal_digest(backward)


def test_a_different_verdict_or_finding_gives_a_different_digest() -> None:
    base = refusal_digest(_refusal())

    assert refusal_digest(_refusal(detail="14 acceptance criteria — more than one change's worth")) != base
    assert refusal_digest(Assessment(verdict=Verdict.CRITERIA_WRONG, findings=_refusal().findings)) != base


def test_the_digest_is_the_same_in_another_process() -> None:
    """Python randomises string hashing per process; a digest built over a dict or set order
    would pass in-process and drift between the park and the resume."""
    code = (
        "from orchestrator.sdlc.validity import Assessment, Finding, Verdict, refusal_digest;"
        "print(refusal_digest(Assessment(verdict=Verdict.TOO_BIG, findings=["
        "Finding(check='size', detail=\"13 acceptance criteria — more than one change's worth\","
        " evidence='cap is 12; split the ticket'),"
        "Finding(check='context-budget', detail='a.py is 400 KB', evidence='budget is 300 KB')])))"
    )
    seen = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={"PYTHONHASHSEED": seed, "PATH": ""},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2", "3")
    }

    assert seen == {refusal_digest(_refusal())}
