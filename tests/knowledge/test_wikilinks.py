"""The Obsidian vault export — a link-syntax transform over rendered episteme markdown.

Most of these test what is *not* rewritten. A transform that converts too much is worse than
one that converts too little: a wikilink pointing at nothing is a dead end inside the vault,
whereas a surviving markdown link still works (Obsidian renders both).
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.knowledge.wikilinks import to_wikilinks, write_vault


def test_rewrites_a_sibling_page_link() -> None:
    out = to_wikilinks("See [`app.cli`](orchestrator.cli.md).", page="modules/x.md")
    assert out == "See [[modules/orchestrator.cli|app.cli]]."


def test_drops_a_redundant_alias() -> None:
    """`[[modules/x]]` reads better than `[[modules/x|x]]` when they say the same thing."""
    out = to_wikilinks("[`orchestrator.cli`](orchestrator.cli.md)", page="modules/x.md")
    assert out == "[[modules/orchestrator.cli]]"


def test_resolves_a_parent_relative_link_against_the_vault_root() -> None:
    out = to_wikilinks("[← Episteme](../README.md)", page="modules/x.md")
    assert out == "[[README|← Episteme]]"


def test_keeps_the_anchor() -> None:
    out = to_wikilinks("[`parse`](../modules/foo.md#parse)", page="areas/a.md")
    assert out == "[[modules/foo#parse|parse]]"


def test_subdirectory_paths_disambiguate_a_basename_collision() -> None:
    """`modules/x.md` and `areas/x.md` both basename to `x`; the path form keeps them apart."""
    mod = to_wikilinks("[a](../modules/x.md)", page="areas/p.md")
    area = to_wikilinks("[a](../areas/x.md)", page="modules/p.md")
    assert mod == "[[modules/x|a]]"
    assert area == "[[areas/x|a]]"
    assert mod != area


def test_leaves_source_links_alone() -> None:
    """These point at real code outside the vault — a wikilink would resolve to nothing."""
    text = "**Source:** [`app/mod.py:42`](../../src/app/mod.py#L42)"
    assert to_wikilinks(text, page="modules/x.md") == text


def test_leaves_external_and_anchor_links_alone() -> None:
    text = "[docs](https://example.com/a.md) and [top](#overview)"
    assert to_wikilinks(text, page="README.md") == text


def test_leaves_a_target_that_escapes_the_vault_alone() -> None:
    text = "[out](../../elsewhere/page.md)"
    assert to_wikilinks(text, page="modules/x.md") == text


def test_keeps_markdown_when_the_label_contains_a_pipe() -> None:
    """A wikilink alias has no escape for `|`. A working markdown link beats a broken
    wikilink — the same reasoning as md.js falling back to <pre>."""
    text = "[a|b](../modules/x.md)"
    assert to_wikilinks(text, page="areas/p.md") == text


def test_is_idempotent() -> None:
    """Running the transform twice must not mangle already-converted links."""
    once = to_wikilinks("[`f`](../modules/foo.md#f)", page="areas/a.md")
    assert to_wikilinks(once, page="areas/a.md") == once


def _bank(root: Path) -> Path:
    bank = root / "episteme"
    (bank / "modules").mkdir(parents=True)
    (bank / "README.md").write_text("- [`cli`](modules/orchestrator.cli.md)\n", encoding="utf-8")
    (bank / "modules" / "orchestrator.cli.md").write_text(
        "[← Episteme](../README.md)\n\n**Source:** [`cli.py:1`](../../src/cli.py#L1)\n",
        encoding="utf-8",
    )
    (bank / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n binary")
    return bank


def test_write_vault_transforms_markdown_and_copies_everything_else(tmp_path: Path) -> None:
    bank = _bank(tmp_path)
    counts = write_vault(bank, tmp_path / "vault")

    assert counts == {"pages": 2, "wikilinks": 2, "copied": 1}
    readme = (tmp_path / "vault" / "README.md").read_text(encoding="utf-8")
    assert readme.strip() == "- [[modules/orchestrator.cli|cli]]"

    page = (tmp_path / "vault" / "modules" / "orchestrator.cli.md").read_text(encoding="utf-8")
    assert "[[README|← Episteme]]" in page
    assert "[`cli.py:1`](../../src/cli.py#L1)" in page, "source link must survive untouched"

    # Non-markdown is copied byte-for-byte, not decoded as text.
    assert (tmp_path / "vault" / "diagram.png").read_bytes() == b"\x89PNG\r\n\x1a\n binary"


def test_write_vault_is_byte_deterministic(tmp_path: Path) -> None:
    """Same property the graph exports hold: an unchanged bank rewrites identical bytes."""
    bank = _bank(tmp_path)
    write_vault(bank, tmp_path / "a")
    write_vault(bank, tmp_path / "b")
    for rel in ("README.md", "modules/orchestrator.cli.md", "diagram.png"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()
