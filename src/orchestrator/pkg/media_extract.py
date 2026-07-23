"""Producing side of media ingestion (G3, phase 2): OCR images → committed artifacts.

This is the deliberate *opposite* of :mod:`pkg.media`. The reader there is pure, deterministic,
model-free, and runs inside ``understand``/``state``. **This module is none of those** — and that
is the whole point of the split. ``orchestrator media extract`` is explicit and opt-in: it MAY run
a model (OCR), be slow, and produce output that varies across extractor versions. It writes a
content-addressed transcript artifact (``.spine-media/<sha256>.json``) that a human reviews and
commits; from then on the reader turns that plain JSON into ``Doc`` nodes with no model at all.

OCR here is **local** — Tesseract via ``pytesseract`` + Pillow, the ``[media]`` extra, lazy-imported
so the base install stays stdlib-only. Nothing leaves the machine (invariant #5: no silent network).
An absent extra or a missing ``tesseract`` binary raises :class:`MediaExtractorUnavailable` with
actionable guidance — never a silent skip, because the user explicitly *asked* to extract.

Diagram-oriented: architecture diagrams are labels, not prose, so extraction keeps short text lines
(box/edge labels) and drops OCR noise. Bounded honestly (invariant #4): oversized images are skipped
and over-long label sets are truncated with ``truncated: true`` recorded in the artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.pkg.media import (
    IMAGE_SUFFIXES,
    MEDIA_SUFFIXES,
    SCHEMA_VERSION,
    _file_sha256,
    artifact_path,
)

# The producer's identity, recorded in every artifact. A re-extract with a newer Tesseract writes
# the same file with a changed `extractor_version`, so the improvement shows up in a git diff.
EXTRACTOR_NAME = "tesseract"

# Skip images larger than this — a huge scan is almost never a diagram and would stall extraction.
_MAX_IMAGE_BYTES = 25_000_000
# Cap labels per image: a noisy OCR pass on a dense figure shouldn't flood the graph. Beyond this
# the artifact is marked truncated (honest bounding), exactly as the doc readers cap sections.
_MAX_SEGMENTS = 200
# A label worth keeping has at least this many characters and one alphanumeric — enough to drop the
# stray glyphs and rule fragments OCR emits for diagram lines and arrowheads.
_MIN_LABEL_CHARS = 2


class OcrFn(Protocol):
    """A callable that turns an image path into raw recognised text. Injectable for testing."""

    def __call__(self, path: Path, /) -> str: ...


class MediaExtractorUnavailable(RuntimeError):
    """The ``[media]`` extra or the system ``tesseract`` binary isn't installed.

    Raised — never swallowed — because ``media extract`` is an explicit request: the user wants
    extraction, so silence would be a lie. The CLI turns this into install guidance.
    """


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of extracting one media file."""

    media: Path
    status: str  # "written" | "unchanged" | "skipped-too-large" | "unsupported"
    artifact: Path | None = None
    segments: int = 0
    truncated: bool = False


def _load_pytesseract() -> object:
    try:
        import pytesseract
    except ImportError as exc:  # the `[media]` extra isn't installed
        raise MediaExtractorUnavailable(
            "media extraction needs the [media] extra: pip install 'synaptixs-spine[media]' "
            "(and a system `tesseract` binary — https://tesseract-ocr.github.io/)."
        ) from exc
    return pytesseract


def tesseract_version() -> str:
    """The installed Tesseract version string, pinned into artifacts for determinism/diffing.

    Raises :class:`MediaExtractorUnavailable` if the binary is missing — the version *is* the
    binary's, so we can't record a truthful artifact without it.
    """
    pytesseract = _load_pytesseract()
    try:
        return str(pytesseract.get_tesseract_version())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — pytesseract raises TesseractNotFoundError et al.
        raise MediaExtractorUnavailable(
            "the `tesseract` binary was not found on PATH. Install it "
            "(https://tesseract-ocr.github.io/) — the [media] Python extra wraps it, it is not a "
            "pure-Python OCR engine."
        ) from exc


def _tesseract_ocr(path: Path) -> str:
    """Default OCR backend: recognise an image's text locally with Tesseract."""
    pytesseract = _load_pytesseract()
    try:
        from PIL import Image
    except ImportError as exc:
        raise MediaExtractorUnavailable(
            "media extraction needs Pillow: pip install 'synaptixs-spine[media]'."
        ) from exc
    try:
        with Image.open(path) as image:
            return str(pytesseract.image_to_string(image))  # type: ignore[attr-defined]
    except MediaExtractorUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — unreadable image / tesseract failure
        raise MediaExtractorUnavailable(f"OCR failed for {path}: {exc}") from exc


