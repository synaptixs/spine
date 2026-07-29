"""ASR side of media ingestion (G3, phase 3): transcribe audio/video → committed artifacts.

The same split as OCR (:mod:`pkg.media_extract`): ``media extract`` is opt-in and model-using and
may vary across versions; it writes a content-addressed transcript artifact that the deterministic
reader (:mod:`pkg.media`) later ingests with no model. Here the transcript's segments carry
``start_ms``/``end_ms`` so a recorded design review becomes a searchable ``Doc`` whose text is
anchored in time.

**Pluggable backend, and the first path that can leave the machine.** A backend is either *local*
(Whisper — nothing leaves the box) or *remote* (an API — which uploads the audio). Invariant #5
(no silent network) is enforced structurally: a backend advertises ``off_machine``, and
:func:`extract_media` refuses to run a remote backend unless the caller passes ``allow_remote=True``
— an explicit, per-run consent the CLI surfaces as ``--allow-remote``. There is no way to send audio
off-machine by accident.

Bounded honestly (invariant #4): transcripts are capped by segment count and by duration; anything
past the cap is dropped and ``truncated: true`` is recorded, so a three-hour recording can't silently
balloon the graph or the API bill.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from orchestrator.pkg.media import (
    AUDIO_SUFFIXES,
    SCHEMA_VERSION,
    _file_sha256,
    artifact_path,
)
from orchestrator.pkg.media_extract import (
    ExtractResult,
    MediaExtractorUnavailableError,
    write_artifact,
)

# Hard ceilings so a long recording can't stall extraction, flood the graph, or run up an API bill.
_MAX_MEDIA_BYTES = 2_000_000_000  # 2 GB — video is legitimately large; still a ceiling.
_MAX_DURATION_MS = 3 * 60 * 60 * 1000  # 3 hours of transcript kept; the rest is truncated.
_MAX_SEGMENTS = 10_000


class RemoteConsentRequiredError(RuntimeError):
    """A remote (off-machine) ASR backend was asked to run without explicit per-run consent.

    Raised — never bypassed — so audio can never leave the machine as a silent side effect
    (invariant #5). The CLI turns this into "pass --allow-remote to consent".
    """


@dataclass(frozen=True)
class Segment:
    """One transcript segment: recognised text and its time span in the media (ms)."""

    text: str
    start_ms: int | None
    end_ms: int | None


class AsrBackend(Protocol):
    """A speech-to-text backend. ``off_machine`` is the honesty contract the consent gate reads."""

    name: str
    off_machine: bool

    def version(self) -> str: ...
    def transcribe(self, path: Path) -> list[Segment]: ...


class LocalWhisperBackend:
    """Local Whisper (the ``[asr]`` extra). Nothing leaves the machine — ``off_machine = False``."""

    off_machine = False

    def __init__(self, model: str = "base") -> None:
        self.model = model
        self.name = "whisper-local"

    def version(self) -> str:
        whisper = _load_whisper()
        return f"{getattr(whisper, '__version__', 'unknown')}/{self.model}"

    def transcribe(self, path: Path) -> list[Segment]:
        whisper = _load_whisper()
        try:
            model = whisper.load_model(self.model)  # type: ignore[attr-defined]
            result = model.transcribe(str(path))
        except Exception as exc:  # noqa: BLE001 — whisper raises assorted runtime errors
            raise MediaExtractorUnavailableError(f"local transcription failed for {path}: {exc}") from exc
        return _segments_from_whisper(result.get("segments", []))


class ApiAsrBackend:
    """A remote OpenAI-compatible transcription API. Uploads audio — ``off_machine = True``.

    Guarded by :func:`extract_media`'s consent gate; the API key is read from an env var, never a
    flag, so it can't leak into shell history or the artifact.
    """

    off_machine = True

    def __init__(
        self, endpoint: str, *, model: str = "whisper-1", api_key_env: str = "OPENAI_API_KEY"
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.api_key_env = api_key_env
        self.name = "asr-api"

    def version(self) -> str:
        return f"{self.endpoint}/{self.model}"

    def transcribe(self, path: Path) -> list[Segment]:
        import httpx  # base dependency; imported here to keep pkg import-light

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise MediaExtractorUnavailableError(
                f"remote ASR needs an API key in ${self.api_key_env} (never passed as a flag)."
            )
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"model": self.model, "response_format": "verbose_json"},
                    files={"file": (path.name, handle)},
                    timeout=600.0,
                )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 — network/HTTP/JSON errors all surface the same way
            raise MediaExtractorUnavailableError(f"remote transcription failed for {path}: {exc}") from exc
        return _segments_from_whisper(payload.get("segments", []))


def _load_whisper() -> object:
    try:
        import whisper
    except ImportError as exc:
        raise MediaExtractorUnavailableError(
            "local ASR needs the [asr] extra: pip install 'synaptixs-spine[asr]'. "
            "For a no-local-model option, use --asr api (which sends audio off-machine)."
        ) from exc
    return whisper


def _segments_from_whisper(raw: object) -> list[Segment]:
    """Normalise Whisper/OpenAI ``segments`` (start/end in seconds) into :class:`Segment` (ms)."""
    segments: list[Segment] = []
    if not isinstance(raw, list):
        return segments
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        segments.append(Segment(text, _to_ms(item.get("start")), _to_ms(item.get("end"))))
    return segments


def _to_ms(seconds: object) -> int | None:
    """Seconds (float/int) → integer milliseconds, or ``None`` when absent/unparseable."""
    if isinstance(seconds, (int, float)):
        return int(round(seconds * 1000))
    return None


def _media_kind(path: Path) -> str:
    return "audio" if path.suffix.lower() in AUDIO_SUFFIXES else "video"


def _bounded(segments: list[Segment]) -> tuple[list[Segment], bool]:
    """Apply the segment-count and duration caps; return the kept segments and whether we truncated."""
    truncated = False
    if len(segments) > _MAX_SEGMENTS:
        segments = segments[:_MAX_SEGMENTS]
        truncated = True
    kept: list[Segment] = []
    for segment in segments:
        if segment.start_ms is not None and segment.start_ms > _MAX_DURATION_MS:
            truncated = True
            break
        kept.append(segment)
    return kept, truncated


def build_media_artifact(
    media_path: Path,
    repo_root: Path,
    backend: AsrBackend,
    *,
    version: str | None = None,
) -> dict[str, object]:
    """Build (don't write) the artifact for one audio/video file, segments carrying timestamps.

    Pure given the backend's output — the backend holds all the model/network nondeterminism.
    """
    sha = _file_sha256(media_path)
    if sha is None:
        raise MediaExtractorUnavailableError(f"could not read {media_path}")
    kept, truncated = _bounded(backend.transcribe(media_path))
    rel = media_path.resolve().relative_to(repo_root.resolve()).as_posix()
    # Drop empty/whitespace segments so a committed artifact carries only real transcript text,
    # regardless of what a given backend emits (Whisper occasionally yields blank segments).
    segments = [
        {"text": s.text.strip(), "page": None, "start_ms": s.start_ms, "end_ms": s.end_ms}
        for s in kept
        if s.text.strip()
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_file": rel,
        "source_sha256": sha,
        "media_kind": _media_kind(media_path),
        "extractor": backend.name,
        "extractor_version": version or backend.version(),
        "truncated": truncated,
        "segments": segments,
    }


def extract_media(
    media_path: Path,
    repo_root: Path,
    backend: AsrBackend,
    *,
    allow_remote: bool = False,
    force: bool = False,
    version: str | None = None,
) -> ExtractResult:
    """Transcribe one audio/video file to a committed artifact.

    Enforces the consent gate first: a remote backend won't run without ``allow_remote=True``.
    Then skips oversized files and up-to-date artifacts, exactly like the image path.
    """
    if backend.off_machine and not allow_remote:
        raise RemoteConsentRequiredError(
            f"backend {backend.name!r} would upload {media_path.name} off-machine. "
            "Re-run with --allow-remote to consent, or use --asr local."
        )
    try:
        if media_path.stat().st_size > _MAX_MEDIA_BYTES:
            return ExtractResult(media_path, "skipped-too-large")
    except OSError as exc:
        raise MediaExtractorUnavailableError(f"could not read {media_path}") from exc

    sha = _file_sha256(media_path)
    if sha is None:
        raise MediaExtractorUnavailableError(f"could not read {media_path}")
    existing = artifact_path(repo_root, sha)
    if existing.is_file() and not force:
        return ExtractResult(media_path, "unchanged", existing)

    artifact = build_media_artifact(media_path, repo_root, backend, version=version)
    path = write_artifact(repo_root, artifact)
    segments = artifact["segments"]
    n = len(segments) if isinstance(segments, list) else 0
    return ExtractResult(media_path, "written", path, n, bool(artifact["truncated"]))


__all__ = [
    "ApiAsrBackend",
    "AsrBackend",
    "LocalWhisperBackend",
    "RemoteConsentRequiredError",
    "Segment",
    "build_media_artifact",
    "extract_media",
]
