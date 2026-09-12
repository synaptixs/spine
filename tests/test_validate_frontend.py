"""The real-repository smoke test script: run it against a local fixture (no network) and
check its aggregation logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate-frontend.py"


@pytest.fixture
def validate(tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_frontend", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_frontend"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_validate_one_against_a_local_fixture_passes(
    validate: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tiny local Python repo, exercised through the real extract/verify/state pipeline —
    no network involved, since `resolve_repo_source` treats a non-URL spec as a local path."""
    (tmp_path / "greet.py").write_text(
        "def hello(name):\n    return f'hi {name}'\n\n\ndef main():\n    return hello('world')\n",
        encoding="utf-8",
    )

    ok = validate.validate_one("python", str(tmp_path))

    assert ok is True
    out = capsys.readouterr().out
    assert "extract:" in out
    assert "pkg verify: OK" in out
    assert "python" in out  # the by-language node breakdown


def test_validate_one_refuses_a_disallowed_source(
    validate: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """A git URL Spine's own SSRF guard rejects (not on the allow-list) must be reported,
    not raise — `main()` needs a clean boolean per repo to aggregate the exit code."""
    ok = validate.validate_one("python", "https://not-a-real-git-host.invalid/x/y")

    assert ok is False
    assert "REFUSED" in capsys.readouterr().out


def test_validate_one_reports_and_continues_when_extraction_raises(
    validate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A crash inside the clone/extract/verify/state block (a clone timeout, a private repo
    gone missing, an extractor edge case) must be caught and reported, not propagate —
    `main()`'s loop over multiple URLs depends on this function never raising, or one bad
    repo takes the rest of an unattended run down with it."""
    from orchestrator.pkg import RepoCodeExtractor

    def boom(self: object, root: object) -> None:
        raise RuntimeError("simulated extraction failure")

    monkeypatch.setattr(RepoCodeExtractor, "extract", boom)
    (tmp_path / "greet.py").write_text("def hello():\n    pass\n", encoding="utf-8")

    ok = validate.validate_one("python", str(tmp_path))

    assert ok is False
    assert "FAILED: RuntimeError: simulated extraction failure" in capsys.readouterr().out


def test_main_fails_when_any_repo_fails(validate: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_validate_one(language: str, url: str) -> bool:
        calls.append(url)
        return url != "bad"

    monkeypatch.setattr(validate, "validate_one", fake_validate_one)
    monkeypatch.setattr(sys, "argv", ["validate-frontend.py", "perl", "good", "bad"])

    assert validate.main() == 1
    assert calls == ["good", "bad"]  # every repo is checked, not short-circuited


def test_main_passes_when_every_repo_passes(validate: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate, "validate_one", lambda language, url: True)
    monkeypatch.setattr(sys, "argv", ["validate-frontend.py", "perl", "a", "b"])

    assert validate.main() == 0
