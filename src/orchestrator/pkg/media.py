"""Deterministic ingestion of committed media transcripts (G3, phase 1).

The counterpart to :mod:`pkg.doc_source`'s prose readers, for media. A media file
(``.png``/``.mp4``/…) carries no extractable text on its own — the text lives in a
**committed transcript artifact** that ``orchestrator media extract`` wrote earlier
(a separate, opt-in, possibly-model-using command — *not* this module). This module
only ever *reads* that artifact: it is pure, stdlib-only, no model, no network.

The split is what protects determinism. Model inference (OCR/ASR) runs in
``media extract``; ``understand``/``state`` run :func:`read_media_artifact`, which
turns a plain-JSON artifact into text exactly like any other doc. Same commit in →
same graph out. No artifact present → the media file is skipped (``None``), so a repo
that never ran ``media extract`` builds byte-identically to one with no media at all.

Artifacts live at ``<repo>/.spine-media/<sha256>.json``, keyed by the SHA-256 of the
media file's bytes (the artifact schema is documented on :func:`read_media_artifact` and
:mod:`orchestrator.pkg.media_extract`). Because the reader receives
only the media file's path (the :class:`~pkg.doc_source.DocReader` seam), it hashes the
file and walks up the ancestor directories to find the nearest ``.spine-media/`` holding
a matching artifact. Any mismatch — no artifact, unknown ``schema_version``, a
``source_sha256`` that no longer matches the file (the media changed since extraction),
or no extracted text — returns ``None``: skip, non-fatal, exactly the ``pypdf`` discipline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# The artifact format this reader understands. The reader skips (never errors on) an
# artifact whose schema_version it doesn't recognise, so a newer producer degrades safely.
SCHEMA_VERSION = 1

# Committed transcripts live here, at the repo root. Hidden (leading dot), so `read_doc_pages`'
# walk never recurses into it — the JSON artifacts are never themselves ingested as docs.
ARTIFACT_DIRNAME = ".spine-media"

# Suffixes this reader claims. Images bind diagram labels (produced by `media extract`, phase 2);
# audio/video bind a transcript (phase 3). The reader treats them identically — it only ever reads
# an artifact — so the split is about which suffixes the *producer* handles, not the consumer.
# A claimed suffix with no artifact still just skips — like a PDF with the `[docs]` extra absent.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
AUDIO_SUFFIXES = frozenset({".mp3", ".wav"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
AUDIO_VIDEO_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES
MEDIA_SUFFIXES = IMAGE_SUFFIXES | AUDIO_VIDEO_SUFFIXES

_HASH_CHUNK = 1 << 20  # 1 MiB — media files are large; hash without loading the whole file.


def _file_sha256(path: Path) -> str | None:
    """SHA-256 of ``path``'s bytes (lowercase hex), or ``None`` if it can't be read."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def artifact_path(repo_root: Path, sha: str) -> Path:
    """Where the artifact for a media file with hash ``sha`` lives under ``repo_root``.

    The single source of truth for the layout, shared by the reader (this module) and the
    producer (``pkg.media_extract``), so the two halves can never disagree on the path.
    """
    return repo_root / ARTIFACT_DIRNAME / f"{sha}.json"


def _find_artifact(media_path: Path, sha: str) -> Path | None:
    """The nearest ancestor's ``.spine-media/<sha>.json``, or ``None`` if none exists.

    Walks upward from the media file so the artifact resolves against whatever repo root the
    file sits under, without the reader needing to be told the root. Nearest ancestor wins,
    which is deterministic and does the sensible thing for nested repos.
    """
    name = f"{sha}.json"
    for ancestor in media_path.resolve().parents:
        candidate = ancestor / ARTIFACT_DIRNAME / name
        if candidate.is_file():
            return candidate
    return None


def _segments_text(artifact: dict[str, object]) -> str | None:
    """Join the artifact's segment texts into one doc body, or ``None`` if there is none.

    Segments are joined with blank lines (paragraph breaks) so the binder reads each as its
    own block. Phase 1 keeps a media file to a single ``Doc`` node; per-segment/timestamped
    granularity is a later phase's concern — the offsets stay in the artifact for it to use.
    """
    segments = artifact.get("segments")
    if not isinstance(segments, list):
        return None
    parts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        text = segment.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts) or None


def read_media_artifact(path: Path) -> str | None:
    """A media file's committed transcript text, or ``None`` to skip the file.

    Pure and deterministic: the only inputs are the media file's bytes (hashed to locate the
    artifact and verify it still matches) and the artifact's JSON. No model, no network, no clock.
    ``None`` — always non-fatal — covers every "nothing to ingest" case: unreadable file, no
    artifact, malformed/legacy artifact, an artifact for a since-changed file, or empty text.
    """
    sha = _file_sha256(path)
    if sha is None:
        return None
    artifact_path = _find_artifact(path, sha)
    if artifact_path is None:
        return None  # not extracted yet — skip, exactly as a scanned PDF is skipped today
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(artifact, dict):
        return None
    if artifact.get("schema_version") != SCHEMA_VERSION:
        return None  # unknown format — a newer producer than this reader; skip, don't guess
    # Integrity: the artifact must declare the same hash we computed. A mismatch means the media
    # changed since extraction (the artifact is stale) or was hand-edited — skip rather than bind
    # text to a file it no longer describes.
    if artifact.get("source_sha256") != sha:
        return None
    return _segments_text(artifact)


__all__ = [
    "ARTIFACT_DIRNAME",
    "AUDIO_SUFFIXES",
    "AUDIO_VIDEO_SUFFIXES",
    "IMAGE_SUFFIXES",
    "MEDIA_SUFFIXES",
    "SCHEMA_VERSION",
    "VIDEO_SUFFIXES",
    "artifact_path",
    "read_media_artifact",
]
