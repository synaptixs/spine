"""Local-file source adapter: requirements straight off the filesystem.

The lowest-friction ``SourceAdapter`` — no SaaS account, no API token. Point
``--source file://<path>`` at a markdown/text file or a directory of them and
the same intake pipeline (intents → gaps → specs) runs against local docs.
This is the bring-your-own-stack seam (``orchestrator.intake.source``) taken
to its simplest end: a developer evaluating the orchestrator can produce a
backlog from a spec file in the repo without provisioning Confluence or Notion.

``root_id`` is a filesystem path (parsed from the URI by ``parse_source_uri``,
which keeps the path verbatim for the ``file`` kind). A file root yields one
document; a directory root is walked breadth-first — depth/-doc capped, the
same as the API adapters — collecting text-like files. Binary, oversized, and
empty files are skipped. An optional ``FILE_SOURCE_ROOT`` confines every read
to a sandbox base dir for any non-local/server use; unset (the default) means
paths resolve from the current working directory.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orchestrator.intake.source import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOCS,
    FetchTreeResult,
    SourceDocument,
    SourceRef,
)

# Extensions we treat as readable requirements text. Anything else in a walked
# directory is skipped (no point feeding a PNG or a lockfile to the extractor).
_TEXT_EXTENSIONS = (".md", ".markdown", ".txt", ".rst")
# Skip files larger than this — a requirements doc is prose, not a data dump,
# and an accidental giant file shouldn't blow up the LLM context or the walk.
_MAX_FILE_BYTES = 1_000_000


class FileSourceError(RuntimeError):
    """Raised when a local path can't be read as a requirements source."""


class FileSourceConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILE_SOURCE_", env_file=".env", extra="ignore")

    root: str = Field(
        default="",
        description="Optional sandbox base dir; when set, reads are confined to it (server/CI use).",
    )

    @property
    def configured(self) -> bool:
        # No credentials to provide — the local filesystem is always available.
        # The factory still calls this so file:// shares the builder contract
        # with the API-backed adapters.
        return True


def _derive_title(text: str, path: Path) -> str:
    """A markdown ``# H1`` if the doc opens with one, else the filename stem."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return stripped[2:].strip() or path.stem
        break  # first non-blank line isn't an H1 — fall back to the filename
    return path.stem


class FileSourceAdapter:
    """``SourceAdapter`` over the local filesystem."""

    source_kind = "file"

    def __init__(self, config: FileSourceConfig | None = None) -> None:
        self._config = config or FileSourceConfig()
        root = self._config.root.strip()
        self._sandbox = Path(root).expanduser().resolve() if root else None

    def _resolve(self, raw: str) -> Path:
        """Resolve a source path, enforcing the optional sandbox.

        Without a sandbox, relative paths resolve from the CWD (where the dev
        ran the CLI). With ``FILE_SOURCE_ROOT`` set, relative paths are joined
        under it and the resolved path must stay inside it.
        """
        candidate = Path(raw).expanduser()
        if self._sandbox is not None and not candidate.is_absolute():
            candidate = self._sandbox / candidate
        resolved = candidate.resolve()
        if self._sandbox is not None and resolved != self._sandbox and self._sandbox not in resolved.parents:
            raise FileSourceError(f"path {raw!r} escapes the FILE_SOURCE_ROOT sandbox ({self._sandbox}).")
        return resolved

    @staticmethod
    def _read_text(path: Path) -> str:
        """Read a text file, guarding size; undecodable bytes are replaced."""
        size = path.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise FileSourceError(f"{path} is {size} bytes (> {_MAX_FILE_BYTES} cap); skipping.")
        return path.read_text(encoding="utf-8", errors="replace")

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        path = self._resolve(doc_id)
        if not path.is_file():
            raise FileSourceError(f"not a file: {path}")
        text = await asyncio.to_thread(self._read_text, path)
        return SourceDocument(
            id=str(path),
            title=_derive_title(text, path),
            body=text,
            url=path.as_uri(),
        )

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        """Sub-dirs (kind ``folder``) and text files (kind ``page``) of a dir.

        A file has no children. Hidden entries (dotfiles/dotdirs) are skipped.
        """
        path = self._resolve(doc_id)
        if not path.is_dir():
            return []
        entries = await asyncio.to_thread(lambda: sorted(path.iterdir()))
        refs: list[SourceRef] = []
        for child in entries:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                refs.append(SourceRef(id=str(child), title=child.name, kind="folder"))
            elif child.is_file() and child.suffix.lower() in _TEXT_EXTENSIONS:
                refs.append(SourceRef(id=str(child), title=child.name, kind="page"))
        return refs

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult:
        """One document for a file root; a capped BFS over a directory root.

        Files are leaves (collected, never descended); ``max_depth`` gates how
        far the walk descends into sub-directories. Empty docs are dropped so
        a stray blank file doesn't become a no-op intent.
        """
        result = FetchTreeResult()
        root = self._resolve(root_id)
        if root.is_file():
            doc = await self.fetch_document(str(root))
            if not doc.is_empty:
                result.documents.append(doc)
            return result
        if not root.is_dir():
            raise FileSourceError(f"no such file or directory: {root}")

        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(str(root), 0)])
        while queue:
            node, depth = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            for ref in await self.list_children(node):
                if ref.kind == "folder":
                    if depth < max_depth:
                        queue.append((ref.id, depth + 1))
                    continue
                if len(result.documents) >= max_docs:
                    result.truncated = True
                    return result
                doc = await self.fetch_document(ref.id)
                if not doc.is_empty:
                    result.documents.append(doc)
        return result


__all__ = ["FileSourceAdapter", "FileSourceConfig", "FileSourceError"]