def labels_from_ocr(text: str) -> list[str]:
    """Diagram labels from raw OCR text: non-empty, de-noised lines, order preserved, deduplicated.

    Kept deliberately simple and deterministic — no model, no heuristics beyond "a label is a line
    with real characters". A diagram's value is its box/edge labels (``BacklogService``,
    ``calc_tax``); keeping whole lines lets the doc binder find identifiers inside them while
    dropping the punctuation-only fragments OCR emits for arrows and borders.
    """
    seen: set[str] = set()
    labels: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if len(line) < _MIN_LABEL_CHARS or not any(ch.isalnum() for ch in line):
            continue
        if line not in seen:
            seen.add(line)
            labels.append(line)
    return labels


def build_image_artifact(
    media_path: Path,
    repo_root: Path,
    *,
    ocr: OcrFn | None = None,
    version: str | None = None,
) -> dict[str, object]:
    """Build (but don't write) the artifact dict for one image. Pure given ``ocr`` + ``version``.

    ``ocr`` and ``version`` are injectable so this — and everything that binds to it — is testable
    without a Tesseract install; production passes neither and gets the local backend.
    """
    sha = _file_sha256(media_path)
    if sha is None:
        raise MediaExtractorUnavailable(f"could not read {media_path}")
    run_ocr = ocr or _tesseract_ocr
    extractor_version = version or tesseract_version()
    labels = labels_from_ocr(run_ocr(media_path))
    truncated = len(labels) > _MAX_SEGMENTS
    labels = labels[:_MAX_SEGMENTS]
    rel = media_path.resolve().relative_to(repo_root.resolve()).as_posix()
    segments = [{"text": text, "page": 1, "start_ms": None, "end_ms": None} for text in labels]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_file": rel,
        "source_sha256": sha,
        "media_kind": "image",
        "extractor": EXTRACTOR_NAME,
        "extractor_version": extractor_version,
        "truncated": truncated,
        "segments": segments,
    }


def write_artifact(repo_root: Path, artifact: dict[str, object]) -> Path:
    """Write ``artifact`` to ``<repo_root>/.spine-media/<sha>.json`` and return the path.

    Pretty-printed and newline-terminated so committed artifacts diff cleanly in review.
    """
    sha = str(artifact["source_sha256"])
    path = artifact_path(repo_root, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def extract_image(
    media_path: Path,
    repo_root: Path,
    *,
    force: bool = False,
    ocr: OcrFn | None = None,
    version: str | None = None,
) -> ExtractResult:
    """Extract one image to a committed artifact, skipping work that's already done.

    Returns an :class:`ExtractResult`; never runs OCR when an up-to-date artifact already exists
    (unless ``force``), and skips oversized images rather than stalling on them.
    """
    try:
        if media_path.stat().st_size > _MAX_IMAGE_BYTES:
            return ExtractResult(media_path, "skipped-too-large")
    except OSError as exc:
        raise MediaExtractorUnavailable(f"could not read {media_path}") from exc

    sha = _file_sha256(media_path)
    if sha is None:
        raise MediaExtractorUnavailable(f"could not read {media_path}")
    existing = artifact_path(repo_root, sha)
    if existing.is_file() and not force:
        return ExtractResult(media_path, "unchanged", existing)

    artifact = build_image_artifact(media_path, repo_root, ocr=ocr, version=version)
    path = write_artifact(repo_root, artifact)
    segments = artifact["segments"]
    n = len(segments) if isinstance(segments, list) else 0
    return ExtractResult(media_path, "written", path, n, bool(artifact["truncated"]))


def _iter_files(paths: list[Path], suffixes: frozenset[str]) -> list[Path]:
    """Expand files/directories into files whose suffix is in ``suffixes`` (sorted, deduped)."""
    found: list[Path] = []
    seen: set[Path] = set()

    def _add(candidate: Path) -> None:
        resolved = candidate.resolve()
        if candidate.suffix.lower() in suffixes and resolved not in seen:
            seen.add(resolved)
            found.append(candidate)

    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    _add(child)
        elif path.is_file():
            _add(path)
    return found


def iter_image_files(paths: list[Path]) -> list[Path]:
    """Expand files/directories into the image files phase 2 can extract (sorted, deduped)."""
    return _iter_files(paths, IMAGE_SUFFIXES)


def iter_media_files(paths: list[Path]) -> list[Path]:
    """Expand files/directories into all supported media files — images *and* audio/video."""
    return _iter_files(paths, MEDIA_SUFFIXES)


__all__ = [
    "EXTRACTOR_NAME",
    "ExtractResult",
    "MediaExtractorUnavailable",
    "build_image_artifact",
    "extract_image",
    "iter_image_files",
    "iter_media_files",
    "labels_from_ocr",
    "tesseract_version",
    "write_artifact",
]
