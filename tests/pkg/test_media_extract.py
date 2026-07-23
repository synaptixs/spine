"""Media extraction (G3, phase 2): `media extract` OCRs images → committed artifacts.

The extractor is the opt-in, model-*using* half; the reader (test_media.py) is the deterministic
half. OCR is injected here so the whole producer→artifact→reader→binding loop is exercised without
a Tesseract install. One guarded test runs the *real* backend when it's present.

The load-bearing test is `test_extracted_labels_bind_to_real_symbols` — the phase-2 exit criterion,
and the same assert-a-real-symbol-binds smoke the G2 retro said was the only thing that caught
silent binding loss.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.pkg import FactStore, link_docs
from orchestrator.pkg import media_extract as mx
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.media import SCHEMA_VERSION, read_media_artifact

# A deterministic stand-in for OCR of an architecture diagram naming three symbols.
_DIAGRAM_OCR = "BacklogService\ncalls calc_tax\nInvoice\n"


def _code_batch() -> FactBatch:
    b = FactBatch()
    b.add_node(
        Node(
            "py:billing.invoice",
            NodeKind.MODULE,
            "billing.invoice",
            "python",
            Provenance("src/billing/invoice.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:billing.invoice.Invoice",
            NodeKind.TYPE,
            "Invoice",
            "python",
            Provenance("src/billing/invoice.py", 4),
        )
    )
    b.add_node(
        Node(
            "py:backlog.BacklogService",
            NodeKind.TYPE,
            "BacklogService",
            "python",
            Provenance("src/backlog/service.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:billing.tax.calc_tax",
            NodeKind.FUNCTION,
            "calc_tax",
            "python",
            Provenance("src/billing/tax.py", 1),
        )
    )
    return b


def _png(root: Path, rel: str = "arch.png") -> Path:
    media = root / rel
    media.write_bytes(f"fake-image-bytes::{rel}".encode())
    return media


# ---- label extraction -------------------------------------------------------


def test_labels_from_ocr_keeps_labels_drops_noise_and_dedupes() -> None:
    raw = "BacklogService\n---->\n  calc_tax  \n\n||\ncalc_tax\nInvoice\n."
    # Punctuation-only lines (arrows, borders) and duplicates go; real labels stay, order preserved.
    assert mx.labels_from_ocr(raw) == ["BacklogService", "calc_tax", "Invoice"]


# ---- artifact building ------------------------------------------------------


def test_build_image_artifact_is_schema_conformant(tmp_path: Path) -> None:
    png = _png(tmp_path)
    artifact = mx.build_image_artifact(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="5.3.4")
    assert artifact["schema_version"] == SCHEMA_VERSION
    assert artifact["source_file"] == "arch.png"
    assert artifact["extractor"] == "tesseract" and artifact["extractor_version"] == "5.3.4"
    assert artifact["media_kind"] == "image" and artifact["truncated"] is False
    segments = artifact["segments"]
    assert isinstance(segments, list)
    assert [s["text"] for s in segments] == ["BacklogService", "calls calc_tax", "Invoice"]


def test_build_records_hash_matching_the_file(tmp_path: Path) -> None:
    import hashlib

    png = _png(tmp_path)
    artifact = mx.build_image_artifact(png, tmp_path, ocr=lambda _p: "x", version="v")
    assert artifact["source_sha256"] == hashlib.sha256(png.read_bytes()).hexdigest()


# ---- extract_image (write + skip-existing + determinism) --------------------


def test_extract_image_writes_artifact_the_reader_round_trips(tmp_path: Path) -> None:
    png = _png(tmp_path)
    result = mx.extract_image(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="5.3.4")
    assert result.status == "written"
    assert result.artifact is not None and result.artifact.exists()
    # Producer and consumer agree on the format: the deterministic reader reads back what we wrote.
    text = read_media_artifact(png)
    assert text is not None and "calc_tax" in text and "BacklogService" in text


def test_extract_image_is_noop_when_artifact_exists(tmp_path: Path) -> None:
    png = _png(tmp_path)
    calls = {"n": 0}

    def counting_ocr(_p: Path) -> str:
        calls["n"] += 1
        return _DIAGRAM_OCR

    assert mx.extract_image(png, tmp_path, ocr=counting_ocr, version="v").status == "written"
    # Second run must not re-run OCR — the artifact already covers this exact file hash.
    assert mx.extract_image(png, tmp_path, ocr=counting_ocr, version="v").status == "unchanged"
    assert calls["n"] == 1
    # --force re-runs it.
    assert mx.extract_image(png, tmp_path, ocr=counting_ocr, version="v", force=True).status == "written"
    assert calls["n"] == 2


def test_extract_is_deterministic_for_a_pinned_version(tmp_path: Path) -> None:
    png = _png(tmp_path)
    r1 = mx.extract_image(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="5.3.4")
    assert r1.artifact is not None
    first = r1.artifact.read_bytes()
    mx.extract_image(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="5.3.4", force=True)
    # Same extractor version + same input → byte-identical artifact (invariant #2).
    assert r1.artifact.read_bytes() == first


# ---- honest bounding (invariant #4) -----------------------------------------


def test_oversized_image_is_skipped_not_extracted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mx, "_MAX_IMAGE_BYTES", 4)
    png = _png(tmp_path)  # longer than 4 bytes
    result = mx.extract_image(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="v")
    assert result.status == "skipped-too-large" and result.artifact is None


def test_too_many_labels_are_truncated_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mx, "_MAX_SEGMENTS", 2)
    png = _png(tmp_path)
    # Labels must clear _MIN_LABEL_CHARS (≥2 chars), so use two-char lines, not single letters.
    artifact = mx.build_image_artifact(png, tmp_path, ocr=lambda _p: "aa\nbb\ncc\ndd", version="v")
    segments = artifact["segments"]
    assert artifact["truncated"] is True
    assert isinstance(segments, list) and len(segments) == 2  # capped


# ---- discovery --------------------------------------------------------------


def test_iter_image_files_expands_dirs_images_only(tmp_path: Path) -> None:
    _png(tmp_path, "a.png")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.jpg").write_bytes(b"x")
    (tmp_path / "notes.md").write_text("not an image\n", encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"phase 3, not here")
    names = sorted(p.name for p in mx.iter_image_files([tmp_path]))
    assert names == ["a.png", "b.jpg"]  # audio/video excluded (phase 3), non-media excluded


# ---- unavailable backend surfaces, never silently skips ---------------------


def test_extractor_unavailable_propagates(tmp_path: Path) -> None:
    def broken_ocr(_p: Path) -> str:
        raise mx.MediaExtractorUnavailable("no tesseract")

    png = _png(tmp_path)
    with pytest.raises(mx.MediaExtractorUnavailable):
        mx.extract_image(png, tmp_path, ocr=broken_ocr, version="v")


# ---- exit criterion: extracted labels bind to real symbols ------------------


def test_extracted_labels_bind_to_real_symbols(tmp_path: Path) -> None:
    """extract → artifact → reader → link_docs: the diagram's labels become MENTIONS edges."""
    png = _png(tmp_path)
    mx.extract_image(png, tmp_path, ocr=lambda _p: _DIAGRAM_OCR, version="5.3.4")
    store = FactStore(link_docs(_code_batch(), tmp_path))
    mentioned = {n.name for n in store.mentions_of("doc:arch.png")}
    # `BacklogService` (multi-segment CamelCase) and `calc_tax` (snake_case) are code-intent
    # mentions and bind. The bare word `Invoice` is a single capitalised word — the binder treats
    # that as prose, not a code claim (precision-first, invariant #6), so it does NOT bind from
    # plain OCR text. It would only bind if backticked, which OCR output never is.
    assert mentioned == {"BacklogService", "calc_tax"}


# ---- real Tesseract (guarded — runs where the binary is installed) ----------


def test_real_tesseract_produces_a_valid_artifact(tmp_path: Path) -> None:
    pytest.importorskip("pytesseract")
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    try:
        mx.tesseract_version()  # raises if the system binary is absent
    except mx.MediaExtractorUnavailable:
        pytest.skip("tesseract binary not installed")

    image = Image.new("RGB", (320, 80), "white")
    ImageDraw.Draw(image).text((10, 30), "calc_tax", fill="black")
    png = tmp_path / "diagram.png"
    image.save(png)

    result = mx.extract_image(png, tmp_path)
    assert result.status == "written" and result.artifact is not None
    # Assert the pipeline produces a schema-valid artifact — not exact recognition, which depends on
    # font/DPI. Precision on a *real* diagram is the manual spike the roadmap mandates before relying
    # on this in anger; this test only guards the plumbing.
    artifact = json.loads(result.artifact.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == SCHEMA_VERSION
    assert artifact["extractor"] == "tesseract"
