"""Render the episteme as an Obsidian vault — the same pages, a different link syntax.

The G5 spec originally scoped an "Obsidian-vault / Markdown-wiki writer: a page per
module/symbol, links along edges". That writer already exists — it is
:mod:`knowledge.renderers`, and its output is ``episteme/``: a page per module and area,
symbol anchors, source deep-links, backlinks, orphan reaping. The only thing Obsidian wants
that it lacks is ``[[wikilink]]`` syntax instead of relative paths.

So this is a **transform over rendered markdown, not a second renderer**. Two renderers over
the same facts would drift — the ``IMPORTS``-edge bug that shipped in phase 2 of
``pkg-navigable-reports`` is the precedent for how quietly that happens. It is also why this
writes a *copy*: ``episteme/`` stays canonical and reviewable in relative-markdown form, and
the vault is an export like GraphML is, regenerated on demand and never the source of truth.

What is deliberately left alone:

* **Source links** (``../../src/app/mod.py#L42``). They point outside the vault at real code;
  a wikilink there would resolve to nothing.
* **External links** (``http(s)://``), and bare anchors (``#section``).
* **Any link whose label contains a pipe.** ``[[page|alias]]`` has no escape for ``|``, so
  rather than emit a broken link the original markdown is kept. Obsidian renders standard
  markdown links too, so the fallback still works — the same reasoning as ``md.js``
  falling back to ``<pre>``: no link beats a wrong link.

Backticks are stripped from the alias. Obsidian renders a wikilink alias as plain text, so
``[[modules/x|`parse`]]`` would show literal backtick characters; ``[[modules/x|parse]]``
is what the author meant. Where the alias would just repeat the page name it is dropped
entirely, giving the plain ``[[modules/x]]`` that reads best in a vault.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
"""Inline markdown link. Deliberately rejects whitespace in the target — a titled link
(``[a](b "t")``) is not something the renderers emit, and skipping it is safer than guessing."""

_EXTERNAL = ("http://", "https://", "mailto:")


def _alias(label: str, target_page: str) -> str | None:
    """The ``|alias`` portion, or None when the alias would be redundant."""
    clean = label.strip().strip("`").strip()
    return None if clean == posixpath.basename(target_page) else clean


def to_wikilinks(markdown: str, *, page: str) -> str:
    """Rewrite in-vault ``.md`` links in one page to Obsidian wikilinks.

    ``page`` is the page's own vault-relative path (``modules/foo.md``), needed to resolve
    relative targets like ``../README.md``. Targets that escape the vault root are left as-is.
    Pure and deterministic: same input, same output.
    """
    page_dir = posixpath.dirname(page)

    def replace(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(_EXTERNAL) or target.startswith("#"):
            return match.group(0)

        path, _, anchor = target.partition("#")
        if not path.endswith(".md"):
            return match.group(0)  # a source link, or some other asset

        resolved = posixpath.normpath(posixpath.join(page_dir, path))
        if resolved.startswith(".."):
            return match.group(0)  # outside the vault — not ours to rewrite

        # No escape exists for a pipe inside a wikilink alias; keep the markdown link.
        if "|" in label:
            return match.group(0)

        without_ext = resolved[: -len(".md")]
        alias = _alias(label, without_ext)
        inner = without_ext + (f"#{anchor}" if anchor else "")
        return f"[[{inner}|{alias}]]" if alias else f"[[{inner}]]"

    return _LINK.sub(replace, markdown)


def write_vault(bank_dir: Path | str, out_dir: Path | str) -> dict[str, int]:
    """Copy an ``episteme/`` tree to ``out_dir`` with wikilink syntax.

    Markdown is transformed; every other file is copied byte-for-byte. Returns counts for
    the caller to report. Deterministic — pages are walked in sorted order and each is a
    pure function of its input, so re-running over an unchanged bank rewrites identical bytes.
    """
    bank, out = Path(bank_dir), Path(out_dir)
    pages = 0
    links = 0
    copied = 0
    for src in sorted(p for p in bank.rglob("*") if p.is_file()):
        rel = src.relative_to(bank).as_posix()
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() != ".md":
            dst.write_bytes(src.read_bytes())
            copied += 1
            continue
        text = src.read_text(encoding="utf-8")
        converted = to_wikilinks(text, page=rel)
        links += converted.count("[[")
        dst.write_text(converted, encoding="utf-8")
        pages += 1
    return {"pages": pages, "wikilinks": links, "copied": copied}


__all__ = ["to_wikilinks", "write_vault"]
