"""Shared PHP layout and changed-file discovery; no model or package installation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS
from orchestrator.pkg.walk_rules import walk_repo
from orchestrator.sdlc.process import ExecCapture, exec_capture


def safe_relative(root: Path, value: str) -> str:
    """Keep config-supplied paths inside the worktree, including symlink targets."""
    value = value.strip().replace("\\", "/")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or ":" in value:
        raise ValueError(f"PHP configuration path must stay inside the repository: {value!r}")
    if not (root / candidate).resolve().is_relative_to(root.resolve()):
        raise ValueError(f"PHP configuration path escapes the repository: {value!r}")
    return candidate.as_posix()


@dataclass(frozen=True)
class PhpUnitConfig:
    path: str | None = None
    tests_dir: str = "tests"
    suffix: str = "Test.php"
    bootstrap: str = ""


def read_phpunit_config(root: Path) -> PhpUnitConfig:
    for name in ("phpunit.xml", "phpunit.xml.dist"):
        file = root / name
        if not file.is_file():
            continue
        safe_relative(root, name)
        text = file.read_text(encoding="utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError(f"{name}: XML document types and entities are not supported")
        try:
            tree = ElementTree.fromstring(text)  # noqa: S314 — entities/DOCTYPE rejected above
        except ElementTree.ParseError as exc:
            raise ValueError(f"Invalid {name}: {exc}") from exc
        # Strip namespaces so both schema-qualified and historical configurations work.
        for node in tree.iter():
            node.tag = node.tag.rsplit("}", 1)[-1]
        if tree.tag != "phpunit":
            raise ValueError(f"{name}: expected a phpunit root element")
        directory = tree.find("./testsuites/testsuite/directory")
        if directory is None:
            directory = tree.find("./testsuite/directory")
        tests, suffix = "tests", "Test.php"
        if directory is not None:
            tests = safe_relative(root, directory.text or "tests")
            suffix = directory.get("suffix", "Test.php")
            if not suffix.endswith(".php") or any(c in suffix for c in "/\\\0"):
                raise ValueError(f"{name}: test suffix must be a PHP filename suffix")
        bootstrap = tree.get("bootstrap", "")
        if bootstrap:
            bootstrap = safe_relative(root, bootstrap)
        return PhpUnitConfig(name, tests, suffix, bootstrap)
    return PhpUnitConfig()


def read_composer(root: Path) -> dict[str, Any] | None:
    file = root / "composer.json"
    if not file.is_file():
        return None
    safe_relative(root, "composer.json")
    try:
        manifest = json.loads(file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Invalid composer.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("composer.json must contain an object")
    return manifest


def php_files(root: Path) -> Iterator[Path]:
    """The repository's ``.php`` files (Blade templates aside), walked the way the graph walks.

    A symlinked file is still refused here, whatever its target: each file is handed to
    ``php -l``, and a link is the same file twice at best.
    """
    for here, _dirs, files in walk_repo(root):
        for name in files:
            file = here / name
            if name.endswith(".php") and not name.endswith(".blade.php") and not file.is_symlink():
                yield file


async def changed_php_files(root: Path, *, capture: ExecCapture | None = None) -> list[str]:
    """Include staged, unstaged, renamed, and individual untracked files (NUL-safe)."""
    run_capture = capture or exec_capture

    rc, output = await run_capture(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=str(root),
        timeout=60,
    )
    if rc:
        raise RuntimeError(f"Cannot identify changed PHP files: {output.strip()}")
    records = iter(output.split("\0"))
    changed: set[str] = set()
    for record in records:
        if not record:
            continue
        status, name = record[:2], record[3:]
        if "R" in status or "C" in status:
            next(records, None)  # -z emits destination first, original second
        if any(part in DEFAULT_IGNORE_DIRS for part in Path(name).parts[:-1]):
            continue
        if not name.endswith(".php") or not (root / name).is_file():
            continue
        safe_relative(root, name)
        changed.add(name)
    return sorted(changed)
