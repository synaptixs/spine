"""``bin/`` is skipped only where it is .NET build output (B63, SSPN-144).

``bin`` sat in ``DEFAULT_IGNORE_DIRS`` with the comment ".NET build output", so every ``bin/`` in every
language was skipped, silently: sympy's ``bin/*.py`` (12 files) and django's ``django-admin.py`` produced no
nodes, no ``blast_radius`` and no warning. A .NET ``bin/`` — beside a project or solution file — holds
compiled output and copied static assets, and is still skipped.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.extractor import (
    DEFAULT_IGNORE_DIRS,
    PKG_IGNORE_DIRS,
    RepoCodeExtractor,
    is_dotnet_output_dir,
)

_PY = "def run():\n    return 1\n"


def _write(root: Path, rel: str, text: str = _PY) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _files(root: Path) -> set[str]:
    batch = RepoCodeExtractor().extract(root)
    return {n.provenance.file for n in batch.nodes if n.provenance}


def test_a_python_bin_directory_is_extracted(tmp_path: Path) -> None:
    _write(tmp_path, "bin/tool.py")
    _write(tmp_path, "main.py")
    assert _files(tmp_path) == {"bin/tool.py", "main.py"}


def test_a_dotnet_bin_beside_a_project_file_is_still_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "App/App.csproj", "<Project />\n")
    _write(tmp_path, "App/Program.cs", "class P { static void M() { } }\n")
    _write(tmp_path, "App/bin/Release/publish/wwwroot/site.js", "function f() { return 1; }\n")
    files = _files(tmp_path)
    assert "App/Program.cs" in files
    assert not any("/bin/" in f for f in files), "compiled output and copied assets are not source"


def test_a_dotnet_bin_beside_a_solution_file_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "Shop.sln", "Microsoft Visual Studio Solution File\n")
    _write(tmp_path, "bin/Debug/helper.py")
    _write(tmp_path, "main.py")
    assert _files(tmp_path) == {"main.py"}


def test_a_bin_elsewhere_in_a_dotnet_repo_is_still_walked(tmp_path: Path) -> None:
    """The rule is about the directory's own neighbours, not the repository's language."""
    _write(tmp_path, "App/App.csproj", "<Project />\n")
    _write(tmp_path, "scripts/bin/deploy.py")
    assert "scripts/bin/deploy.py" in _files(tmp_path)


def test_the_rule_is_named_by_what_sits_beside_the_directory(tmp_path: Path) -> None:
    _write(tmp_path, "a/bin/x.py")
    _write(tmp_path, "b/B.fsproj", "<Project />\n")
    _write(tmp_path, "b/bin/y.py")
    assert not is_dotnet_output_dir(tmp_path / "a", "bin")
    assert is_dotnet_output_dir(tmp_path / "b", "bin")
    assert not is_dotnet_output_dir(tmp_path / "b", "lib"), "only bin"


def test_the_shared_default_set_keeps_bin_for_every_other_walker(tmp_path: Path) -> None:
    """Layout, language detection and the codegen walkers (11 other files) are deliberately unchanged."""
    assert "bin" in DEFAULT_IGNORE_DIRS
    assert "bin" not in PKG_IGNORE_DIRS
    assert DEFAULT_IGNORE_DIRS - {"bin"} == PKG_IGNORE_DIRS


def test_the_doc_walker_follows_the_same_rule(tmp_path: Path) -> None:
    _write(tmp_path, "bin/NOTES.md", "# Tools\n\nThe `run` helper lives here.\n")
    assert [p.title for p in read_doc_pages(tmp_path)] == ["bin/NOTES.md#tools"]
    dotnet = tmp_path / "dn"
    _write(dotnet, "App.csproj", "<Project />\n")
    _write(dotnet, "bin/NOTES.md", "# Output\n\nBuild output.\n")
    assert read_doc_pages(dotnet) == []


def test_a_c_include_under_a_python_style_bin_is_admitted(tmp_path: Path) -> None:
    from orchestrator.pkg.clang_includes import infer_include_roots

    _write(tmp_path, "bin/util.h", "int util(void);\n")
    _write(tmp_path, "src/main.c", '#include "util.h"\nint main(void) { return util(); }\n')
    roots = infer_include_roots(tmp_path, {"bin/util.h", "src/main.c"}, [])
    assert "bin" in roots


def test_ignored_directories_are_recorded_by_name_and_count(tmp_path: Path) -> None:
    _write(tmp_path, "build/gen.py")
    _write(tmp_path, "vendor/lib/x.py")
    _write(tmp_path, "pkg/build/y.py")
    _write(tmp_path, "App/App.csproj", "<Project />\n")
    _write(tmp_path, "App/bin/z.py")
    _write(tmp_path, "bin/kept.py")  # a walked bin is not "ignored"
    _write(tmp_path, ".git/config.py")  # dot-prefixed directories are not interesting
    extractor = RepoCodeExtractor()
    extractor.extract(tmp_path)
    assert extractor.ignored_dirs_seen == {"bin": 1, "build": 2, "vendor": 1}


def test_pkg_extract_names_the_directories_it_did_not_enter(tmp_path: Path) -> None:
    _write(tmp_path, "build/gen.py")
    _write(tmp_path, "main.py")
    result = CliRunner().invoke(app, ["pkg", "extract", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "ignored directories: build (1)" in result.output


def test_pkg_extract_says_nothing_when_it_ignored_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "main.py")
    result = CliRunner().invoke(app, ["pkg", "extract", str(tmp_path)])
    assert "ignored directories" not in result.output
