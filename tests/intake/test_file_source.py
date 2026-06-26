"""Tests for the local-file source adapter (``file://``).

The credential-free ``SourceAdapter``: a single file becomes one document, a
directory is walked breadth-first (capped, dotfiles + non-text + empty files
skipped), and an optional ``FILE_SOURCE_ROOT`` sandbox confines reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake.file_source import (
    FileSourceAdapter,
    FileSourceConfig,
    FileSourceError,
)


async def test_single_file_root_yields_one_document(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Login\n\nUsers can sign in.\n", encoding="utf-8")
    result = await FileSourceAdapter().fetch_tree(str(spec))
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.title == "Login"  # H1
    assert "Users can sign in." in doc.body
    assert doc.url.startswith("file://")


async def test_title_falls_back_to_filename_without_h1(tmp_path: Path) -> None:
    spec = tmp_path / "requirements.md"
    spec.write_text("Just prose, no heading.\n", encoding="utf-8")
    doc = await FileSourceAdapter().fetch_document(str(spec))
    assert doc.title == "requirements"


async def test_directory_walk_collects_text_skips_noise(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\nbody a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("body b\n", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")  # non-text → skipped
    (tmp_path / "empty.md").write_text("   \n", encoding="utf-8")  # empty → skipped
    (tmp_path / ".hidden.md").write_text("# secret\n", encoding="utf-8")  # dotfile → skipped
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.md").write_text("# C\nbody c\n", encoding="utf-8")

    result = await FileSourceAdapter().fetch_tree(str(tmp_path))
    titles = {d.title for d in result.documents}
    assert titles == {"A", "b", "C"}  # md H1, txt stem, nested H1; png/empty/hidden dropped
    assert not result.truncated


async def test_max_docs_truncates(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"doc{i}.md").write_text(f"# D{i}\nbody\n", encoding="utf-8")
    result = await FileSourceAdapter().fetch_tree(str(tmp_path), max_docs=2)
    assert len(result.documents) == 2
    assert result.truncated


async def test_max_depth_stops_descent(tmp_path: Path) -> None:
    (tmp_path / "top.md").write_text("# Top\nx\n", encoding="utf-8")
    deep = tmp_path / "l1" / "l2"
    deep.mkdir(parents=True)
    (deep / "deep.md").write_text("# Deep\nx\n", encoding="utf-8")
    # depth 1 descends into l1 (depth 0→1) but not into l2 (would be depth 2).
    result = await FileSourceAdapter().fetch_tree(str(tmp_path), max_depth=1)
    titles = {d.title for d in result.documents}
    assert "Top" in titles and "Deep" not in titles


async def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileSourceError):
        await FileSourceAdapter().fetch_tree(str(tmp_path / "nope.md"))


async def test_oversized_file_skipped_via_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.intake.file_source as mod

    monkeypatch.setattr(mod, "_MAX_FILE_BYTES", 4)
    big = tmp_path / "big.md"
    big.write_text("# way too long\n", encoding="utf-8")
    with pytest.raises(FileSourceError, match="cap"):
        await FileSourceAdapter().fetch_document(str(big))


async def test_sandbox_confines_reads(tmp_path: Path) -> None:
    sandbox = tmp_path / "allowed"
    sandbox.mkdir()
    (sandbox / "ok.md").write_text("# OK\nx\n", encoding="utf-8")
    outside = tmp_path / "secret.md"
    outside.write_text("# secret\n", encoding="utf-8")

    adapter = FileSourceAdapter(FileSourceConfig(root=str(sandbox)))
    # A relative path resolves under the sandbox.
    doc = await adapter.fetch_document("ok.md")
    assert doc.title == "OK"
    # An absolute path outside the sandbox is refused.
    with pytest.raises(FileSourceError, match="sandbox"):
        await adapter.fetch_document(str(outside))


def test_config_always_configured() -> None:
    assert FileSourceConfig().configured is True
