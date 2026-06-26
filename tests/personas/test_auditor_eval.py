"""Auditor eval scoring + arm (Bet 4c) — deterministic, no network."""

from __future__ import annotations

from pathlib import Path

from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall
from orchestrator.evals import EvalTask, run_eval
from orchestrator.personas import ExpectedFinding, Finding, make_audit_arm, score_findings


def test_score_findings_matches_by_file_and_nearby_line() -> None:
    findings = [Finding("inj", "svc.py", 3, "blocker"), Finding("noise", "other.py", 99, "note")]
    expected = [ExpectedFinding("svc.py", 2, "command injection")]  # line 2 vs found 3 → within tolerance
    score = score_findings(findings, expected, line_tolerance=3)
    assert score.accepted is True
    assert len(score.matched) == 1 and not score.missed
    assert score.spurious == 1  # the unrelated "noise" finding


def test_score_findings_misses_when_not_caught() -> None:
    score = score_findings([Finding("x", "a.py", 1, "note")], [ExpectedFinding("b.py", 1)])
    assert score.accepted is False and len(score.missed) == 1


def test_score_findings_line_out_of_tolerance_misses() -> None:
    score = score_findings([Finding("x", "a.py", 50, "note")], [ExpectedFinding("a.py", 1)], line_tolerance=3)
    assert score.accepted is False


def _submit(findings: list[dict[str, object]]) -> CompletionResult:
    return CompletionResult(
        text="",
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=(ToolCall("c", "submit_findings", {"summary": "s", "findings": findings}),),
    )


async def test_audit_arm_scores_a_seeded_repo(tmp_path: Path) -> None:
    (tmp_path / "svc.py").write_text(
        "import subprocess\nx = subprocess.run('echo', shell=True)\n", encoding="utf-8"
    )
    # Auditor (scripted) submits a finding at the seeded line.
    llm = MockLLMClient(
        script=[_submit([{"title": "inj", "file": "svc.py", "line": 2, "severity": "blocker"}])]
    )
    task = EvalTask(
        id="svc",
        category="security",
        payload={"root": str(tmp_path), "focus": "security", "expected": [{"file": "svc.py", "line": 2}]},
    )
    card = await run_eval([task], make_audit_arm(llm, model="m"), arm_name="auditor", repeats=1)
    m = card.metrics()
    assert m["acceptance_rate"] == 1.0  # the seeded issue was caught


async def test_audit_arm_rejects_when_seeded_issue_missed(tmp_path: Path) -> None:
    (tmp_path / "svc.py").write_text("x = 1\n", encoding="utf-8")
    llm = MockLLMClient(script=[_submit([])])  # auditor finds nothing
    task = EvalTask(
        id="svc",
        category="security",
        payload={"root": str(tmp_path), "focus": "security", "expected": [{"file": "svc.py", "line": 1}]},
    )
    card = await run_eval([task], make_audit_arm(llm, model="m"), arm_name="auditor", repeats=1)
    assert card.metrics()["acceptance_rate"] == 0.0
    assert card.metrics()["failure_modes"] == {"missed": 1}
