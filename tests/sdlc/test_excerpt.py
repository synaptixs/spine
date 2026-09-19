"""`source_at` — reading real source at a location the graph already found."""

from __future__ import annotations

from pathlib import Path

# ---- source_at: the public entry point briefs read code through --------------


def test_source_at_centres_on_the_line_and_tags_the_fence(tmp_path: Path) -> None:
    from orchestrator.sdlc.excerpt import source_at

    (tmp_path / "m.cs").write_text("\n".join(f"line{i}" for i in range(40)), encoding="utf-8")
    e = source_at(tmp_path, "m.cs:20", context=6)
    assert e is not None
    assert e.language == "csharp"
    assert "line19" in e.text and e.start_line <= 20


def test_source_at_refuses_rather_than_raises(tmp_path: Path) -> None:
    """Every failure is None. A brief that dies because a file moved is worse than one that
    omits an excerpt and keeps the bullet."""
    from orchestrator.sdlc.excerpt import source_at

    (tmp_path / "m.py").write_text("one\ntwo\n", encoding="utf-8")
    assert source_at(tmp_path, "missing.py:1") is None  # deleted path
    assert source_at(tmp_path, "m.py:900") is None  # file shrank since extraction
    assert source_at(tmp_path, "m.py:notaline") is None  # malformed provenance
    assert source_at(None, "m.py:1") is None  # no root (a merged brief with no checkout)
    assert source_at(tmp_path, "") is None


def test_source_at_does_not_clamp_a_line_past_the_end(tmp_path: Path) -> None:
    """Clamping would quote the wrong code confidently; refusing says nothing, which is true."""
    from orchestrator.sdlc.excerpt import source_at

    (tmp_path / "m.py").write_text("only\n", encoding="utf-8")
    assert source_at(tmp_path, "m.py:5") is None


def test_source_at_survives_a_binary_file(tmp_path: Path) -> None:
    from orchestrator.sdlc.excerpt import source_at

    (tmp_path / "blob.py").write_bytes(b"\x00\xff\xfe binary \x00")
    assert source_at(tmp_path, "blob.py:1") is None
