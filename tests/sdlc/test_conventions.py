"""Convention learning (G8): RepoConventions extraction + prompt rendering."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.conventions import RepoConventions, extract_conventions


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    """A small repo whose files follow a consistent house style."""
    _write(tmp_path / "pyproject.toml", "[tool.ruff]\nline-length = 110\n")
    pkg = tmp_path / "src" / "myapp"
    for i in range(4):
        _write(
            pkg / f"mod{i}.py",
            '"""Module docstring."""\n\n'
            "from __future__ import annotations\n\n"
            "from myapp.other import thing\n\n\n"
            "def run(x: int) -> int:\n    return thing(x)\n",
        )
    for i in range(3):
        _write(
            tmp_path / "tests" / f"test_mod{i}.py",
            "from __future__ import annotations\n\n\ndef test_it() -> None:\n    assert True\n",
        )
    return tmp_path


def test_extract_derives_house_style(tmp_path: Path) -> None:
    conv = extract_conventions(_make_repo(tmp_path))
    assert conv.future_annotations is True
    assert conv.absolute_imports is True
    assert conv.module_docstrings is True
    assert conv.typed_defs is True
    assert conv.line_length == 110
    assert conv.test_style == "functions"
    assert conv.top_package == "myapp"
    assert conv.sampled >= 4


def test_prompt_block_lists_observed_rules(tmp_path: Path) -> None:
    block = extract_conventions(_make_repo(tmp_path)).prompt_block()
    assert "REPO CONVENTIONS" in block
    assert "from __future__ import annotations" in block
    assert "absolute paths under `myapp.`" in block
    assert "110 characters" in block
    assert "def test_*" in block


def test_class_test_style_detected(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    _write(tmp_path / "src" / "a" / "m.py", "x = 1\n")
    _write(
        tmp_path / "tests" / "test_a.py",
        "class TestThing:\n    def test_one(self) -> None:\n        assert True\n",
    )
    conv = extract_conventions(tmp_path)
    assert conv.test_style == "classes"


def test_empty_repo_yields_empty_digest_and_no_block(tmp_path: Path) -> None:
    conv = extract_conventions(tmp_path)
    assert conv.is_empty
    assert conv.prompt_block() == ""


def test_unconventional_repo_does_not_assert_conventions(tmp_path: Path) -> None:
    # Files that share no consistent style → prevalence threshold not met.
    _write(tmp_path / "src" / "p" / "a.py", "x = 1\n")
    _write(tmp_path / "src" / "p" / "b.py", "y = 2\n")
    conv = extract_conventions(tmp_path)
    assert conv.future_annotations is False
    assert conv.module_docstrings is False
    # line_length still falls back to ruff default when no pyproject says.
    assert conv.line_length == 88


def test_default_conventions_render_only_line_length() -> None:
    # A digest with one sampled file but no observed conventions still names
    # the line length (always safe to state).
    conv = RepoConventions(line_length=120, sampled=1)
    block = conv.prompt_block()
    assert "120 characters" in block
