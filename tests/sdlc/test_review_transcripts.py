"""B92 (SSPN-197): a transcript or a bug description from the issue must not decide the verdict.

Intake copies lines of an issue into the stated acceptance criteria. On a bug report those can
be a REPL session (``xarray-4966``: a remote URL the sandbox cannot reach, so the judge said
"uncertain" three times and the run ended with no patch) or a sentence that describes the
defect (``django-14404``: "returns path_info instead of path", read as the requirement, so the
judge rejected the correct fix). The strings below are the real criteria from those two runs.

What these tests keep unchanged matters as much as what they change: ``unmet`` still blocks,
and an ``uncertain`` criterion that is not a transcript still blocks — that rule exists because
a run once shipped a change whose only doubt was "existing output fields remain unchanged".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.review import _JUDGE_SYSTEM, SemanticReviewAdapter, _is_transcript

# xarray-4966, P5 run 2026-10-17: the one "stated" criterion intake produced.
XARRAY_TRANSCRIPT = (
    'In [2]: xr.open_dataset("https://observations.ipsl.fr/thredds/dodsC/EUREC4A/PRODUCTS/'
    'testdata/netcdf_testfiles/test_NC_BYTE_neg.nc", engine="netcdf4")\n'
    "Out[2]: \n<xarray.Dataset>\nDimensions:  (test: 7)\nCoordinates:\n"
    "  * test     (test) float32 -128.0 -1.0 0.0 1.0 2.0 nan 127.0\nData variables:\n    *empty*"
)
# django-14404, P5 run: the issue's description of the bug, stated as a criterion.
DJANGO_SYMPTOM = (
    "catch_all_view returns redirect to '%s/' % request.path_info (script name cut off there) "
    "instead of '%s/' % request.path (with the script name)"
)
SYMPY_EXPECTED_OUTPUT = ">>> Poly(x)*x\nPoly(x**2, x, domain='ZZ')"
PROSE = "handles empty input"


class _JudgeLLM:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.last_user = ""

    async def complete(self, messages: list[Message], **_: Any) -> CompletionResult:
        self.last_user = messages[-1].content
        return CompletionResult(json.dumps(self._payload), "m", 1, 1, 0.0, 1.0)


def _worktree(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return tmp_path


def _rows(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"criteria": [{"criterion": c, "status": s, "evidence": "e"} for c, s in pairs], "summary": "s"}


def _spec(*criteria: str, **extra: Any) -> dict[str, Any]:
    return {"title": "t", "acceptance_criteria": list(criteria), **extra}


async def _review(tmp_path: Path, spec: dict[str, Any], payload: dict[str, Any]) -> Any:
    llm = _JudgeLLM(payload)
    result = await SemanticReviewAdapter(llm).review(
        path=str(_worktree(tmp_path)), issue_key="S-1", spec=spec
    )
    return result, llm


# ---- the shape test ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        XARRAY_TRANSCRIPT,
        SYMPY_EXPECTED_OUTPUT,
        ">>> from django.db.models import Q\n>>> Q(x=1).deconstruct()",
        'Traceback (most recent call last):\n  File "<stdin>", line 1, in <module>\nKeyError: 1',
        "  In [4]: S1\nOut[4]: {n}",
        "$ python manage.py check\nSystemCheckError",
    ],
)
def test_a_repl_session_or_traceback_is_a_transcript(text: str) -> None:
    assert _is_transcript(text)


@pytest.mark.parametrize(
    "text",
    [
        DJANGO_SYMPTOM,
        PROSE,
        "uses the >>> operator for the prompt",  # the marker must open a line
        "charges a $ 5 fee",  # `$ ` mid-line is not a shell prompt
        "Given a request, When it is handled, Then it redirects",
        "",
    ],
)
def test_prose_is_not_a_transcript(text: str) -> None:
    assert not _is_transcript(text)


# ---- the verdict mapping ----------------------------------------------------------


async def test_an_uncertain_transcript_does_not_block(tmp_path: Path) -> None:
    """The xarray-4966 case: the doubt is that a remote server's output cannot be reproduced."""
    result, _ = await _review(
        tmp_path,
        _spec(XARRAY_TRANSCRIPT, PROSE),
        _rows((XARRAY_TRANSCRIPT, "uncertain"), (PROSE, "met")),
    )

    assert not result.has_blocker
    assert result.verdict == "comment"
    assert result.uncertain == [XARRAY_TRANSCRIPT], "still reported: the escalation policy reads it"
    assert "not blocking" in result.summary
    assert "In [2]" in result.summary, "the soft pass names the criterion it did not insist on"


