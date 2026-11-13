"""PHP codegen contracts, including legacy layouts and false-green regressions."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from orchestrator.pkg.test_rule import is_test_path
from orchestrator.sdlc.codegen import _has_testable_source
from orchestrator.sdlc.feature_runner import _resolve_language, unsupported_language_error
from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.php import changed_php_files, read_phpunit_config
from orchestrator.sdlc.scaffold import scaffold
from orchestrator.sdlc.testenv import (
    PhpToolEnvironment,
    _ensure_phpunit_phar,
    make_test_environment,
    make_test_runner,
)
from orchestrator.sdlc.testrunner import PhpUnitTestRunner


def write(root: Path, name: str, body: str) -> Path:
    file = root / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body)
    return file


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.mark.parametrize("shape", ["empty", "composer", "xml", "loose", "vendor"])
def test_layout_shapes(tmp_path: Path, shape: str) -> None:
    if shape == "composer":
        write(tmp_path, "composer.json", json.dumps({"autoload": {"psr-4": {"Acme\\": ["lib/"]}}}))
    elif shape == "xml":
        write(
            tmp_path,
            "phpunit.xml",
            '<phpunit bootstrap="boot.php"><testsuites><testsuite>'
            '<directory suffix="Check.php">Tests/Unit</directory></testsuite></testsuites></phpunit>',
        )
    elif shape == "loose":
        write(tmp_path, "legacy/Foo.class.php", "<?php class Foo {}")
    elif shape == "vendor":
        write(tmp_path, "vendor/package/Foo.php", "<?php class Foo {}")
    layout = resolve_layout(tmp_path, language="php")
    assert layout.language == "php"
    assert layout.mode == ("new" if shape in ("empty", "vendor") else "existing")
    assert layout.build_tool == ("composer" if shape in ("empty", "vendor", "composer") else "phar")
    assert layout.module_rel_path("Foo").endswith("Foo.php")
    if shape == "composer":
        assert (layout.package_name, layout.source_dir) == ("Acme", "lib")
    if shape == "xml":
        assert (layout.tests_dir, layout.test_suffix, layout.test_bootstrap) == (
            "Tests/Unit",
            "Check.php",
            "boot.php",
        )


def test_scaffold_idempotent(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, language="php")
    assert set(scaffold(tmp_path, layout)) == {
        "composer.json",
        "phpunit.xml",
        "src/.gitkeep",
        "tests/.gitkeep",
        ".gitignore",
    }
    assert scaffold(tmp_path, layout) == []
    manifest = json.loads((tmp_path / "composer.json").read_text())
    assert manifest["autoload"]["psr-4"] == {"App\\": "src/"}
    assert manifest["require-dev"]["phpunit/phpunit"] == "^11"
    assert read_phpunit_config(tmp_path).bootstrap == "vendor/autoload.php"
    assert not (tmp_path / "pyproject.toml").exists()


def test_config_precedence_and_namespace(tmp_path: Path) -> None:
    write(
        tmp_path,
        "phpunit.xml.dist",
        '<phpunit xmlns="urn:test"><testsuites><testsuite>'
        "<directory>Tests</directory></testsuite></testsuites></phpunit>",
    )
    assert read_phpunit_config(tmp_path).tests_dir == "Tests"
    write(tmp_path, "phpunit.xml", "<phpunit><testsuite><directory>unit</directory></testsuite></phpunit>")
    assert read_phpunit_config(tmp_path).tests_dir == "unit"


@pytest.mark.parametrize(
    "xml",
    [
        "<phpunit>",
        '<!DOCTYPE x [<!ENTITY x "x">]><phpunit/>',
        "<other/>",
        '<phpunit bootstrap="../secret.php"/>',
        "<phpunit><testsuite><directory>/tmp/out</directory></testsuite></phpunit>",
        '<phpunit><testsuite><directory suffix="../Test.php">Tests</directory></testsuite></phpunit>',
    ],
)
def test_invalid_config_is_not_silently_ignored(tmp_path: Path, xml: str) -> None:
    write(tmp_path, "phpunit.xml", xml)
    with pytest.raises(ValueError):
        resolve_layout(tmp_path, language="php")


def test_symlink_config_path_rejected(tmp_path: Path) -> None:
    (tmp_path / "Tests").symlink_to(tmp_path.parent, target_is_directory=True)
    write(tmp_path, "phpunit.xml", "<phpunit><testsuite><directory>Tests</directory></testsuite></phpunit>")
    with pytest.raises(ValueError, match="escapes"):
        resolve_layout(tmp_path, language="php")


def test_dispatch_and_php_source_recognition(tmp_path: Path) -> None:
    write(tmp_path, "foo.php", "<?php class Foo {}")
    assert _resolve_language(tmp_path, "auto") == "php"
    assert unsupported_language_error("php") is None
    env = make_test_environment("php")
    assert isinstance(env, PhpToolEnvironment)
    assert isinstance(make_test_runner("php", env), PhpUnitTestRunner)
    assert _has_testable_source([tmp_path / "foo.php"])
    assert is_test_path("Tests/FooTest.php")


async def test_changed_paths_include_nested_untracked_and_renames(tmp_path: Path) -> None:
    git(tmp_path, "init")
    write(tmp_path, "old name.php", "<?php // old")
    write(tmp_path, "gone.php", "<?php // gone")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    git(tmp_path, "mv", "old name.php", "new name.php")
    (tmp_path / "gone.php").unlink()
    write(tmp_path, 'Tests/odd "file.php', "<?php // test")
    assert await changed_php_files(tmp_path) == ['Tests/odd "file.php', "new name.php"]


async def test_runner_lints_then_targets_each_changed_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(tmp_path, "init")
    write(
        tmp_path,
        "phpunit.xml",
        '<phpunit><testsuite><directory suffix="Check.php">Tests</directory></testsuite></phpunit>',
    )
    write(tmp_path, "Tests/OldCheck.php", "<?php invalid legacy test")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    write(tmp_path, "src/A.php", "<?php class A {}")
    write(tmp_path, "Tests/ACheck.php", "<?php // test")
    write(tmp_path, "Tests/BCheck.php", "<?php // test")
    write(tmp_path, "vendor/bin/phpunit", "<?php // runner")
    write(tmp_path, "vendor/dependency/Invalid.php", "<?php invalid vendor source")
    from orchestrator.sdlc import testrunner

    real = testrunner._exec_capture
    calls: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        if argv[0] == "git":
            return await real(argv, cwd=cwd, timeout=timeout)
        calls.append(argv)
        return 0, "OK (1 test, 1 assertion)"

    monkeypatch.setattr(testrunner, "_exec_capture", capture)
    result = await PhpUnitTestRunner().run(path=str(tmp_path))
    assert result.passed
    assert len([c for c in calls if c[1] == "-l"]) == 3
    runs = [c for c in calls if c[1] != "-l"]
    assert [Path(c[-1]).name for c in runs] == ["ACheck.php", "BCheck.php"]
    assert all("--configuration" in c for c in runs)
    assert calls[:3] == [c for c in calls if c[1] == "-l"]


@pytest.mark.parametrize("failure", ["no_changes", "lint", "empty_suite", "assertion", "missing_php"])
async def test_runner_never_false_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    git(tmp_path, "init")
    if failure != "no_changes":
        write(tmp_path, "Tests/FooTest.php", "<?php // test")
    from orchestrator.sdlc import testrunner

    real = testrunner._exec_capture

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        if argv[0] == "git":
            return await real(argv, cwd=cwd, timeout=timeout)
        if failure == "missing_php":
            raise FileNotFoundError("php")
        if argv[1] == "-l":
            return (255, "Parse error") if failure == "lint" else (0, "No syntax errors")
        assert failure in ("empty_suite", "assertion")
        return (0, "No tests executed!") if failure == "empty_suite" else (1, "Failed asserting")

    monkeypatch.setattr(testrunner, "_exec_capture", capture)
    result = await PhpUnitTestRunner(phpunit="/outside/phpunit.phar").run(path=str(tmp_path))
    assert not result.passed
    assert result.returncode != 0


@pytest.mark.parametrize("version,release", [("7.4", "9.6.36"), ("8.1", "9.6.36"), ("8.3", "11.5.56")])
async def test_environment_selects_compatible_phar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, release: str
) -> None:
    monkeypatch.setattr(
        "orchestrator.sdlc.testrunner._exec_capture", AsyncMock(return_value=(0, f"PHP {version}.0"))
    )
    downloads: list[Path] = []
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv._ensure_phpunit_phar", lambda dest, *_: downloads.append(dest)
    )
    env = PhpToolEnvironment()
    await env.ensure(tmp_path)
    assert downloads == [tmp_path.parent / f".sdlc-phpunit-{release}.phar"]
    assert version in env.describe()
    assert not await env.install(["anything"])


async def test_environment_honors_php_version_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, ".php-version", "7.4")
    monkeypatch.setattr(
        "orchestrator.sdlc.testrunner._exec_capture", AsyncMock(return_value=(0, "PHP 8.3.0"))
    )
    with pytest.raises(RuntimeError, match="requests PHP 7.4"):
        await PhpToolEnvironment().ensure(tmp_path)


async def test_composer_install_and_missing_phpunit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, "composer.json", "{}")
    monkeypatch.setattr("shutil.which", lambda name: name)
    capture = AsyncMock(side_effect=[(0, "PHP 8.3.0"), (1, "install failed"), (1, "install failed")])
    monkeypatch.setattr("orchestrator.sdlc.testrunner._exec_capture", capture)
    with pytest.raises(RuntimeError, match="require-dev"):
        await PhpToolEnvironment().ensure(tmp_path)
    write(tmp_path, "vendor/bin/phpunit", "<?php")
    write(tmp_path, "vendor/autoload.php", "<?php")
    capture.side_effect = [(0, "PHP 8.3.0"), (0, "installed")]
    env = PhpToolEnvironment()
    await env.ensure(tmp_path)
    assert env.phpunit == str(tmp_path / "vendor/bin/phpunit")
    assert capture.call_args.args[0][1:] == ("install", "--no-interaction", "--prefer-dist")


def test_phar_checksum_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"verified test payload"
    digest = hashlib.sha256(data).hexdigest()
    calls: list[str] = []

    def download(url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, content=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", download)
    destination = tmp_path / "phpunit.phar"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _ensure_phpunit_phar(destination, "test", "wrong")
    assert not destination.exists()
    _ensure_phpunit_phar(destination, "test", digest)
    _ensure_phpunit_phar(destination, "test", digest)
    assert len(calls) == 2
    destination.write_bytes(b"corrupt")
    _ensure_phpunit_phar(destination, "test", digest)
    assert destination.read_bytes() == data


@pytest.mark.skipif(
    not shutil.which("php") or not shutil.which("composer"), reason="PHP/Composer unavailable"
)
async def test_real_composer_and_phar(tmp_path: Path) -> None:
    for composer in (True, False):
        repo = tmp_path / ("composer" if composer else "phar")
        repo.mkdir()
        git(repo, "init")
        if composer:
            scaffold(repo, resolve_layout(repo, language="php"))
        write(repo, "src/Add.php", "<?php function add($a, $b) { return $a + $b; }")
        write(
            repo,
            "tests/AddTest.php",
            "<?php\nrequire_once __DIR__ . '/../src/Add.php';\n"
            "class AddTest extends PHPUnit\\Framework\\TestCase { public function testAdd(): void {"
            "$this->assertSame(5, add(2, 3)); }}",
        )
        env = PhpToolEnvironment()
        await env.ensure(repo)
        runner = make_test_runner("php", env)
        if composer:
            (repo / ".gitignore").unlink()  # dependency trees stay excluded during change-removal probes
        result = await runner.run(path=str(repo))
        assert result.passed, env.describe() + "\n" + result.output
        write(repo, "src/Add.php", "<?php function add($a, $b) { return $a - $b; }")
        assert not (await runner.run(path=str(repo))).passed

        write(
            repo,
            "tests/AddTest.php",
            "<?php class AddTest extends PHPUnit\\Framework\\TestCase {"
            "public function testSkipped(): void { $this->markTestSkipped('not implemented'); }}",
        )
        assert not (await runner.run(path=str(repo))).passed
