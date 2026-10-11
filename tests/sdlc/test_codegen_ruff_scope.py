"""B64: codegen's ruff auto-fix touches only what this run created, unless the repo configures ruff.

``apply_files`` ran ``ruff check --fix`` and ``ruff format`` over every file the model wrote, edited
existing files included. With no ruff configuration ruff applies its own defaults, so a one-line edit
came back as a whole-file rewrite: the first real SWE-bench attempt was a 53,109-character patch over
8 files for a change of a few lines. The rule (programme D35, plan Track C): fix a file only if this
run created it, or if the repository configures ruff.

Measured with the real ruff, so a test here fails for the right reason: the reformatting is observable
in the bytes. Skipped when ruff is not installed, which is also when ``_ruff_fix`` does nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.agentic.codegen_tools import CodegenSession, build_codegen_tools
from orchestrator.sdlc.codegen import _ruff_fix, apply_files


def _ruff_installed() -> bool:
    try:
        return (
            subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True).returncode == 0
        )
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _ruff_installed(), reason="ruff is not installed")

#: Not ruff-formatted: spacing and quotes that `ruff format` always rewrites.
MESSY = "a = {  'x':1 }\n"
FORMATTED = 'a = {"x": 1}\n'


def _edit(rel: str, find: str, replace: str) -> dict[str, Any]:
    return {"path": rel, "edits": [{"find": find, "replace": replace}]}


def _show_settings_path(root: Path, rel: str) -> str:
    """Ruff's own answer to "which config applies to this file": a ``Settings path:`` line, or none."""
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-settings", rel],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return next((ln for ln in done.stdout.splitlines() if ln.startswith("Settings path:")), "")


# --- the defect, and what must not change ----------------------------------------------------


def test_editing_an_existing_file_in_a_repo_without_ruff_config_changes_only_the_edit(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(MESSY + "b = 1\n", encoding="utf-8")

    apply_files([_edit("mod.py", "b = 1", "b = 2")], tmp_path, written_tracker={}, grounded=False)

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == MESSY + "b = 2\n"


def test_a_file_the_run_creates_in_a_repo_without_ruff_config_is_still_fixed(tmp_path: Path) -> None:
    apply_files([{"path": "new.py", "content": MESSY}], tmp_path, written_tracker={}, grounded=False)

    assert (tmp_path / "new.py").read_text(encoding="utf-8") == FORMATTED


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("ruff.toml", "line-length = 100\n"),
        (".ruff.toml", "line-length = 100\n"),
        ("pyproject.toml", "[tool.ruff]\nline-length = 100\n"),
        ("pyproject.toml", "[tool.ruff.lint]\nselect = ['E']\n"),
    ],
)
def test_a_repo_that_configures_ruff_still_has_its_edited_files_fixed(
    tmp_path: Path, name: str, text: str
) -> None:
    (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "mod.py").write_text(MESSY + "b = 1\n", encoding="utf-8")

    apply_files([_edit("mod.py", "b = 1", "b = 2")], tmp_path, written_tracker={}, grounded=False)

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == FORMATTED + "b = 2\n"


def test_a_pyproject_without_a_ruff_table_is_not_a_ruff_config(tmp_path: Path) -> None:
    """Measured: ruff prints no ``Settings path`` for it, i.e. it runs on its defaults."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "mod.py").write_text(MESSY + "b = 1\n", encoding="utf-8")

    apply_files([_edit("mod.py", "b = 1", "b = 2")], tmp_path, written_tracker={}, grounded=False)

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == MESSY + "b = 2\n"


def test_a_nested_config_applies_to_its_own_subtree_only(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    (tmp_path / "sub" / "inner.py").write_text(MESSY + "b = 1\n", encoding="utf-8")
    (tmp_path / "outer.py").write_text(MESSY + "b = 1\n", encoding="utf-8")

    apply_files(
        [_edit("sub/inner.py", "b = 1", "b = 2"), _edit("outer.py", "b = 1", "b = 2")],
        tmp_path,
        written_tracker={},
        grounded=False,
    )

    assert (tmp_path / "sub" / "inner.py").read_text(encoding="utf-8") == FORMATTED + "b = 2\n"
    assert (tmp_path / "outer.py").read_text(encoding="utf-8") == MESSY + "b = 2\n"


def test_a_config_above_the_repository_root_is_not_the_repositorys(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")  # outside the repo
    (repo / "mod.py").write_text(MESSY + "b = 1\n", encoding="utf-8")

    apply_files([_edit("mod.py", "b = 1", "b = 2")], repo, written_tracker={}, grounded=False)

    assert (repo / "mod.py").read_text(encoding="utf-8") == MESSY + "b = 2\n"


def test_a_file_created_in_one_pass_is_still_fixed_when_a_later_pass_edits_it(tmp_path: Path) -> None:
    """Repair passes see the run's own files as existing; the run-wide tracker says they are not."""
    created: dict[Path, list[Path]] = {}
    apply_files(
        [{"path": "new.py", "content": "b = 1\n"}],
        tmp_path,
        written_tracker={},
        grounded=False,
        created_tracker=created,
    )

    apply_files(
        [_edit("new.py", "b = 1", MESSY.rstrip("\n"))],
        tmp_path,
        written_tracker={},
        grounded=False,
        created_tracker=created,
    )

    assert (tmp_path / "new.py").read_text(encoding="utf-8") == FORMATTED


