"""The default extractor set is multi-language (Python always; Java/TS when available)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor, default_extractors
from orchestrator.pkg.facts import NodeKind


def test_default_always_includes_python() -> None:
    langs = {e.language for e in default_extractors()}
    assert "python" in langs


def test_default_includes_java_when_available() -> None:
    have_java = importlib.util.find_spec("tree_sitter_java") is not None
    langs = {e.language for e in default_extractors()}
    assert ("java" in langs) == have_java


def test_default_includes_typescript_when_available() -> None:
    have_ts = importlib.util.find_spec("tree_sitter_typescript") is not None
    langs = {e.language for e in default_extractors()}
    assert ("typescript" in langs) == have_ts


def test_default_includes_csharp_when_available() -> None:
    have_csharp = importlib.util.find_spec("tree_sitter_c_sharp") is not None
    langs = {e.language for e in default_extractors()}
    assert ("csharp" in langs) == have_csharp


def test_default_includes_c_when_available() -> None:
    have_c = importlib.util.find_spec("tree_sitter_c") is not None
    langs = {e.language for e in default_extractors()}
    assert ("c" in langs) == have_c


def test_default_includes_cpp_when_available() -> None:
    have_cpp = importlib.util.find_spec("tree_sitter_cpp") is not None
    langs = {e.language for e in default_extractors()}
    assert ("cpp" in langs) == have_cpp


def test_repo_extractor_default_handles_java(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    src = tmp_path / "src" / "main" / "java" / "com" / "demo"
    src.mkdir(parents=True)
    (src / "Widget.java").write_text(
        "package com.demo;\n\npublic class Widget {\n    public int score() { return 1; }\n}\n"
    )
    # Default RepoCodeExtractor (no explicit extractors) must now pick up .java.
    batch = RepoCodeExtractor().extract(tmp_path)
    types = {n.name for n in batch.nodes if n.kind is NodeKind.TYPE and n.language == "java"}
    assert "Widget" in types


def test_repo_extractor_default_handles_typescript(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "widget.ts").write_text("export class Widget {\n  score(): number { return 1; }\n}\n")
    # Default RepoCodeExtractor (no explicit extractors) must now pick up .ts.
    batch = RepoCodeExtractor().extract(tmp_path)
    types = {n.name for n in batch.nodes if n.kind is NodeKind.TYPE and n.language == "typescript"}
    assert "Widget" in types


def test_repo_extractor_default_handles_csharp(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "Widget.cs").write_text(
        "namespace Demo;\n\npublic class Widget {\n    public int Score() { return 1; }\n}\n"
    )
    # Default RepoCodeExtractor (no explicit extractors) must now pick up .cs.
    batch = RepoCodeExtractor().extract(tmp_path)
    types = {n.name for n in batch.nodes if n.kind is NodeKind.TYPE and n.language == "csharp"}
    assert "Widget" in types


def test_repo_extractor_default_handles_c(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")
    (tmp_path / "widget.c").write_text(
        "struct Widget { int id; };\nint widget_score(struct Widget *w) { return w->id; }\n"
    )
    # Default RepoCodeExtractor (no explicit extractors) must now pick up .c.
    batch = RepoCodeExtractor().extract(tmp_path)
    types = {n.name for n in batch.nodes if n.kind is NodeKind.TYPE and n.language == "c"}
    funcs = {n.name for n in batch.nodes if n.kind is NodeKind.FUNCTION and n.language == "c"}
    assert "Widget" in types and "widget_score" in funcs


def test_repo_extractor_default_handles_cpp(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_cpp", reason="install the 'cpp' extra")
    (tmp_path / "widget.cpp").write_text(
        "namespace app {\nclass Widget {\npublic:\n  int score() const;\n};\n}\n"
        "int app::Widget::score() const { return 1; }\n"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    types = {n.name for n in batch.nodes if n.kind is NodeKind.TYPE and n.language == "cpp"}
    assert "Widget" in types
