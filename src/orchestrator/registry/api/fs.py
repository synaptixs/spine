"""Server-side filesystem browser (read-only directory listings).

The config files the Connections page reads live on the **server's** disk, so a
browser ``<input type=file>`` (which only yields file *content*) can't pick one.
``GET /v1/fs/list`` lists a directory's entries so the UI can offer a native-feel
file picker. Auth-gated and **listings only** — it returns names + is-dir, never
file contents. Consistent with the "any absolute path" config-path policy; a
default (no ``path``) starts at the workspace root.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from orchestrator.registry.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/fs", tags=["fs"])

_MAX_ENTRIES = 1000


class FsEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    is_dir: bool


class FsListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str  # the resolved directory being listed
    parent: str | None  # its parent (null at the filesystem root)
    home: str  # the user's home dir, for a quick-jump
    entries: list[FsEntry]  # directories first, then files, name-sorted
    truncated: bool


def _default_dir(request: Request) -> Path:
    from orchestrator.registry.api.workspace import workspace_root

    return workspace_root(request.app.state.settings)


@router.get("/list", response_model=FsListing)
async def list_dir(request: Request, _principal: PrincipalDep, path: str | None = None) -> FsListing:
    base = Path(path).expanduser() if path and path.strip() else _default_dir(request)
    try:
        resolved = base.resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid path") from exc
    if not resolved.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"not a directory: {resolved}")

    raw: list[tuple[str, str, bool]] = []
    try:
        for p in resolved.iterdir():
            try:
                is_dir = p.is_dir()
            except OSError:  # broken symlink / unreadable entry — skip it
                continue
            raw.append((p.name, str(p), is_dir))
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"permission denied: {resolved}"
        ) from exc

    raw.sort(key=lambda t: (not t[2], t[0].lower()))  # dirs first, then case-insensitive name
    entries = [FsEntry(name=n, path=pp, is_dir=d) for n, pp, d in raw[:_MAX_ENTRIES]]
    parent = str(resolved.parent) if resolved.parent != resolved else None
    return FsListing(
        path=str(resolved),
        parent=parent,
        home=str(Path.home()),
        entries=entries,
        truncated=len(raw) > _MAX_ENTRIES,
    )