# --- _ruff_fix itself ------------------------------------------------------------------------


def test_with_no_answer_about_what_was_created_nothing_is_reformatted(tmp_path: Path) -> None:
    """``created=None`` is the caller saying "I cannot tell": fail safe, touch nothing."""
    (tmp_path / "mod.py").write_text(MESSY, encoding="utf-8")

    _ruff_fix(tmp_path, [str(tmp_path / "mod.py")], created=None)

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == MESSY


def test_a_file_named_as_created_is_fixed(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(MESSY, encoding="utf-8")

    _ruff_fix(tmp_path, [str(tmp_path / "mod.py")], created=[tmp_path / "mod.py"])

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == FORMATTED


_FUTURE_THEN_DOC = '''from __future__ import annotations

"""Doc."""

import os
'''


def test_the_docstring_hoist_follows_the_same_targets(tmp_path: Path) -> None:
    """It edits a file the run only meant to patch, so an unconfigured repo's existing file is left alone."""
    (tmp_path / "old.py").write_text(_FUTURE_THEN_DOC, encoding="utf-8")
    (tmp_path / "new.py").write_text(_FUTURE_THEN_DOC, encoding="utf-8")

    _ruff_fix(tmp_path, [str(tmp_path / "old.py"), str(tmp_path / "new.py")], created=[tmp_path / "new.py"])

    assert (tmp_path / "old.py").read_text(encoding="utf-8") == _FUTURE_THEN_DOC
    assert (tmp_path / "new.py").read_text(encoding="utf-8").startswith('"""Doc."""')


# --- the config detector ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "rel"),
    [
        ({}, "a.py"),
        ({"ruff.toml": "line-length = 90\n"}, "a.py"),
        ({".ruff.toml": "line-length = 90\n"}, "a.py"),
        ({"pyproject.toml": "[tool.ruff]\nline-length = 90\n"}, "a.py"),
        ({"pyproject.toml": "[tool.ruff.format]\nquote-style = 'single'\n"}, "a.py"),
        ({"pyproject.toml": '[project]\nname = "x"\n'}, "a.py"),
        ({"pyproject.toml": "[tool.black]\nline-length = 90\n"}, "a.py"),
        ({"pyproject.toml": "this is [not toml"}, "a.py"),
        ({"sub/ruff.toml": "line-length = 90\n"}, "sub/a.py"),
        ({"sub/ruff.toml": "line-length = 90\n"}, "a.py"),
        ({"ruff.toml": "line-length = 90\n"}, "sub/deeper/a.py"),
    ],
)
def test_the_detector_agrees_with_ruffs_own_choice_of_config(
    tmp_path: Path, files: dict[str, str], rel: str
) -> None:
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x = 1\n", encoding="utf-8")

    from orchestrator.sdlc.codegen import _repo_configures_ruff

    ruff_found_one = _show_settings_path(tmp_path, rel) != ""
    ours = _repo_configures_ruff(tmp_path, tmp_path / rel)

    # A malformed pyproject makes ruff fail outright; "not configured" is the safe answer there.
    if files.get("pyproject.toml") == "this is [not toml":
        assert ours is False
    else:
        assert ours == ruff_found_one


# --- the agentic path ------------------------------------------------------------------------


async def test_a_file_the_agentic_loop_creates_is_remembered_and_stays_fixable(tmp_path: Path) -> None:
    session = CodegenSession(tracker={}, created={})
    tools = {t.spec.name: t for t in build_codegen_tools(tmp_path, grounded=False, session=session)}

    await tools["write_files"].run({"files": [{"path": "new.py", "content": "b = 1\n"}]})
    await tools["write_files"].run({"files": [_edit("new.py", "b = 1", MESSY.rstrip("\n"))]})

    assert session.created[tmp_path.resolve()] == [tmp_path / "new.py"]
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == FORMATTED


async def test_an_existing_file_edited_through_the_agentic_loop_is_not_reformatted(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(MESSY + "b = 1\n", encoding="utf-8")
    session = CodegenSession(tracker={}, created={})
    tools = {t.spec.name: t for t in build_codegen_tools(tmp_path, grounded=False, session=session)}

    await tools["write_files"].run({"files": [_edit("mod.py", "b = 1", "b = 2")]})

    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == MESSY + "b = 2\n"


def test_the_adapters_loop_sessions_share_its_run_wide_trackers() -> None:
    """Every pass of one run — the loop, its resume after a pause, a later refine — must see the same record
    of what the run created. This also feeds B47's "test files this session created" (see `refine`)."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter

    adapter = LLMCodegenAdapter(object())  # type: ignore[arg-type]

    first, resumed = adapter._session(), adapter._session(written=["a.py"], summary="s")

    assert first.created is adapter._created and first.tracker is adapter._written
    assert resumed.created is adapter._created and resumed.written == ["a.py"] and resumed.summary == "s"
