"""Knowledge graph: the ``media`` sub-app — OCR and transcription into graph content."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

media_app = typer.Typer(
    help=(
        "Media ingestion — OCR images and transcribe audio/video into graph content.\n\n"
        "Explicit and opt-in; local by default, and `--asr api` uploads off-machine only with "
        "`--allow-remote`."
    ),
    no_args_is_help=True,
)


@media_app.command("extract")
def media_extract(
    paths: Annotated[
        list[Path], typer.Argument(help="Media file(s)/director(ies): images (OCR) + audio/video (ASR).")
    ],
    repo_root: Annotated[
        Path, typer.Option("--repo-root", help="Root whose .spine-media/ receives the artifacts.")
    ] = Path("."),
    force: Annotated[
        bool, typer.Option("--force", help="Re-extract even if an up-to-date artifact exists.")
    ] = False,
    asr: Annotated[
        str, typer.Option("--asr", help="Audio/video backend: 'local' (Whisper) or 'api' (remote).")
    ] = "local",
    whisper_model: Annotated[
        str, typer.Option("--whisper-model", help="Local Whisper model size (tiny/base/small/…).")
    ] = "base",
    api_endpoint: Annotated[
        str | None,
        typer.Option("--api-endpoint", help="OpenAI-compatible transcription URL (with --asr api)."),
    ] = None,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Consent to uploading audio/video OFF-MACHINE (--asr api)."),
    ] = False,
) -> None:
    """OCR images and transcribe audio/video into reviewable artifacts under .spine-media/.

    Explicit and opt-in: this MAY run a model and be slow. Image OCR and `--asr local` run entirely
    on this machine. `--asr api` UPLOADS audio/video to a remote service and therefore requires
    `--allow-remote`. The deterministic graph build (`understand`/`state`) never runs this; it only
    reads the committed artifacts. Review and commit the .spine-media/ files afterward.
    """
    from orchestrator.pkg.media import AUDIO_VIDEO_SUFFIXES, IMAGE_SUFFIXES
    from orchestrator.pkg.media_asr import (
        ApiAsrBackend,
        AsrBackend,
        LocalWhisperBackend,
        RemoteConsentRequiredError,
        extract_media,
    )
    from orchestrator.pkg.media_extract import (
        MediaExtractorUnavailableError,
        extract_image,
        iter_media_files,
    )

    files = iter_media_files(paths)
    if not files:
        typer.echo("No supported media (.png/.jpg/.webp/.mp3/.wav/.mp4/.mov) in the given path(s).")
        raise typer.Exit(code=2)

    needs_asr = any(f.suffix.lower() in AUDIO_VIDEO_SUFFIXES for f in files)
    backend: AsrBackend | None = None
    if needs_asr:
        if asr == "local":
            backend = LocalWhisperBackend(whisper_model)
        elif asr == "api":
            if not api_endpoint:
                typer.echo("ERROR: --asr api needs --api-endpoint <url>.", err=True)
                raise typer.Exit(code=2)
            backend = ApiAsrBackend(api_endpoint)
        else:
            typer.echo(f"ERROR: unknown --asr backend {asr!r} (use 'local' or 'api').", err=True)
            raise typer.Exit(code=2)
        where = "OFF-MACHINE (remote API)" if backend.off_machine else "local — nothing leaves this machine"
        typer.echo(f"Audio/video transcription: {where}.")

    written = 0
    try:
        for media in files:
            if media.suffix.lower() in IMAGE_SUFFIXES:
                result = extract_image(media, repo_root, force=force)
                unit = "label(s)"
            else:
                assert backend is not None  # noqa: S101  (type-narrowing for mypy; set whenever needs_asr)
                result = extract_media(media, repo_root, backend, allow_remote=allow_remote, force=force)
                unit = "segment(s)"
            if result.status == "written":
                written += 1
                note = " (truncated)" if result.truncated else ""
                typer.echo(f"  wrote {result.artifact}  · {result.segments} {unit}{note}")
            elif result.status == "unchanged":
                typer.echo(f"  unchanged {media} (artifact exists; --force to re-extract)")
            elif result.status == "skipped-too-large":
                typer.echo(f"  skipped {media} (larger than the size cap)")
    except RemoteConsentRequiredError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except MediaExtractorUnavailableError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Done — {written} artifact(s) written. Review and commit .spine-media/ to ingest them.")
