"""Provenance carried past the spec writer: the judge, the SPEC banner, the views.

`FeatureSpec` separates the criteria a source stated from the ones the spec writer
inferred. These tests are about everything *downstream* of that split honouring it —
the failure being guarded is a run rejected for missing a requirement nobody asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.review import SemanticReviewAdapter, _only_stated

_STATED = ["exports a CSV file", "handles empty input"]
_PROPOSED = ["emits a generated_at timestamp"]

SPEC: dict[str, Any] = {
    "title": "CSV export",
    "acceptance_criteria": list(_STATED),
    "proposed_criteria": list(_PROPOSED),
}


class _JudgeLLM:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.last_user = ""

    async def complete(self, messages: list[Message], **_: Any) -> CompletionResult:
        self.last_user = messages[-1].content
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return CompletionResult(text, "m", 1, 1, 0.0, 1.0)


def _worktree(tmp_path: Path) -> Path:
    (tmp_path / "export.py").write_text("def export_csv(rows):\n    return 'a,b'\n", encoding="utf-8")
    return tmp_path


def _rows(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"criteria": [{"criterion": c, "status": s} for c, s in pairs], "summary": "s"}


# ---- the judge: proposed criteria are context, never contract ----------------------


async def test_an_unmet_proposed_criterion_does_not_block(tmp_path: Path) -> None:
    """The whole point: a change is not rejected for missing what nobody asked for."""
    llm = _JudgeLLM(_rows((_STATED[0], "met"), (_STATED[1], "met"), (_PROPOSED[0], "unmet")))

    result = await SemanticReviewAdapter(llm).review(
        path=str(_worktree(tmp_path)), issue_key="S-1", spec=SPEC
    )

    assert result.verdict == "approve"
    assert not result.blockers


async def test_an_unmet_stated_criterion_still_blocks(tmp_path: Path) -> None:
    """Narrowing must not have disarmed the gate."""
    llm = _JudgeLLM(_rows((_STATED[0], "unmet"), (_STATED[1], "met")))

    result = await SemanticReviewAdapter(llm).review(
        path=str(_worktree(tmp_path)), issue_key="S-1", spec=SPEC
    )

    assert result.verdict == "request_changes"
    assert result.blockers


async def test_proposed_criteria_reach_the_prompt_as_context(tmp_path: Path) -> None:
    """Told they exist, so their implementation doesn't read as scope creep."""
    llm = _JudgeLLM(_rows((_STATED[0], "met"), (_STATED[1], "met")))

    await SemanticReviewAdapter(llm).review(path=str(_worktree(tmp_path)), issue_key="S-1", spec=SPEC)

    assert _PROPOSED[0] in llm.last_user
    assert "do NOT" in llm.last_user, "and told plainly not to judge against them"


async def test_a_spec_with_no_proposed_criteria_prompts_exactly_as_before(tmp_path: Path) -> None:
    llm = _JudgeLLM(_rows((_STATED[0], "met"), (_STATED[1], "met")))
    spec = {"title": "CSV export", "acceptance_criteria": list(_STATED)}

    await SemanticReviewAdapter(llm).review(path=str(_worktree(tmp_path)), issue_key="S-1", spec=spec)

    assert "PROPOSED CRITERIA" not in llm.last_user


# ---- the narrowing itself -----------------------------------------------------------


def test_only_stated_drops_rows_for_criteria_the_source_never_stated() -> None:
    rows = [{"criterion": _STATED[0], "status": "met"}, {"criterion": _PROPOSED[0], "status": "unmet"}]
    assert _only_stated(rows, _STATED) == [rows[0]]


def test_only_stated_is_whitespace_insensitive() -> None:
    """Matching intake.specs._merge_criteria: a re-spaced criterion is the stated one."""
    rows = [{"criterion": "exports   a  CSV file", "status": "unmet"}]
    assert _only_stated(rows, _STATED) == rows


def test_only_stated_keeps_everything_when_nothing_matches() -> None:
    """Fail closed: a judge that renamed every criterion must not approve vacuously."""
    rows = [{"criterion": "something else entirely", "status": "unmet"}]
    assert _only_stated(rows, _STATED) == rows


def test_only_stated_is_a_no_op_without_a_stated_list() -> None:
    rows = [{"criterion": "anything", "status": "unmet"}]
    assert _only_stated(rows, None) == rows


# ---- the surfaces a human reads ----------------------------------------------------


def test_the_issue_body_labels_proposed_criteria_apart_from_stated() -> None:
    from orchestrator.intake.service import spec_to_issue_request

    body = spec_to_issue_request(SPEC).description

    assert _STATED[0] in body and _PROPOSED[0] in body
    assert "Proposed criteria" in body
    # And the reader can tell which is which: the proposed heading comes after the stated one.
    assert body.index("Acceptance criteria") < body.index("Proposed criteria")


def test_the_issue_body_has_no_proposed_heading_when_there_are_none() -> None:
    from orchestrator.intake.service import spec_to_issue_request

    spec = {"title": "CSV export", "acceptance_criteria": list(_STATED)}
    body = spec_to_issue_request(spec).description

    assert "Proposed criteria" not in body


def test_the_report_cell_labels_proposed_criteria() -> None:
    from orchestrator.intake.report import _criteria_cell

    cell = _criteria_cell(SPEC)

    assert _STATED[0] in cell and _PROPOSED[0] in cell
    assert "Proposed" in cell


def test_the_report_cell_is_unchanged_without_proposed_criteria() -> None:
    from orchestrator.intake.report import _criteria_cell

    assert "Proposed" not in _criteria_cell({"acceptance_criteria": list(_STATED)})