async def test_an_unmet_transcript_still_blocks(tmp_path: Path) -> None:
    """A transcript that is checkable and wrong is a failure (the sympy-13757 shape)."""
    result, _ = await _review(
        tmp_path,
        _spec(SYMPY_EXPECTED_OUTPUT, PROSE),
        _rows((SYMPY_EXPECTED_OUTPUT, "unmet"), (PROSE, "met")),
    )

    assert result.verdict == "request_changes"
    assert result.blockers


async def test_an_uncertain_criterion_that_is_not_a_transcript_still_blocks(tmp_path: Path) -> None:
    """The 2026 incident: 'existing output fields remain unchanged' was uncertain, and right."""
    result, _ = await _review(
        tmp_path,
        _spec(XARRAY_TRANSCRIPT, PROSE),
        _rows((XARRAY_TRANSCRIPT, "uncertain"), (PROSE, "uncertain")),
    )

    assert result.verdict == "comment"
    assert result.blockers == [f"acceptance criterion unverified: {PROSE}"]


async def test_an_unmet_criterion_blocks_even_beside_an_uncertain_transcript(tmp_path: Path) -> None:
    result, _ = await _review(
        tmp_path,
        _spec(XARRAY_TRANSCRIPT, PROSE),
        _rows((XARRAY_TRANSCRIPT, "uncertain"), (PROSE, "unmet")),
    )

    assert result.verdict == "request_changes"
    assert len(result.blockers) == 1 and PROSE in result.blockers[0]


async def test_a_judge_that_respaces_the_transcript_is_still_recognised(tmp_path: Path) -> None:
    """Rows are matched to the stated criterion whitespace-insensitively, as `_only_stated` does."""
    respaced = " ".join(XARRAY_TRANSCRIPT.split())
    result, _ = await _review(tmp_path, _spec(XARRAY_TRANSCRIPT), _rows((respaced, "uncertain")))

    assert not result.has_blocker


async def test_the_off_switch_restores_the_old_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDLC_JUDGE_TRANSCRIPT_SOFT", "0")
    result, _ = await _review(
        tmp_path,
        _spec(XARRAY_TRANSCRIPT, PROSE),
        _rows((XARRAY_TRANSCRIPT, "uncertain"), (PROSE, "met")),
    )

    assert result.verdict == "comment"
    assert len(result.blockers) == 1 and "In [2]" in result.blockers[0]


# ---- the ticket context -----------------------------------------------------------


async def test_the_judge_is_shown_the_tickets_own_summary_and_problem(tmp_path: Path) -> None:
    """django-14404: with only the criterion, 'A instead of B' reads either way round."""
    summary = "catch_all_view drops FORCE_SCRIPT_NAME from its redirect."
    problem = "The APPEND_SLASH redirect is built from request.path_info, which cuts the script name off."
    result, llm = await _review(
        tmp_path,
        _spec(DJANGO_SYMPTOM, summary=summary, problem=problem),
        _rows((DJANGO_SYMPTOM, "met")),
    )

    assert "TICKET" in llm.last_user
    assert summary in llm.last_user and problem in llm.last_user
    assert DJANGO_SYMPTOM in llm.last_user, "the stated criterion is still what is judged"
    assert result.verdict == "approve"


async def test_a_spec_with_no_summary_or_problem_prompts_as_before(tmp_path: Path) -> None:
    _, llm = await _review(tmp_path, _spec(PROSE), _rows((PROSE, "met")))

    assert "TICKET" not in llm.last_user


def test_the_judge_is_told_a_criterion_may_state_the_defect() -> None:
    assert "describes the defect" in _JUDGE_SYSTEM
    assert "correction" in _JUDGE_SYSTEM
