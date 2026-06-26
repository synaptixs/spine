"""Codebase-auditor persona — deterministic via a scripted Mock LLM."""

from __future__ import annotations

from pathlib import Path

from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall
from orchestrator.personas import Finding, render_findings_markdown, run_audit

_MODULE = """\
def risky(x):
    return eval(x)
"""


def _call(name: str, args: dict[str, object]) -> CompletionResult:
    return CompletionResult(
        text="",
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=(ToolCall("c", name, args),),
    )


def _findings_call(findings: list[dict[str, object]], summary: str = "done") -> CompletionResult:
    return _call("submit_findings", {"summary": summary, "findings": findings})


async def test_audit_reads_then_submits_resolved_findings(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(_MODULE, encoding="utf-8")
    script = [
        _call("read_file", {"path": "mod.py"}),  # the auditor investigates first
        _findings_call(
            [
                {
                    "title": "Use of eval",
                    "file": "mod.py",
                    "line": 2,
                    "severity": "blocker",
                    "detail": "eval on input",
                }
            ]
        ),
    ]
    result = await run_audit(tmp_path, llm=MockLLMClient(script=script), model="m")
    assert result.stopped_reason == "submitted"
    assert len(result.findings) == 1
    assert result.findings[0].title == "Use of eval" and result.findings[0].severity == "blocker"
    assert not result.unresolved


async def test_unresolved_findings_are_dropped(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(_MODULE, encoding="utf-8")
    script = [
        _findings_call(
            [
                {"title": "real", "file": "mod.py", "line": 1, "severity": "note"},
                {"title": "ghost file", "file": "does_not_exist.py", "line": 1, "severity": "warning"},
                {"title": "ghost line", "file": "mod.py", "line": 9999, "severity": "warning"},
            ]
        )
    ]
    result = await run_audit(tmp_path, llm=MockLLMClient(script=script), model="m")
    assert [f.title for f in result.findings] == ["real"]
    assert {f.title for f in result.unresolved} == {"ghost file", "ghost line"}


def test_finding_resolution(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    assert Finding("t", "a.py", 2, "note").resolves(tmp_path) is True
    assert Finding("t", "a.py", 3, "note").resolves(tmp_path) is False  # past EOF
    assert Finding("t", "missing.py", 1, "note").resolves(tmp_path) is False
    assert Finding("t", "../escape.py", 1, "note").resolves(tmp_path) is False  # outside root


async def test_render_groups_by_severity(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    script = [
        _findings_call(
            [
                {"title": "B1", "file": "m.py", "line": 1, "severity": "blocker"},
                {"title": "N1", "file": "m.py", "line": 2, "severity": "note"},
            ],
            summary="two findings",
        )
    ]
    result = await run_audit(tmp_path, llm=MockLLMClient(script=script), model="m")
    md = render_findings_markdown(result, title="Audit X")
    assert "# Audit X" in md and "two findings" in md
    assert "## Blocker (1)" in md and "## Note (1)" in md
    assert md.index("## Blocker") < md.index("## Note")  # severity order


async def test_no_findings_renders_cleanly(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    result = await run_audit(
        tmp_path, llm=MockLLMClient(script=[_findings_call([], summary="clean")]), model="m"
    )
    assert "No findings." in render_findings_markdown(result)
