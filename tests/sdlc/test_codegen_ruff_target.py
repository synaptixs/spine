"""B69 (SSPN-152): codegen's ruff autofix must not rewrite code beyond the Python the repository declares.

After every write, ``_ruff_fix`` runs ``ruff check --fix`` and ``ruff format`` on the files the run
created (B64). With no ruff configuration ruff applies its default rules and its default target
Python, and it reads a declared minimum only from ``pyproject.toml`` and only for 3.7 and up. So a
model that wrote ``typing.Dict`` and a nested ``with`` for a Python 3.6 repository got ``dict[...]``,
``X | None`` and a parenthesised ``with`` back: the three constructs that failed the SWE-bench Django
instances. These tests run the real ruff, so a failure is visible in the bytes.

The new name (``_ruff_target_version``) is reached through a helper so this file type-checks while
the function is still being added.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import _ruff_fix, apply_files
from tests.sdlc.test_codegen_ruff_scope import _ruff_installed

pytestmark = pytest.mark.skipif(not _ruff_installed(), reason="ruff is not installed")

#: What a model writes for a Python 3.6 repository: ``typing`` generics and one long multi-item ``with``.
COMPLIANT = """\
from typing import Dict, List, Optional
from unittest import mock


def lookup(table: Dict[str, str], keys: List[str], default: Optional[str] = None) -> str:
    return table.get(keys[0], default or "")


def test_prog_name():
    with mock.patch("django.core.management.ManagementUtility.fetch_command") as one, mock.patch("django.core.management.base.CommandParser.parse_known_args") as two:
        assert one and two
"""  # noqa: E501 — the long line is the point: it is what makes the formatter parenthesise it


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _fix(root: Path, body: str = COMPLIANT, rel: str = "mod.py") -> str:
    """Run codegen's own autofix over a file this run created, and return what is left on disk."""
    file = _write(root / rel, body)
    _ruff_fix(root, [rel], created=[file])
    return file.read_text(encoding="utf-8")


def _declare(root: Path, how: str, floor: str) -> None:
    """Declare ``floor`` (e.g. "3.6") the way the Django instances did, where ruff cannot see it."""
    if how == "setup.cfg":
        _write(root / "setup.cfg", f"[options]\npython_requires = >={floor}\n")
    elif how == "setup.py":
        _write(root / "setup.py", f"from setuptools import setup\n\nsetup(python_requires='>={floor}')\n")
    elif how == "classifiers":
        # Django 3.0: the specifier is built at run time, so only the classifiers carry a version.
        _write(
            root / "setup.py",
            "REQUIRED_PYTHON = (3, 6)\n"
            "setup(\n    python_requires='>={}.{}'.format(*REQUIRED_PYTHON),\n"
            f"    classifiers=['Programming Language :: Python :: {floor}'],\n)\n",
        )
    elif how == "pyproject":
        _write(root / "pyproject.toml", f'[project]\nname = "demo"\nrequires-python = ">={floor}"\n')
    else:  # pragma: no cover - a typo in a test
        raise AssertionError(how)


def _unchanged_in_meaning(text: str) -> None:
    """The compliant spellings survive: typing generics, ``Optional``, and no parenthesised ``with``."""
    assert "Dict[str, str]" in text and "List[str]" in text and "Optional[str]" in text, text
    assert "dict[" not in text and "list[" not in text and "| None" not in text, text
    assert "with (" not in text, text


# ---- the floors ruff cannot see --------------------------------------------


@pytest.mark.parametrize(
    ("how", "floor"),
    [
        ("setup.cfg", "3.6"),
        ("setup.py", "3.6"),
        ("classifiers", "3.6"),
        ("setup.cfg", "3.8"),
        ("setup.py", "3.8"),
        ("pyproject", "3.8"),
    ],
)
def test_the_autofix_does_not_rewrite_code_the_declared_python_has_not_got(
    tmp_path: Path, how: str, floor: str
) -> None:
    _declare(tmp_path, how, floor)
    _unchanged_in_meaning(_fix(tmp_path))


def test_a_floor_below_ruffs_lowest_target_is_honoured_as_its_lowest(tmp_path: Path) -> None:
    for floor in ("3.6", "2.7"):
        root = tmp_path / floor
        _declare(root, "setup.cfg", floor)
        _unchanged_in_meaning(_fix(root))


# ---- the fix removes only the invalid rewrites -----------------------------


def test_at_3_9_nothing_that_needs_3_10_is_written(tmp_path: Path) -> None:
    _declare(tmp_path, "setup.cfg", "3.9")
    text = _fix(tmp_path)
    assert "| None" not in text and "Optional[str]" in text  # `X | None` needs 3.10


