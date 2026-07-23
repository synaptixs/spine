"""Media ASR (G3, phase 3): `media extract` transcribes audio/video → committed artifacts.

Same shape as test_media_extract: the backend is injected so the whole
backend→artifact→reader→binding loop runs without Whisper or any network. The two load-bearing
tests are the exit criterion (`test_recorded_review_becomes_searchable_doc`) and the invariant-#5
consent gate (`test_remote_backend_refuses_without_consent`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore, link_docs
from orchestrator.pkg import media_asr as asr
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.media import read_media_artifact


def _code_batch() -> FactBatch:
    b = FactBatch()
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


class _FakeBackend:
    """A deterministic stand-in for Whisper/an API, so tests need neither a model nor a network."""

    def __init__(self, segments: list[asr.Segment], *, off_machine: bool = False) -> None:
        self._segments = segments
        self.off_machine = off_machine
        self.name = "fake-api" if off_machine else "fake-local"

    def version(self) -> str:
        return "fake-1"

    def transcribe(self, path: Path) -> list[asr.Segment]:
        return self._segments


_REVIEW = [
    asr.Segment("Today we review the Invoice type.", 0, 4000),
    asr.Segment("It applies calc_tax to each line.", 4000, 8000),
    asr.Segment("   ", 8000, 9000),  # a blank segment Whisper sometimes emits
]


def _audio(root: Path, rel: str = "review.wav") -> Path:
    media = root / rel
    media.write_bytes(f"fake-audio::{rel}".encode())
    return media


# ---- artifact building ------------------------------------------------------


def test_build_media_artifact_preserves_timestamps_and_kind(tmp_path: Path) -> None:
    art = asr.build_media_artifact(_audio(tmp_path), tmp_path, _FakeBackend(_REVIEW), version="whisper-test")
    assert art["media_kind"] == "audio"
    assert art["extractor"] == "fake-local" and art["extractor_version"] == "whisper-test"
    segs = art["segments"]
    assert isinstance(segs, list) and len(segs) == 2  # blank segment dropped
    assert segs[0]["start_ms"] == 0 and segs[0]["end_ms"] == 4000
    assert segs[1]["start_ms"] == 4000 and segs[1]["end_ms"] == 8000


def test_video_suffix_is_kind_video(tmp_path: Path) -> None:
    art = asr.build_media_artifact(
        _audio(tmp_path, "demo.mov"), tmp_path, _FakeBackend([asr.Segment("hi", 0, 1000)]), version="v"
    )
    assert art["media_kind"] == "video"


def test_seconds_convert_to_milliseconds() -> None:
    assert asr._to_ms(1.5) == 1500
    assert asr._to_ms(0) == 0
    assert asr._to_ms(None) is None
    assert asr._to_ms("nope") is None


def test_segments_from_whisper_normalises_and_drops_blanks() -> None:
    raw = [{"start": 0.0, "end": 1.2, "text": " hello "}, {"start": 1.2, "end": 2.0, "text": "  "}]
    segs = asr._segments_from_whisper(raw)
    assert [(s.text, s.start_ms, s.end_ms) for s in segs] == [("hello", 0, 1200)]


# ---- extract_media (write + reader round-trip) ------------------------------


def test_extract_media_writes_and_reader_round_trips(tmp_path: Path) -> None:
    result = asr.extract_media(_audio(tmp_path), tmp_path, _FakeBackend(_REVIEW), version="whisper-test")
    assert result.status == "written" and result.artifact is not None
    text = read_media_artifact(_audio(tmp_path))
    assert text is not None and "calc_tax" in text and "Invoice" in text


def test_extract_media_is_deterministic_for_a_pinned_version(tmp_path: Path) -> None:
    audio = _audio(tmp_path)
    r1 = asr.extract_media(audio, tmp_path, _FakeBackend(_REVIEW), version="whisper-test")
    assert r1.artifact is not None
    first = r1.artifact.read_bytes()
    asr.extract_media(audio, tmp_path, _FakeBackend(_REVIEW), version="whisper-test", force=True)
    assert r1.artifact.read_bytes() == first


# ---- honest bounding (invariant #4) -----------------------------------------


def test_duration_cap_truncates_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asr, "_MAX_DURATION_MS", 5000)
    long_review = [asr.Segment("early", 0, 1000), asr.Segment("late", 6000, 6500)]
    art = asr.build_media_artifact(_audio(tmp_path), tmp_path, _FakeBackend(long_review), version="v")
    segments = art["segments"]
    assert art["truncated"] is True
    assert isinstance(segments, list)
    assert [s["text"] for s in segments] == ["early"]  # past the cap dropped


def test_segment_count_cap_truncates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asr, "_MAX_SEGMENTS", 2)
    many = [asr.Segment(f"seg{i}", i * 1000, i * 1000 + 500) for i in range(5)]
    art = asr.build_media_artifact(_audio(tmp_path), tmp_path, _FakeBackend(many), version="v")
    segments = art["segments"]
    assert art["truncated"] is True
    assert isinstance(segments, list) and len(segments) == 2


def test_oversized_media_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asr, "_MAX_MEDIA_BYTES", 4)
    result = asr.extract_media(_audio(tmp_path), tmp_path, _FakeBackend(_REVIEW), version="v")
    assert result.status == "skipped-too-large" and result.artifact is None


# ---- invariant #5: no data leaves the machine without explicit consent ------


def test_remote_backend_refuses_without_consent(tmp_path: Path) -> None:
    remote = _FakeBackend(_REVIEW, off_machine=True)
    # A remote backend must not run — and therefore must not upload — without allow_remote.
    with pytest.raises(asr.RemoteConsentRequired):
        asr.extract_media(_audio(tmp_path), tmp_path, remote, allow_remote=False)


def test_remote_backend_runs_with_consent(tmp_path: Path) -> None:
    remote = _FakeBackend(_REVIEW, off_machine=True)
    result = asr.extract_media(_audio(tmp_path), tmp_path, remote, allow_remote=True)
    assert result.status == "written"


def test_local_backend_needs_no_consent(tmp_path: Path) -> None:
    # A local backend is off_machine=False, so it runs without allow_remote — no gate.
    result = asr.extract_media(_audio(tmp_path), tmp_path, _FakeBackend(_REVIEW), allow_remote=False)
    assert result.status == "written"


# ---- exit criterion: a recorded review is a searchable, timestamped Doc -----


def test_recorded_review_becomes_searchable_doc(tmp_path: Path) -> None:
    """transcribe → artifact → reader → link_docs: the review's speech binds to real symbols."""
    asr.extract_media(_audio(tmp_path), tmp_path, _FakeBackend(_REVIEW), version="whisper-test")
    store = FactStore(link_docs(_code_batch(), tmp_path))
    mentioned = {n.name for n in store.mentions_of("doc:review.wav")}
    # The snake_case identifier `calc_tax` binds — the recording is now searchable by symbol.
    # The spoken word "Invoice" is a single capitalised word, which the binder treats as prose
    # (precision-first, invariant #6), so it does NOT bind from a plain transcript. This is a real
    # property of ASR ingestion: snake_case/dotted identifiers survive as code claims, bare
    # single-word type names do not.
    assert mentioned == {"calc_tax"}
