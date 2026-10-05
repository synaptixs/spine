"""Read Cargo package and target topology without invoking Cargo.

This index is deliberately static: comprehension must work on a machine without
the Rust toolchain, and must never execute a repository's build script.
"""

from __future__ import annotations

import glob
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CargoTarget:
    package: str
    kind: str
    name: str
    source: Path

    @property
    def scope(self) -> str:
        return f"rust:{self.package}@{self.kind}/{self.name}"


@dataclass
class CargoPackage:
    name: str
    directory: Path
    manifest: Path
    edition: str
    rust_version: str | None
    targets: list[CargoTarget] = field(default_factory=list)
    path_dependencies: dict[str, Path] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    default_features: tuple[str, ...] = ()


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _root_for(path: Path) -> Path | None:
    """Nearest manifest, with its outer workspace if this is a member checkout."""
    start = path if path.is_dir() else path.parent
    manifest = next((p / "Cargo.toml" for p in (start, *start.parents) if (p / "Cargo.toml").is_file()), None)
    if manifest is None:
        return None
    data = _read(manifest)
    package = _table(data.get("package"))
    explicit = package.get("workspace")
    if isinstance(explicit, str):
        candidate = (manifest.parent / explicit / "Cargo.toml").resolve()
        if candidate.is_file():
            return candidate.parent
    for parent in manifest.parent.parents:
        candidate = parent / "Cargo.toml"
        if candidate.is_file() and "workspace" in _read(candidate):
            return parent
    return manifest.parent