def test_at_3_10_the_rewrites_that_are_valid_there_still_happen(tmp_path: Path) -> None:
    _declare(tmp_path, "setup.cfg", "3.10")
    text = _fix(tmp_path)
    assert "dict[str, str]" in text and "str | None" in text


def test_an_undeclared_repository_is_exactly_as_before(tmp_path: Path) -> None:
    # Nothing is known about its floor, so ruff's default applies, as B64 left it.
    text = _fix(tmp_path)
    assert "dict[str, str]" in text and "str | None" in text


# ---- a repository that configures ruff is not overridden -------------------


def test_the_repositorys_own_ruff_config_governs(tmp_path: Path) -> None:
    _declare(tmp_path, "setup.cfg", "3.6")
    _write(tmp_path / "ruff.toml", 'target-version = "py312"\n\n[lint]\nselect = ["UP"]\n')
    text = _fix(tmp_path)
    assert "dict[str, str]" in text and "str | None" in text  # its config asked for these


def test_a_mixed_batch_gets_the_flag_only_where_there_is_no_config(tmp_path: Path) -> None:
    _declare(tmp_path, "setup.cfg", "3.6")
    _write(tmp_path / "sub" / "ruff.toml", 'target-version = "py312"\n\n[lint]\nselect = ["UP"]\n')
    plain = _write(tmp_path / "mod.py", COMPLIANT)
    configured = _write(tmp_path / "sub" / "other.py", COMPLIANT)

    _ruff_fix(tmp_path, ["mod.py", "sub/other.py"], created=[plain, configured])

    _unchanged_in_meaning(plain.read_text(encoding="utf-8"))
    assert "dict[str, str]" in configured.read_text(encoding="utf-8")


# ---- through the real write path -------------------------------------------


def test_apply_files_keeps_the_compliant_file_for_a_3_6_repository(tmp_path: Path) -> None:
    _declare(tmp_path, "classifiers", "3.6")
    apply_files(
        [{"path": "tests/test_x.py", "content": COMPLIANT}],
        tmp_path,
        written_tracker={},
        grounded=False,
        created_tracker={},
    )
    _unchanged_in_meaning((tmp_path / "tests" / "test_x.py").read_text(encoding="utf-8"))


# ---- the target mapping ----------------------------------------------------


def _target(floor: tuple[int, int] | None) -> str | None:
    module = importlib.import_module("orchestrator.sdlc.codegen")
    result: str | None = module._ruff_target_version(floor)
    return result


@pytest.mark.parametrize(
    ("floor", "expected"),
    [
        (None, None),
        ((3, 6), "py37"),
        ((2, 7), "py37"),
        ((3, 0), "py37"),
        ((3, 7), "py37"),
        ((3, 8), "py38"),
        ((3, 12), "py312"),
        ((3, 15), "py315"),
        ((3, 16), "py315"),
        ((4, 0), "py315"),
    ],
)
def test_the_floor_maps_to_a_target_ruff_accepts(floor: tuple[int, int] | None, expected: str | None) -> None:
    assert _target(floor) == expected


def test_every_mapped_target_is_one_ruff_accepts() -> None:
    import subprocess
    import sys

    for minor in range(0, 20):
        target = _target((3, minor))
        assert target is not None
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--quiet",
                "--stdin-filename",
                "x.py",
                "--target-version",
                target,
            ],
            input="x = 1\n",
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, (minor, target, done.stderr)


# ---- visibility ------------------------------------------------------------


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "ruff_fix" in r.getMessage()]


def test_a_pass_that_changes_a_file_says_so_and_names_the_target(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="orchestrator.sdlc.codegen")
    _declare(tmp_path, "setup.cfg", "3.6")

    _fix(tmp_path, body="a = {  'x':1 }\n")

    (line,) = _warnings(caplog)
    assert "sdlc.codegen.ruff_fix" in line and "mod.py" in line and "py37" in line


def test_an_undeclared_repository_is_logged_as_the_default_target(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="orchestrator.sdlc.codegen")

    _fix(tmp_path, body="a = {  'x':1 }\n")

    (line,) = _warnings(caplog)
    assert "default" in line and "mod.py" in line


def test_a_pass_that_changes_nothing_logs_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="orchestrator.sdlc.codegen")
    _declare(tmp_path, "setup.cfg", "3.6")

    _fix(tmp_path, body='a = {"x": 1}\n')

    assert _warnings(caplog) == []


def test_the_log_line_carries_names_and_counts_never_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="orchestrator.sdlc.codegen")
    secret = "a = {  'password_hunter2':1 }\n"

    _fix(tmp_path, body=secret)

    (line,) = _warnings(caplog)
    assert "hunter2" not in line
