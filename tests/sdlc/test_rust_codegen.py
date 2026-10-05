"""Rust codegen contracts: Cargo placement, reverse dependents, and red results."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.rust_codegen import (
    CargoTestRunner,
    RustPreflightRunner,
    _changed_paths,
    rust_files,
    rust_project_error,
)
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.toolchains import TOOLCHAINS


def _workspace(root: Path) -> None:
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/core", "crates/app"]\nresolver = "3"\n',
        encoding="utf-8",
    )
    core = root / "crates/core"
    app = root / "crates/app"
    (core / "source").mkdir(parents=True)
    (app / "src").mkdir(parents=True)
    (core / "Cargo.toml").write_text(
        '[package]\nname = "core"\nversion = "0.1.0"\nedition = "2024"\n'
        'rust-version = "1.90"\n[lib]\npath = "source/core.rs"\n',
        encoding="utf-8",
    )
    (app / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\nedition = "2024"\n'
        '[dependencies]\ncore = { path = "../core" }\n',
        encoding="utf-8",
    )
    (core / "source/core.rs").write_text("pub fn value() -> i32 { 1 }\n", encoding="utf-8")
    (app / "src/main.rs").write_text("fn main() {}\n", encoding="utf-8")


def test_layout_selects_custom_cargo_target(tmp_path: Path) -> None:
    _workspace(tmp_path)
    layout = resolve_layout(tmp_path, language="rust", prefer_paths=["crates/core/source/new_feature.rs"])
    assert (layout.package_name, layout.target_kind, layout.target_name) == ("core", "lib", "core")
    assert layout.target_source == "crates/core/source/core.rs"
    assert layout.source_dir == "crates/core/source"
    assert layout.edition == "2024" and layout.rust_version == "1.90"
    assert "package `core`" in TOOLCHAINS["rust"].layout_guidance(layout)


def test_existing_layout_rejects_unknown_package(tmp_path: Path) -> None:
    _workspace(tmp_path)
    with pytest.raises(ValueError, match="Cargo package 'missing'"):
        resolve_layout(tmp_path, language="rust", package_name="missing")


def test_multi_package_layout_uses_only_unambiguous_default_member(tmp_path: Path) -> None:
    _workspace(tmp_path)
    with pytest.raises(ValueError, match="needs a package name or source path"):
        resolve_layout(tmp_path, language="rust")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/core", "crates/app"]\n'
        'default-members = ["crates/app"]\nresolver = "3"\n',
        encoding="utf-8",
    )
    layout = resolve_layout(tmp_path, language="rust")
    assert layout.package_name == "app"


def test_greenfield_scaffold_is_minimal_and_idempotent(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, language="rust", mode="new", package_name="sample")
    assert layout.target_kind == "lib"
    assert set(rust_files(layout)) == {"Cargo.toml", "src/lib.rs", "README.md", ".gitignore"}
    assert scaffold(tmp_path, layout)
    assert scaffold(tmp_path, layout) == []
    binary = resolve_layout(
        tmp_path / "other",
        language="rust",
        mode="new",
        package_name="cli",
        prefer_paths=["src/main.rs"],
    )
    assert binary.target_kind == "bin"
    assert "src/main.rs" in rust_files(binary)


def test_changed_shared_package_includes_reverse_dependents(tmp_path: Path) -> None:
    _workspace(tmp_path)
    seen: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        seen.append(argv)
        if argv[:2] == ("git", "status"):
            return 0, " M crates/core/source/core.rs\n"
        return 0, "running 1 test\ntest result: ok. 1 passed; 0 failed\n"

    result = asyncio.run(CargoTestRunner(capture=capture).run(path=str(tmp_path)))
    assert result.passed
    assert seen[1][:2] == ("cargo", "build")
    assert seen[1][2:] == ("-p", "app", "-p", "core")
    assert seen[2][:2] == ("cargo", "test")


def test_workspace_manifest_escalates_and_compile_red_is_not_green(tmp_path: Path) -> None:
    _workspace(tmp_path)
    seen: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        seen.append(argv)
        if argv[:2] == ("git", "status"):
            return 0, " M Cargo.toml\n"
        return 101, "error[E0425]: cannot find value"

    result = asyncio.run(CargoTestRunner(capture=capture).run(path=str(tmp_path)))
    assert not result.passed and result.returncode == 101
    assert seen[1] == ("cargo", "build", "--workspace")
    assert len(seen) == 2


def test_empty_test_run_is_reported_as_missing_coverage(tmp_path: Path) -> None:
    _workspace(tmp_path)

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        if argv[:2] == ("git", "status"):
            return 0, " M crates/app/src/main.rs\n"
        return 0, "running 0 tests\ntest result: ok. 0 passed; 0 failed\n"

    result = asyncio.run(CargoTestRunner(capture=capture).run(path=str(tmp_path)))
    assert not result.passed and "No Rust tests executed" in result.output


def test_manifest_change_updates_lock_before_fixed_test(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / "Cargo.lock").write_text("# existing lock\n", encoding="utf-8")
    seen: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        seen.append(argv)
        if argv[:2] == ("git", "status"):
            return 0, " M crates/core/Cargo.toml\n"
        return 0, "running 1 test\ntest result: ok. 1 passed\n"

    result = asyncio.run(CargoTestRunner(capture=capture).run(path=str(tmp_path)))
    assert result.passed
    assert "--locked" not in seen[1]
    assert seen[2][-1] == "--locked"


def test_preflight_respects_repository_clippy_warning_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path)
    (tmp_path / "Makefile").write_text(
        "lint:\n\tcargo clippy --workspace --all-targets -- -D warnings\n", encoding="utf-8"
    )
    monkeypatch.setattr("orchestrator.sdlc.rust_codegen.shutil.which", lambda name: "/usr/bin/true")
    seen: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        seen.append(argv)
        if argv[:2] == ("git", "status"):
            return 0, " M crates/core/source/core.rs\n"
        if argv[:2] == ("cargo", "clippy"):
            return 101, "error: pre-existing warning denied"
        return 0, ""

    result = asyncio.run(RustPreflightRunner(capture=capture).run(path=str(tmp_path)))
    assert not result.passed
    assert seen[-1][-3:] == ("--", "-D", "warnings")


def test_preflight_requires_clippy_when_repo_configures_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path)
    (tmp_path / "Makefile").write_text(
        "lint:\n\tcargo clippy --workspace --all-targets -- -D warnings\n", encoding="utf-8"
    )
    monkeypatch.setattr("orchestrator.sdlc.rust_codegen.shutil.which", lambda name: None)

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        if argv[:2] == ("git", "status"):
            return 0, " M crates/core/source/core.rs\n"
        return 0, ""

    result = asyncio.run(RustPreflightRunner(capture=capture).run(path=str(tmp_path)))
    assert not result.passed and "Clippy is configured" in result.output


def test_project_rejects_compiler_below_declared_msrv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path)
    layout = resolve_layout(tmp_path, language="rust", prefer_paths=["crates/core/source/core.rs"])
    monkeypatch.setattr(
        "orchestrator.sdlc.rust_codegen.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "rustc 1.89.0", ""),
    )
    assert "Rust 1.90 or newer" in (rust_project_error(tmp_path, layout) or "")


def test_changed_path_with_spaces_is_not_split(tmp_path: Path) -> None:
    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        assert "-z" in argv
        return 0, " M crates/core/source/new feature.rs\0"

    assert asyncio.run(_changed_paths(tmp_path, capture)) == [tmp_path / "crates/core/source/new feature.rs"]