class CargoIndex:
    """Cargo packages, crate targets, and dependency aliases under one checkout."""

    def __init__(self, root: Path):
        self.root = root
        self.workspace_root = _root_for(root)
        self.packages: dict[str, CargoPackage] = {}
        self.workspace_members: tuple[str, ...] = ()
        self.default_members: tuple[str, ...] = ()
        self.workspace_features: dict[str, Any] = {}
        if self.workspace_root is not None:
            self._discover()

    def _discover(self) -> None:
        if self.workspace_root is None:
            return
        workspace_data = _read(self.workspace_root / "Cargo.toml")
        workspace = _table(workspace_data.get("workspace"))
        inherited = _table(workspace.get("package"))
        dependency_defaults = _table(workspace.get("dependencies"))
        excludes = self._expand(workspace.get("exclude", []))
        members = self._expand(workspace.get("members", [])) - excludes
        if "package" in workspace_data:
            members.add(self.workspace_root)
        if not workspace:
            members = {self.workspace_root}
        # Cargo admits local path dependencies of workspace packages as members unless
        # excluded. Repeat until transitive path dependencies stop adding packages.
        pending = list(sorted(members))
        seen: set[Path] = set()
        while pending:
            directory = pending.pop(0)
            if directory in seen or directory in excludes:
                continue
            seen.add(directory)
            data = _read(directory / "Cargo.toml")
            package_data = _table(data.get("package"))
            if not package_data or not isinstance(package_data.get("name"), str):
                continue
            name = package_data["name"]
            edition = self._inherit(package_data.get("edition"), inherited.get("edition"), "2015")
            rust_version = self._inherit(
                package_data.get("rust-version"), inherited.get("rust-version"), None
            )
            pkg = CargoPackage(name, directory, directory / "Cargo.toml", edition, rust_version)
            pkg.features = _table(data.get("features"))
            defaults = pkg.features.get("default", [])
            pkg.default_features = tuple(defaults) if isinstance(defaults, list) else ()
            pkg.targets = self._targets(pkg, data)
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                for alias, declaration in _table(data.get(section)).items():
                    spec = _table(declaration)
                    if spec.get("workspace") is True:
                        spec = _table(dependency_defaults.get(alias))
                    dep_path = spec.get("path")
                    if not isinstance(dep_path, str):
                        continue
                    dep_dir = (
                        (
                            self.workspace_root
                            if declaration != spec and _table(declaration).get("workspace")
                            else directory
                        )
                        / dep_path
                    ).resolve()
                    if not dep_dir.is_relative_to(self.workspace_root.resolve()):
                        continue
                    pkg.path_dependencies[alias] = dep_dir
                    if dep_dir not in seen and dep_dir not in excludes and (dep_dir / "Cargo.toml").is_file():
                        pending.append(dep_dir)
            self.packages[name] = pkg
        default_paths = self._expand(workspace.get("default-members", []))
        if "default-members" not in workspace:
            default_paths = {self.workspace_root} if "package" in workspace_data else set(members)
        self.default_members = tuple(
            sorted(p.name for p in self.packages.values() if p.directory in default_paths)
        )
        self.workspace_members = tuple(
            sorted(p.name for p in self.packages.values() if p.directory in members)
        )

    @staticmethod
    def _inherit(value: Any, inherited: Any, fallback: Any) -> Any:
        if isinstance(value, dict) and value.get("workspace") is True:
            return inherited if isinstance(inherited, str) else fallback
        return value if isinstance(value, str) else fallback

    def _expand(self, patterns: Any) -> set[Path]:
        if self.workspace_root is None or not isinstance(patterns, list):
            return set()
        found: set[Path] = set()
        for pattern in patterns:
            if not isinstance(pattern, str):
                continue
            for path in glob.glob(str(self.workspace_root / pattern)):
                candidate = Path(path).resolve()
                if (
                    candidate.is_relative_to(self.workspace_root.resolve())
                    and (candidate / "Cargo.toml").is_file()
                ):
                    found.add(candidate)
        return found

    @staticmethod
    def _targets(pkg: CargoPackage, data: dict[str, Any]) -> list[CargoTarget]:
        directory = pkg.directory
        package_data = _table(data.get("package"))
        targets: list[CargoTarget] = []

        def add(kind: str, name: str, source: str | Path) -> None:
            path = directory / source
            if path.is_file() and not any(
                t.kind == kind and (t.name == name or t.source == path) for t in targets
            ):
                targets.append(CargoTarget(pkg.name, kind, name, path))

        lib = _table(data.get("lib"))
        lib_name = str(lib.get("name", pkg.name.replace("-", "_")))
        add("lib", lib_name, str(lib.get("path", "src/lib.rs")))
        for spec in data.get("bin", []):
            if isinstance(spec, dict):
                name = spec.get("name")
                if isinstance(name, str):
                    add("bin", name, str(spec.get("path", f"src/bin/{name}.rs")))
        if package_data.get("autobins", True):
            add("bin", pkg.name, "src/main.rs")
            for path in sorted((directory / "src/bin").glob("*.rs")):
                add("bin", path.stem, path.relative_to(directory))
            for path in sorted((directory / "src/bin").glob("*/main.rs")):
                add("bin", path.parent.name, path.relative_to(directory))
        for kind, key, default_dir in (
            ("test", "test", "tests"),
            ("example", "example", "examples"),
            ("bench", "bench", "benches"),
        ):
            for spec in data.get(key, []):
                if isinstance(spec, dict) and isinstance(spec.get("name"), str):
                    name = spec["name"]
                    add(kind, name, str(spec.get("path", f"{default_dir}/{name}.rs")))
            if package_data.get(
                "autotests" if kind == "test" else "autoexamples" if kind == "example" else "autobenches",
                True,
            ):
                for path in sorted((directory / default_dir).glob("*.rs")):
                    add(kind, path.stem, path.relative_to(directory))
                for path in sorted((directory / default_dir).glob("*/main.rs")):
                    add(kind, path.parent.name, path.relative_to(directory))
        build = package_data.get("build", "build.rs")
        if isinstance(build, str):
            add("custom-build", "build-script-build", build)
        return targets

    @property
    def targets(self) -> tuple[CargoTarget, ...]:
        return tuple(t for p in self.packages.values() for t in p.targets)


__all__ = ["CargoIndex", "CargoPackage", "CargoTarget"]
