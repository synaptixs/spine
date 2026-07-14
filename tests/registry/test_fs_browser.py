"""Server-side filesystem browser (/v1/fs/list) — for the config file picker."""

from __future__ import annotations

from pathlib import Path

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_AUTH = {"X-API-Key": "dev-key"}


def _app(root: Path) -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", workspace_root=str(root)))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


async def test_default_lists_workspace_root_dirs_first(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "mcp.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    async with _client(_app(tmp_path)) as c:
        d = (await c.get("/v1/fs/list")).json()  # no path → workspace root
    assert d["path"] == str(tmp_path.resolve())
    names = [e["name"] for e in d["entries"]]
    assert names[0] == "sub"  # directories sorted first
    assert set(names) == {"sub", "mcp.json", "a.txt"}
    assert {e["name"]: e["is_dir"] for e in d["entries"]}["sub"] is True
    assert "home" in d


async def test_navigates_into_subdir_and_reports_parent(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "mcp.json").write_text("{}", encoding="utf-8")
    async with _client(_app(tmp_path)) as c:
        d = (await c.get(f"/v1/fs/list?path={tmp_path / 'sub'}")).json()
    assert d["path"] == str((tmp_path / "sub").resolve())
    assert d["parent"] == str(tmp_path.resolve())
    assert [e["name"] for e in d["entries"]] == ["mcp.json"]


async def test_404_for_non_directory(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    async with _client(_app(tmp_path)) as c:
        assert (await c.get(f"/v1/fs/list?path={f}")).status_code == 404
        assert (await c.get(f"/v1/fs/list?path={tmp_path / 'nope'}")).status_code == 404


async def test_requires_auth(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        assert (await c.get("/v1/fs/list")).status_code == 401
