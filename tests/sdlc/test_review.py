"""SemanticReviewAdapter: the acceptance-criteria LLM judge (Track 3.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.review import SemanticReviewAdapter, StubReviewAdapter

SPEC: dict[str, Any] = {
    "title": "CSV export",
    "acceptance_criteria": ["exports a CSV file", "handles empty input"],
}


class _JudgeLLM:
    """Returns a scripted judge payload; records the prompt."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.last_user = ""

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: object | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: object = None,
    ) -> CompletionResult:
        self.last_user = messages[-1].content
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return CompletionResult(text, model, 1, 1, 0.0, 1.0)


def _worktree(tmp_path: Path) -> Path:
    (tmp_path / "export.py").write_text("def export_csv(rows):\n    return 'a,b'\n", encoding="utf-8")
    return tmp_path


def _judge(payload: Any) -> tuple[SemanticReviewAdapter, _JudgeLLM]:
    llm = _JudgeLLM(payload)
    return SemanticReviewAdapter(llm), llm


async def test_all_met_approves(tmp_path: Path) -> None:
    adapter, llm = _judge(
        {
            "criteria": [
                {"criterion": "exports a CSV file", "status": "met", "evidence": "export.py:export_csv"},
                {"criterion": "handles empty input", "status": "met", "evidence": "export.py"},
            ],
            "summary": "both criteria satisfied",
        }
    )
    result = await adapter.review(path=str(_worktree(tmp_path)), issue_key="E-1", spec=SPEC)
    assert result.verdict == "approve" and not result.has_blocker
    # the judge saw the criteria and the source
    assert "exports a CSV file" in llm.last_user and "export_csv" in llm.last_user


async def test_unmet_criterion_blocks(tmp_path: Path) -> None:
    adapter, _ = _judge(
        {
            "criteria": [
                {"criterion": "exports a CSV file", "status": "met", "evidence": "ok"},
                {"criterion": "handles empty input", "status": "unmet", "evidence": "no guard"},
            ],
            "summary": "missing empty-input handling",
        }
    )
    result = await adapter.review(path=str(_worktree(tmp_path)), issue_key="E-1", spec=SPEC)
    assert result.verdict == "request_changes" and result.has_blocker
    assert "handles empty input" in result.blockers[0]


async def test_uncertain_is_comment_not_block(tmp_path: Path) -> None:
    adapter, _ = _judge(
        {
            "criteria": [{"criterion": "handles empty input", "status": "uncertain", "evidence": "?"}],
            "summary": "cannot tell",
        }
    )
    result = await adapter.review(path=str(_worktree(tmp_path)), issue_key="E-1", spec=SPEC)
    assert result.verdict == "comment" and not result.has_blocker
    assert "uncertain" in result.summary


async def test_no_criteria_skips_without_approving(tmp_path: Path) -> None:
    adapter, _ = _judge({"criteria": []})
    result = await adapter.review(path=str(_worktree(tmp_path)), issue_key="E-1", spec={"title": "x"})
    assert result.verdict == "comment" and "no acceptance criteria" in result.summary


async def test_empty_worktree_blocks(tmp_path: Path) -> None:
    adapter, _ = _judge({"criteria": [{"criterion": "x", "status": "met"}]})
    result = await adapter.review(path=str(tmp_path), issue_key="E-1", spec=SPEC)
    assert result.verdict == "request_changes" and result.has_blocker


async def test_unparseable_judge_output_cannot_approve(tmp_path: Path) -> None:
    adapter, _ = _judge("the change looks great to me!")
    result = await adapter.review(path=str(_worktree(tmp_path)), issue_key="E-1", spec=SPEC)
    assert result.verdict == "comment" and "unparseable" in result.summary


async def test_stub_accepts_spec_kwarg(tmp_path: Path) -> None:
    result = await StubReviewAdapter().review(path=str(tmp_path), issue_key="E-1", spec=SPEC)
    assert result.verdict == "comment" and not result.has_blocker


async def test_new_untracked_directory_source_is_seen(tmp_path: Path) -> None:
    """Run #20's bug: git collapses a brand-new untracked directory to one
    ``path/`` entry, so source in a NEW package dir was invisible to the
    judge (it blocked every criterion as 'implementation missing'). With
    -uall the files are listed individually and reach the prompt."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)

    # A feature that creates a brand-new package directory.
    pkg = tmp_path / "src" / "orchestrator" / "notify"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .slack import Notifier\n", encoding="utf-8")
    (pkg / "slack.py").write_text("class Notifier:\n    pass\n", encoding="utf-8")

    adapter, llm = _judge({"criteria": [{"criterion": "exports a CSV file", "status": "met"}]})
    await adapter.review(path=str(tmp_path), issue_key="SDLC-1", spec=SPEC)

    assert "src/orchestrator/notify/slack.py" in llm.last_user
    assert "class Notifier" in llm.last_user
