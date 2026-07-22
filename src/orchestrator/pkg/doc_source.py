"""Read a repo's documentation files into ``DocPage`` rows for the doc-semantic layer.

The counterpart to the language extractors, for prose: walk a repository for
documentation — text (``.md`` / ``.rst`` / ``.txt`` / ``.markdown``) and **PDF** — and hand
each file to ``pkg.docs`` as a ``DocPage`` (title = repo-relative path, so mentions resolve
relative to the doc). Deterministic, no LLM, no network.

Text parses with the stdlib. **PDF** needs a parser (``pypdf``, pure-Python) behind the
optional ``[docs]`` extra, lazy-imported so the base install stays stdlib-only. A PDF with
no extractable text (a scanned image — no OCR) yields nothing and is skipped; so is any PDF
when ``pypdf`` isn't installed. Either way ``read_doc_pages`` returns the text docs it could
read — PDFs are additive, never a hard dependency.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from orchestrator.pkg.docs import DocPage
from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

# Text documentation suffixes (lowercased); PDF is handled separately (needs `[docs]`).
_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_PDF_SUFFIX = ".pdf"
# Skip absurdly large docs (generated dumps, vendored changelogs) — keep the graph legible.
_MAX_DOC_BYTES = 1_000_000
# PDFs are binary and legitimately larger; cap bytes and pages so a giant scan can't stall a walk.
_MAX_PDF_BYTES = 25_000_000
_MAX_PDF_PAGES = 500
# Cap section-granular nodes per doc: a runaway doc (hundreds of headings) shouldn't flood the
# graph. Beyond this, the doc stays whole rather than exploding into fragments.
_MAX_SECTIONS = 40
# An ATX markdown heading: `#`..`######` then text. Setext / RST underlines aren't split (they'd
# need lookahead and are rarer in the docs this targets); those docs stay whole — safe, not wrong.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def is_doc_file(path: Path) -> bool:
    """True for a text-doc or PDF suffix (PDF still needs the ``[docs]`` extra to *read*)."""
    suffix = path.suffix.lower()
    return suffix in _TEXT_SUFFIXES or suffix == _PDF_SUFFIX


def _slug(heading: str) -> str:
    """A GitHub-style anchor slug for a heading (lowercase, spaces→dashes, punctuation dropped)."""
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")


def split_sections(page: DocPage) -> list[DocPage]:
    """Split a markdown ``DocPage`` into one page per heading (``path#slug``), or ``[page]`` unchanged.

    Section granularity lets a ``MENTIONS`` edge point at the *section* that names a symbol, not the
    whole file, and gives each section provenance at its heading line. Bounded by ``_MAX_SECTIONS``:
    a doc with more headings than that stays whole rather than fragmenting the graph. Content before
    the first heading (a preamble) becomes a section keyed by the bare path, so nothing is dropped.
    Deterministic; unique slugs are disambiguated with a numeric suffix."""
    lines = page.text.splitlines()
    heads = [(i, m) for i, line in enumerate(lines) if (m := _HEADING_RE.match(line))]
    if not heads or len(heads) > _MAX_SECTIONS:
        return [page]

    file = page.source_file or page.title
    bounds = [i for i, _ in heads] + [len(lines)]
    sections: list[DocPage] = []
    seen: dict[str, int] = {}
    # Preamble before the first heading (if any real content) → a page keyed by the bare path.
    if heads[0][0] > 0 and "".join(lines[: heads[0][0]]).strip():
        pre = "\n".join(lines[: heads[0][0]])
        sections.append(DocPage(title=file, text=pre, base_dir=page.base_dir, source_file=file, line=1))
    for idx, (line_no, m) in enumerate(heads):
        slug = _slug(m.group(2)) or "section"
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        body = "\n".join(lines[line_no : bounds[idx + 1]])
        title = f"{file}#{slug}"
        sections.append(
            DocPage(title=title, text=body, base_dir=page.base_dir, source_file=file, line=line_no + 1)
        )
    return sections


def _read_pdf_text(path: Path) -> str | None:
    """A PDF's text (pages joined by blank lines), or ``None`` if it can't be read.

    ``None`` covers three cases, all non-fatal: ``pypdf`` isn't installed (the ``[docs]``
    extra), the file isn't a parseable PDF, or it has no extractable text (a scanned image —
    we don't OCR). pypdf's error surface on malformed input is wide, so the parse is
    best-effort per page."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages[:_MAX_PDF_PAGES]:
            parts.append(page.extract_text() or "")
    except Exception:  # noqa: BLE001 — malformed/encrypted PDFs raise assorted pypdf errors
        return None
    text = "\n\n".join(p for p in parts if p.strip())
    return text or None


def read_doc_pages(root: Path | str, *, sections: bool = True) -> list[DocPage]:
    """Every documentation file under ``root`` as a ``DocPage`` (repo-relative title).

    Skips the usual ignored dirs (``.git``, ``node_modules``, build output, hidden dirs),
    text files that can't be read as UTF-8 or exceed ``_MAX_DOC_BYTES``, and PDFs that are
    too large or yield no text. Deterministic order.

    With ``sections=True`` (the default), markdown docs are split by heading into section-granular
    pages (``path#slug``) via :func:`split_sections`; other formats and heading-less docs stay
    whole. Pass ``sections=False`` for strictly one page per file."""
    root_path = Path(root).resolve()
    pages: list[DocPage] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            suffix = path.suffix.lower()
            if suffix in _TEXT_SUFFIXES:
                text = _read_text(path)
            elif suffix == _PDF_SUFFIX:
                text = _read_pdf(path)
            else:
                continue
            if text is None:
                continue
            rel = path.relative_to(root_path).as_posix()
            page = DocPage(title=rel, text=text, base_dir=Path(rel).parent.as_posix(), source_file=rel)
            if sections and suffix in _MARKDOWN_SUFFIXES:
                pages.extend(split_sections(page))
            else:
                pages.append(page)
    return pages


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_DOC_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_pdf(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_PDF_BYTES:
            return None
    except OSError:
        return None
    return _read_pdf_text(path)


__all__ = ["is_doc_file", "read_doc_pages", "split_sections"]
