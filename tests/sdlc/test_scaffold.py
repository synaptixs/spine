"""Scaffolder: creates a runnable skeleton, idempotent, never clobbers."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.layout import TargetLayout
from orchestrator.sdlc.scaffold import scaffold

_LAYOUT = TargetLayout(
    package_name="example_service",
    source_dir="src/example_service",
    tests_dir="tests",
    src_layout=True,
    mode="new",
)


def test_scaffold_creates_runnable_skeleton(tmp_path: Path) -> None:
    created = scaffold(tmp_path, _LAYOUT)
    assert "src/example_service/__init__.py" in created
    assert "tests/__init__.py" in created
    assert "pyproject.toml" in created
    assert ".gitignore" in created

    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert 'name = "example_service"' in pyproject
    assert "pytest" in pyproject  # generated project is test-runnable
    assert 'pythonpath = ["src"]' in pyproject  # `import <package>` resolves


def test_scaffold_is_idempotent(tmp_path: Path) -> None:
    first = scaffold(tmp_path, _LAYOUT)
    assert first  # created files on the first pass
    second = scaffold(tmp_path, _LAYOUT)
    assert second == []  # nothing new the second time


def test_scaffold_never_clobbers_existing_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("# pre-existing, keep me\n")
    created = scaffold(tmp_path, _LAYOUT)
    assert "pyproject.toml" not in created
    assert (tmp_path / "pyproject.toml").read_text() == "# pre-existing, keep me\n"


_JAVA_LAYOUT = TargetLayout(
    package_name="org.example.widgets",
    source_dir="src/main/java/org/example/widgets",
    tests_dir="src/test/java/org/example/widgets",
    src_layout=True,
    mode="new",
    language="java",
    build_tool="maven",
)


def test_scaffold_java_maven_project(tmp_path: Path) -> None:
    created = scaffold(tmp_path, _JAVA_LAYOUT)
    assert "pom.xml" in created
    assert "src/main/java/org/example/widgets/.gitkeep" in created
    assert "src/test/java/org/example/widgets/.gitkeep" in created
    pom = (tmp_path / "pom.xml").read_text()
    assert "<groupId>org.example</groupId>" in pom
    assert "<artifactId>widgets</artifactId>" in pom
    assert "junit-jupiter" in pom  # JUnit 5 wired
    assert "target/" in (tmp_path / ".gitignore").read_text()  # Java .gitignore
    assert not (tmp_path / "pyproject.toml").exists()  # no Python files


def test_scaffold_java_is_idempotent(tmp_path: Path) -> None:
    scaffold(tmp_path, _JAVA_LAYOUT)
    assert scaffold(tmp_path, _JAVA_LAYOUT) == []


_TS_LAYOUT = TargetLayout(
    package_name="widgets",
    source_dir="src",
    tests_dir="src",
    src_layout=True,
    mode="new",
    language="typescript",
    build_tool="npm",
)


def test_scaffold_typescript_vitest_project(tmp_path: Path) -> None:
    import json

    created = scaffold(tmp_path, _TS_LAYOUT)
    assert "package.json" in created
    assert "tsconfig.json" in created
    assert "src/.gitkeep" in created
    pkg = json.loads((tmp_path / "package.json").read_text())  # valid JSON
    assert pkg["name"] == "widgets"
    assert pkg["type"] == "module"
    assert pkg["scripts"]["test"] == "vitest run"
    assert "vitest" in pkg["devDependencies"] and "typescript" in pkg["devDependencies"]
    assert '"strict": true' in (tmp_path / "tsconfig.json").read_text()
    assert "node_modules/" in (tmp_path / ".gitignore").read_text()  # TS .gitignore
    assert not (tmp_path / "pyproject.toml").exists()  # no Python files


def test_scaffold_typescript_is_idempotent(tmp_path: Path) -> None:
    scaffold(tmp_path, _TS_LAYOUT)
    assert scaffold(tmp_path, _TS_LAYOUT) == []


_CSHARP_LAYOUT = TargetLayout(
    package_name="Widgets",
    source_dir="src/Widgets",
    tests_dir="tests/Widgets.Tests",
    src_layout=True,
    mode="new",
    language="csharp",
    build_tool="dotnet",
)


def test_scaffold_csharp_solution_project(tmp_path: Path) -> None:
    created = scaffold(tmp_path, _CSHARP_LAYOUT)
    assert "Widgets.sln" in created
    assert "src/Widgets/Widgets.csproj" in created
    assert "tests/Widgets.Tests/Widgets.Tests.csproj" in created
    sln = (tmp_path / "Widgets.sln").read_text()
    assert "Microsoft Visual Studio Solution File" in sln
    assert "src\\Widgets\\Widgets.csproj" in sln  # backslash paths in the .sln
    assert "tests\\Widgets.Tests\\Widgets.Tests.csproj" in sln
    src_csproj = (tmp_path / "src" / "Widgets" / "Widgets.csproj").read_text()
    assert "<TargetFramework>net8.0</TargetFramework>" in src_csproj
    assert "<RootNamespace>Widgets</RootNamespace>" in src_csproj
    test_csproj = (tmp_path / "tests" / "Widgets.Tests" / "Widgets.Tests.csproj").read_text()
    assert "xunit" in test_csproj  # xUnit wired
    # the test project references the source project up two levels.
    assert 'Include="..\\..\\src\\Widgets\\Widgets.csproj"' in test_csproj
    assert "bin/" in (tmp_path / ".gitignore").read_text()  # C# .gitignore
    assert not (tmp_path / "pyproject.toml").exists()  # no Python files


def test_scaffold_csharp_guids_are_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    scaffold(a, _CSHARP_LAYOUT)
    scaffold(b, _CSHARP_LAYOUT)
    # Same project → byte-identical solution (deterministic uuid5 GUIDs).
    assert (a / "Widgets.sln").read_text() == (b / "Widgets.sln").read_text()


def test_scaffold_csharp_is_idempotent(tmp_path: Path) -> None:
    scaffold(tmp_path, _CSHARP_LAYOUT)
    assert scaffold(tmp_path, _CSHARP_LAYOUT) == []


def test_flat_layout_pythonpath(tmp_path: Path) -> None:
    flat = TargetLayout("pkg", "pkg", "tests", src_layout=False, mode="new")
    scaffold(tmp_path, flat)
    assert 'pythonpath = ["."]' in (tmp_path / "pyproject.toml").read_text()
    assert (tmp_path / "pkg" / "__init__.py").exists()
