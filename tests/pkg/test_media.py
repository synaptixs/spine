"""Media ingestion (G3, phase 1): committed transcript artifacts → ``Doc`` nodes + ``MENTIONS``.

The reader is pure and model-free — OCR/ASR lives in ``orchestrator media extract``, not here, so
these tests only ever hand-write artifacts. ``test_doc_source.py`` covers the reader seam in
general; this file covers the media reader specifically: artifact lookup + validation, real
symbol binding, and the invariant that a repo with **no** artifacts is bit-identical to today.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from orchestrator.pkg import FactStore, link_docs
from orchestrator.pkg.doc_source import _READERS, read_doc_pages
from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.media import (
    ARTIFACT_DIRNAME,
    MEDIA_SUFFIXES,
    SCHEMA_VERSION,
    read_media_artifact,
)


def _code_batch() -> FactBatch:
    """A tiny code graph: one module, one type, one function (mirrors test_doc_link)."""
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
            "py:billing.tax.calc_tax",
            NodeKind.FUNCTION,
            "calc_tax",
            "python",
            Provenance("src/billing/tax.py", 1),
        )
    )
    return b


def _write_media(
    root: Path,
    rel: str,
    segments: list[str],
    *,
    schema_version: int = SCHEMA_VERSION,
    sha_override: str | None = None,
) -> Path:
    """Create a fake media file and its committed artifact; return the media path.

    The artifact is always *named* by the file's real hash (so lookup finds it); ``sha_override``
    poisons only the internal ``source_sha256`` field, to exercise the integrity check.
    """
    media = root / rel
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(f"fake-media-bytes::{rel}".encode())
    sha = hashlib.sha256(media.read_bytes()).hexdigest()
    art_dir = root / ARTIFACT_DIRNAME
    art_dir.mkdir(exist_ok=True)
    artifact = {
        "schema_version": schema_version,
        "source_file": rel,
        "source_sha256": sha_override or sha,
        "media_kind": "image",
        "extractor": "hand",
        "extractor_version": "0",
        "truncated": False,
        "segments": [{"text": t, "page": 1, "start_ms": None, "end_ms": None} for t in segments],
    }
    (art_dir / f"{sha}.json").write_text(json.dumps(artifact), encoding="utf-8")
    return media


# ---- the seam ---------------------------------------------------------------


def test_media_suffixes_are_registered() -> None:
    for suffix in MEDIA_SUFFIXES:
        assert suffix in _READERS, suffix
        assert _READERS[suffix].name == "media"
    # Media is one Doc per file — segment/timestamp granularity is a later phase.
    assert _READERS[".png"].sections is False


# ---- reader validation (no model, pure) -------------------------------------


def test_valid_artifact_ingests_its_transcript(tmp_path: Path) -> None:
    _write_media(tmp_path, "overview.png", ["The `Invoice` type totals lines.", "It calls calc_tax."])
    pages = {p.title: p for p in read_doc_pages(tmp_path)}
    assert "overview.png" in pages
    text = pages["overview.png"].text
    assert "`Invoice`" in text and "calc_tax" in text
    # The Doc's provenance is the media file itself, so MENTIONS resolve relative to it.
    assert pages["overview.png"].source_file == "overview.png"


def test_media_file_without_artifact_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "diagram.png").write_bytes(b"a diagram nobody has extracted yet")
    (tmp_path / "README.md").write_text("kept\n", encoding="utf-8")
    # Not extracted yet → skip, non-fatal, exactly as a scanned PDF is skipped today.
    assert {p.title for p in read_doc_pages(tmp_path)} == {"README.md"}


def test_changed_media_orphans_its_artifact(tmp_path: Path) -> None:
    media = _write_media(tmp_path, "overview.png", ["names calc_tax"])
    assert read_media_artifact(media) is not None
    media.write_bytes(b"the diagram was edited after extraction")
    # New bytes → new hash → the old artifact no longer matches any file → skip.
    assert read_media_artifact(media) is None


def test_integrity_mismatch_is_skipped(tmp_path: Path) -> None:
    # Artifact found by name, but its declared source_sha256 doesn't match the file → skip.
    media = _write_media(tmp_path, "overview.png", ["names calc_tax"], sha_override="0" * 64)
    assert read_media_artifact(media) is None


def test_unknown_schema_version_is_skipped(tmp_path: Path) -> None:
    media = _write_media(tmp_path, "future.png", ["names calc_tax"], schema_version=SCHEMA_VERSION + 1)
    assert read_media_artifact(media) is None


def test_empty_transcript_is_skipped(tmp_path: Path) -> None:
    media = _write_media(tmp_path, "blank.png", ["   ", ""])
    # No extractable text (the OCR-of-a-photo case) → skip, like a scanned PDF.
    assert read_media_artifact(media) is None


def test_malformed_artifact_is_not_fatal(tmp_path: Path) -> None:
    media = tmp_path / "overview.png"
    media.write_bytes(b"some media bytes")
    sha = hashlib.sha256(media.read_bytes()).hexdigest()
    art_dir = tmp_path / ARTIFACT_DIRNAME
    art_dir.mkdir()
    (art_dir / f"{sha}.json").write_text("{ not valid json", encoding="utf-8")
    (tmp_path / "ok.md").write_text("fine\n", encoding="utf-8")
    assert {p.title for p in read_doc_pages(tmp_path)} == {"ok.md"}


# ---- binding (exit criterion: artifacts → Doc nodes + MENTIONS) -------------


def test_link_docs_binds_media_transcript_to_real_symbols(tmp_path: Path) -> None:
    _write_media(tmp_path, "overview.png", ["The `Invoice` type totals lines.", "It applies calc_tax."])
    store = FactStore(link_docs(_code_batch(), tmp_path))
    # The hand-written transcript names two live symbols → both become MENTIONS targets.
    assert {n.name for n in store.mentions_of("doc:overview.png")} == {"Invoice", "calc_tax"}
    # And the reverse query answers "which docs describe Invoice?" with the diagram.
    assert "doc:overview.png" in [d.id for d in store.docs_for("py:billing.invoice.Invoice")]
    # A Doc is never itself a MENTIONS target.
    assert all(e.dst.startswith("py:") for e in store.edges_of_kind(EdgeKind.MENTIONS))


# ---- the core invariant: no artifacts ⇒ bit-identical to today --------------


def test_media_without_artifacts_is_bit_identical_to_media_absent(tmp_path: Path) -> None:
    """A repo that never ran `media extract` must build byte-identically to one with no media."""
    (tmp_path / "README.md").write_text("`calc_tax` is documented here.\n", encoding="utf-8")

    baseline = link_docs(_code_batch(), tmp_path)
    base_nodes = sorted(n.id for n in baseline.nodes)
    base_edges = sorted((e.src, e.dst, e.kind.value) for e in baseline.edges)

    # Add media files but NO `.spine-media/` artifacts — they must contribute exactly nothing.
    (tmp_path / "overview.png").write_bytes(b"an architecture diagram, unextracted")
    (tmp_path / "review.mp4").write_bytes(b"a recorded design review, unextracted")
    with_media = link_docs(_code_batch(), tmp_path)

    assert sorted(n.id for n in with_media.nodes) == base_nodes
    assert sorted((e.src, e.dst, e.kind.value) for e in with_media.edges) == base_edges
