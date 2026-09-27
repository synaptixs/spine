"""Refine may repair lint and type findings in the tests this session wrote (B47, SSPN-93).

Refine is told not to touch tests, so a model cannot make a failing test pass by changing it.
That also left every ruff/mypy finding in a *generated* test file unfixable: measured at 25/30
preflight on develop, every failure in a test file this session wrote. Three pieces close it
without reopening the door the rule closed:

- a docstring after ``from __future__`` is moved to the top before ruff runs (no model call);
- only a lint/type line naming a session-written Python test unlocks it — a pytest failure never;
- any refine edit to a session test that drops or changes an assert, or drops a test, is refused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import _hoist_future_docstring, apply_files

# --- the rewrite (P1) ------------------------------------------------------------------------

_MISPLACED = (
    'from __future__ import annotations\n\n"""Tests for the ledger."""\n\nimport os\n\nprint(os.sep)\n'
)


def test_a_docstring_after_future_is_moved_to_the_top() -> None:
    fixed = _hoist_future_docstring(_MISPLACED)

    assert fixed is not None
    assert fixed.startswith('"""Tests for the ledger."""\n\nfrom __future__ import annotations\n')
    assert ast.get_docstring(ast.parse(fixed)) == "Tests for the ledger."


def test_the_rewrite_changes_no_code() -> None:
    fixed = _hoist_future_docstring(_MISPLACED)

    assert fixed is not None
    before, after = ast.parse(_MISPLACED).body, ast.parse(fixed).body
    assert ast.dump(after[0]) == ast.dump(before[1])  # the docstring, moved
    assert [ast.dump(s) for s in after[1:]] == [ast.dump(s) for s in before[:1] + before[2:]]


def test_several_future_imports_and_a_leading_comment_are_kept() -> None:
    text = (
        "#!/usr/bin/env python\n"
        "from __future__ import annotations\n"
        "from __future__ import division\n"
        '"""Multi\nline."""\n'
        "import os\n"
    )

    fixed = _hoist_future_docstring(text)

    assert fixed is not None
    assert fixed.startswith("#!/usr/bin/env python\n" + '"""Multi\nline."""\n')
    assert ast.get_docstring(ast.parse(fixed)) == "Multi\nline."
    assert "from __future__ import division\n" in fixed


@pytest.mark.parametrize(
    "text",
    [
        '"""Already first."""\n\nfrom __future__ import annotations\n\nimport os\n',
        "from __future__ import annotations\n\nimport os\n",
        'import os\n\n"""Not after a future import."""\n',
        'from __future__ import annotations\n\nx = 1\n"""A string later on."""\n',
        'from __future__ import annotations\n"""On a line with code."""; x = 1\n',
        "def broken(:\n",
        "",
    ],
)
def test_anything_else_is_left_alone(text: str) -> None:
    assert _hoist_future_docstring(text) is None


def test_a_written_file_is_rewritten_before_ruff_runs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()

    apply_files(
        [{"path": "tests/test_ledger.py", "content": _MISPLACED}],
        tmp_path,
        written_tracker={},
        grounded=False,
    )

    text = (tmp_path / "tests" / "test_ledger.py").read_text(encoding="utf-8")
    assert ast.get_docstring(ast.parse(text)) == "Tests for the ledger."
