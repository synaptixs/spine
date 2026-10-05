"""Cargo-aware Rust codegen layout and governed build/test adapters.

The static Cargo index is shared with comprehension. Cargo is invoked only by
the codegen verification adapters, after the governed worktree exists.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import deque
from collections.abc import Sequence
from pathlib import Path

from orchestrator.pkg.rust_cargo import CargoIndex, CargoPackage, CargoTarget
from orchestrator.sdlc.contracts import Baseline, PreflightResult, TestRunResult
from orchestrator.sdlc.layout import TargetLayout, derive_package_name
from orchestrator.sdlc.process import ExecCapture, exec_capture

_SKIP_DIRS = frozenset({".git", "target", "node_modules", ".venv", "vendor"})
_TIMEOUT = 600.0


def _diagnostic_output(parts: Sequence[str]) -> str:
    text = "\n".join(parts)
    if len(text) <= 8000:
        return text
    # A large failing suite lists hundreds of failing names before its summary.
    # Keep the first diagnostic even when it is in the middle of the output.
    marker = re.search(r"(?m)^---- .* stdout ----|^error(?:\[|:)|^thread .* panicked", text)
    middle = text[max(0, marker.start() - 200) : marker.start() + 1800] if marker else ""
    return text[:1200] + "\n...\n" + middle + "\n... output truncated ...\n" + text[-4500:]


def _strict_clippy(workspace: Path) -> bool:
    """Respect an explicit repository `-D warnings` Clippy contract."""
    candidates = [workspace / "Makefile"]
    workflows = workspace / ".github" / "workflows"
    if workflows.is_dir():
        candidates.extend(p for p in workflows.iterdir() if p.suffix in {".yml", ".yaml"})
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"cargo\s+clippy[^\n]*-D\s+warnings", text):
            return True
    return False


def cargo_toolchain_available() -> bool:
    """Cargo, compiler and formatter are needed for a usable codegen loop."""
    return all(shutil.which(command) for command in ("cargo", "rustc", "rustfmt"))


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _workspaces(root: Path) -> list[CargoIndex]:
    manifests: list[Path] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        if "Cargo.toml" in files:
            manifests.append(Path(directory) / "Cargo.toml")
    indexes: dict[Path, CargoIndex] = {}
    for manifest in manifests:
        index = CargoIndex(manifest.parent)
        if index.workspace_root is not None:
            indexes[index.workspace_root.resolve()] = index
    return list(indexes.values())


def _score_target(
    root: Path, package: CargoPackage, target: CargoTarget, preferred: Sequence[Path]
) -> tuple[int, int, int, str]:
    source_dir = target.source.parent.resolve()
    package_dir = package.directory.resolve()
    score = 0
    for path in preferred:
        if path == target.source.resolve():
            score += 1000
        elif path.is_relative_to(source_dir):
            score += 300 + len(source_dir.parts)
        elif path.is_relative_to(package_dir):
            score += 100 + len(package_dir.parts)
    # With no design path, prefer the package's library over a binary or example.
    kind = 10 if target.kind in {"lib", "proc-macro"} else 0
    return score, kind, -len(target.source.parts), _relative(target.source, root)


def resolve_rust_layout(
    root: Path,
    *,
    mode: str,
    package_name: str | None,
    repo: str | None,
    prefer_paths: Sequence[str] = (),
) -> TargetLayout:
    """Select the Cargo package and target that contain the intended landing site."""
    root = root.resolve()
    preferred = [
        (root / path).resolve() for path in prefer_paths if (root / path).resolve().is_relative_to(root)
    ]
    indexes = [] if mode == "new" else _workspaces(root)
    candidates: list[tuple[CargoIndex, CargoPackage, CargoTarget]] = [
        (index, package, target)
        for index in indexes
        for package in index.packages.values()
        for target in package.targets
    ]
    if candidates and mode != "new":
        # Explicit package_name narrows a multi-package workspace; otherwise the
        # design path identifies ownership, including custom [lib]/[[bin]] paths.
        named = [item for item in candidates if item[1].name == package_name]
        if package_name and not named:
            raise ValueError(f"Cargo package {package_name!r} was not found in this repository")
        if named:
            candidates = named
        elif preferred:
            path_matches = [
                item for item in candidates if _score_target(root, item[1], item[2], preferred)[0]
            ]
            if path_matches:
                candidates = path_matches
        if not named and len({item[1].name for item in candidates}) > 1:
            # Cargo's default-members are the natural landing site when a spec
            # names no package. Multiple defaults still need an explicit path.
            defaults = {name for index in indexes for name in index.default_members}
            default_candidates = [item for item in candidates if item[1].name in defaults]
            if len({item[1].name for item in default_candidates}) == 1:
                candidates = default_candidates
            else:
                raise ValueError(
                    "Rust codegen needs a package name or source path in this multi-package workspace"
                )
        index, package, target = max(
            candidates,
            key=lambda item: _score_target(root, item[1], item[2], preferred),
        )
        workspace = index.workspace_root or package.directory
        toolchain = next(
            (p for p in (workspace / "rust-toolchain.toml", workspace / "rust-toolchain") if p.is_file()),
            None,
        )
        source_dir = _relative(target.source.parent, root)
        integration = package.directory / "tests"
        tests_dir = _relative(integration, root) if integration.is_dir() else source_dir
        return TargetLayout(
            package_name=package.name,
            source_dir=source_dir,
            tests_dir=tests_dir,
            src_layout=False,
            mode="existing",
            language="rust",
            build_tool="cargo",
            project_dir=_relative(workspace, root),
            workspace_root=_relative(workspace, root),
            package_root=_relative(package.directory, root),
            target_kind=target.kind,
            target_name=target.name,
            target_source=_relative(target.source, root),
            edition=package.edition,
            rust_version=package.rust_version or "",
            cargo_lock=(workspace / "Cargo.lock").is_file(),
            toolchain_file=_relative(toolchain, root) if toolchain else "",
            chosen_reason="design path and Cargo target ownership" if preferred else "Cargo target",
        )
    if mode == "existing":
        raise ValueError("Rust codegen needs a Cargo.toml in the existing repository")
    name = re.sub(r"[^a-z0-9_-]+", "-", (package_name or derive_package_name(repo or root.name)).lower())
    name = name.strip("-_") or "app"
    if name[0].isdigit():
        name = f"app-{name}"
    binary = any(Path(path).name == "main.rs" or "bin" in Path(path).parts for path in prefer_paths)
    kind = "bin" if binary else "lib"
    return TargetLayout(
        package_name=name,
        source_dir="src",
        tests_dir="src",
        src_layout=False,
        mode="new",
        language="rust",
        build_tool="cargo",
        target_kind=kind,
        target_name=name,
        target_source=f"src/{'main' if binary else 'lib'}.rs",
        edition="2024",
    )


def rust_files(layout: TargetLayout) -> dict[str, str]:
    """Minimal single-crate scaffold; the generic scaffolder writes missing files only."""
    name = layout.package_name
    source = "src/main.rs" if layout.target_kind == "bin" else "src/lib.rs"
    body = "fn main() {}\n" if layout.target_kind == "bin" else "//! New Rust crate.\n"
    return {
        "Cargo.toml": f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n',
        source: body,
        "README.md": f"# {name}\n\nRun `cargo test` to verify the crate.\n",
        ".gitignore": "/target/\n",
    }


class CargoToolEnvironment:
    """Cargo owns dependencies and lockfile updates; no implicit fetch or pip heal."""

    declared: set[str] = set()

    def __init__(self, *, project_dir: str = "") -> None:
        self.project_dir = project_dir

    @property
    def python(self) -> str:
        raise RuntimeError("CargoToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False

    def describe(self) -> str:
        return "Cargo toolchain (dependencies resolved by cargo build)"


async def _changed_paths(root: Path, capture: ExecCapture) -> list[Path]:
    rc, output = await capture(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd=str(root), timeout=60.0
    )
    if rc:
        return []
    paths: list[Path] = []
    # `-z` keeps paths with spaces/newlines literal and puts the destination first
    # for renames; the next NUL record is the old path and is not verified.
    records = output.split("\0") if "\0" in output else output.splitlines()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        name = record[3:].split(" -> ")[-1].strip('"')
        if "\0" in output and any(flag in record[:2] for flag in "RC"):
            index += 1
        path = (root / name).resolve()
        if path.is_relative_to(root.resolve()):
            paths.append(path)
    return paths


def _scope(root: Path, index: CargoIndex, changed: Sequence[Path]) -> tuple[bool, tuple[str, ...]]:
    """Workspace-wide for shared configuration or ambiguous ownership."""
    workspace = index.workspace_root or root
    if not changed:
        return True, ()  # baseline or a clean checkout
    owners: set[str] = set()
    for path in changed:
        if not path.is_relative_to(workspace):
            continue
        rel = path.relative_to(workspace)
        if rel.as_posix() in {
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain",
            "rust-toolchain.toml",
        } or rel.parts[:1] == (".cargo",):
            return True, ()
        matching = [p for p in index.packages.values() if path.is_relative_to(p.directory)]
        if len(matching) != 1:
            return True, ()
        owners.add(matching[0].name)
    if not owners:
        return True, ()
    reverse: dict[str, set[str]] = {name: set() for name in index.packages}
    by_dir = {p.directory.resolve(): p.name for p in index.packages.values()}
    for package in index.packages.values():
        for dep in package.path_dependencies.values():
            dependency = by_dir.get(dep.resolve())
            if dependency:
                reverse[dependency].add(package.name)
    pending = deque(owners)
    while pending:
        for dependent in reverse[pending.popleft()] - owners:
            owners.add(dependent)
            pending.append(dependent)
    return False, tuple(sorted(owners))


async def _verification(
    root: Path, project_dir: str, capture: ExecCapture
) -> tuple[Path, tuple[str, ...], bool]:
    workspace = (root / project_dir).resolve()
    index = CargoIndex(workspace)
    if index.workspace_root is None or not index.packages:
        raise ValueError(f"No Cargo package found in {workspace}")
    changed = await _changed_paths(root, capture)
    entire, packages = _scope(root, index, changed)
    manifest_changed = any(p.name == "Cargo.toml" for p in changed)
    locked = not manifest_changed
    selection = ("--workspace",) if entire else tuple(arg for name in packages for arg in ("-p", name))
    return workspace, selection, locked


class CargoTestRunner:
    """Build then test the changed package and all reverse workspace dependents."""

    def __init__(
        self,
        cargo: str = "cargo",
        *,
        project_dir: str = "",
        timeout: float = _TIMEOUT,
        capture: ExecCapture | None = None,
    ) -> None:
        self._cargo = cargo
        self._project_dir = project_dir
        self._timeout = timeout
        self._capture = capture or exec_capture

    async def run(self, *, path: str) -> TestRunResult:
        try:
            workspace, selection, locked = await _verification(
                Path(path).resolve(), self._project_dir, self._capture
            )
            output: list[str] = []
            for phase in ("build", "test"):
                lock = (
                    ("--locked",)
                    if (workspace / "Cargo.lock").is_file() and (locked or phase == "test")
                    else ()
                )
                argv = (self._cargo, phase, *selection, *lock)
                rc, text = await self._capture(argv, cwd=str(workspace), timeout=self._timeout)
                output.append(f"$ {' '.join(argv)}\n{text}")
                if rc:
                    return TestRunResult(False, rc, _diagnostic_output(output))
            test_output = output[-1]
            counts = [int(n) for n in re.findall(r"running (\d+) tests?", test_output)]
            if counts and not any(counts):
                return TestRunResult(False, 5, _diagnostic_output(output) + "\nNo Rust tests executed")
            return TestRunResult(True, 0, _diagnostic_output(output))
        except (OSError, ValueError, RuntimeError) as exc:
            return TestRunResult(False, 2, f"Cargo verification failed: {exc}")


class RustPreflightRunner:
    """Cargo fmt plus Clippy, with an explicit skip only when Clippy is optional."""

    def __init__(self, cargo: str = "cargo", *, capture: ExecCapture | None = None) -> None:
        self._cargo = cargo
        self._capture = capture or exec_capture

    async def run(self, *, path: str, baseline: Baseline | None = None) -> PreflightResult:
        _ = baseline
        root = Path(path).resolve()
        try:
            workspaces = _workspaces(root)
            if not workspaces:
                return PreflightResult(False, "Rust preflight needs Cargo.toml")
            changed = await _changed_paths(root, self._capture)
            active = [
                index
                for index in workspaces
                if any(path.is_relative_to(index.workspace_root or root) for path in changed)
            ]
            if active:
                workspaces = active
            notes: list[str] = []
            for index in workspaces:
                workspace = index.workspace_root or root
                entire, packages = _scope(root, index, changed)
                selection = ("--all",) if entire else tuple(arg for name in packages for arg in ("-p", name))
                fmt = (self._cargo, "fmt", *selection, "--", "--check")
                rc, out = await self._capture(fmt, cwd=str(workspace), timeout=_TIMEOUT)
                notes.append(f"$ {' '.join(fmt)}\n{out}")
                if rc:
                    return PreflightResult(False, "\n".join(notes)[-8000:])
                if shutil.which("cargo-clippy") is None:
                    manifest = (workspace / "Cargo.toml").read_text(encoding="utf-8")
                    configured = (
                        any((workspace / name).exists() for name in ("clippy.toml", ".clippy.toml"))
                        or "lints.clippy" in manifest
                        or _strict_clippy(workspace)
                        or any(
                            "lints.clippy" in package.manifest.read_text(encoding="utf-8")
                            for package in index.packages.values()
                        )
                    )
                    if configured:
                        return PreflightResult(False, "Clippy is configured but cargo-clippy is unavailable")
                    notes.append("Clippy unavailable; optional check skipped")
                    continue
                clippy_scope = ("--workspace",) if entire else selection
                strict = ("--", "-D", "warnings") if _strict_clippy(workspace) else ()
                clippy = (self._cargo, "clippy", *clippy_scope, "--all-targets", *strict)
                rc, out = await self._capture(clippy, cwd=str(workspace), timeout=_TIMEOUT)
                notes.append(f"$ {' '.join(clippy)}\n{out}")
                if rc:
                    return PreflightResult(False, "\n".join(notes)[-8000:])
            return PreflightResult(True, "\n".join(notes)[-8000:])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return PreflightResult(False, f"Rust preflight failed: {exc}")


def rust_project_error(root: Path, layout: TargetLayout) -> str | None:
    """Check the selected compiler against Cargo's declared MSRV in this checkout."""
    workspace = root / layout.workspace_root
    try:
        result = subprocess.run(
            ("rustc", "--version"),
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Rust compiler unavailable for this repository: {exc}"
    if result.returncode:
        return f"Rust compiler unavailable for this repository: {result.stderr.strip()}"
    if layout.rust_version:
        found = re.search(r"rustc (\d+)\.(\d+)\.(\d+)", result.stdout)
        required = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", layout.rust_version)
        if found and required:
            selected = tuple(map(int, found.groups()))
            minimum = (int(required[1]), int(required[2]), int(required[3] or 0))
            if selected < minimum:
                return (
                    f"Rust {layout.rust_version} or newer is required by Cargo.toml; "
                    f"selected compiler is {'.'.join(map(str, selected))}."
                )
    if layout.mode == "existing":
        try:
            metadata = subprocess.run(
                ("cargo", "metadata", "--format-version", "1", "--no-deps"),
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Cargo metadata unavailable for this repository: {exc}"
        if metadata.returncode:
            return f"Cargo metadata failed: {metadata.stderr.strip()[-1000:]}"
        try:
            packages = json.loads(metadata.stdout)["packages"]
            selected_target = (root / layout.target_source).resolve()
            match = any(
                package["name"] == layout.package_name
                and any(
                    target["name"] == layout.target_name
                    and selected_target == Path(target["src_path"]).resolve()
                    for target in package["targets"]
                )
                for package in packages
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return f"Cargo metadata is unreadable: {exc}"
        if not match:
            return (
                f"Cargo metadata does not confirm package {layout.package_name}, target "
                f"{layout.target_name} at {layout.target_source}; refresh the layout."
            )
    return None
