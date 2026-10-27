"""The shared server's repository allow-list: only what the admin listed, through every door.

A path reaches the server's disk through five argument kinds, not one — a repository path, a
``repos.yaml`` naming more repositories, an OpenSpec change directory, an intake source URI, and
the build tools' report files. Each refusal test changes one thing about an allowed call.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin.shared import (
    REPOS_ENV,
    RepoAllowList,
    allow_list_from_env,
    refusal,
)


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    repos = tmp_path / "repos"
    app, lib, outside = repos / "app", repos / "lib", tmp_path / "outside"
    for d in (app / "pkg", lib, outside / "secret"):
        d.mkdir(parents=True)
    (app / "openspec" / "changes" / "c1").mkdir(parents=True)
    (outside / "secret" / "spec.md").write_text("nope")
    (app / "spec.md").write_text("ok")
    os.symlink(app, tmp_path / "via")  # a link *outside* every listed root, pointing into one
    admin = tmp_path / "srv" / ".spine" / "repos.yaml"
    admin.parent.mkdir(parents=True)
    admin.write_text(f"repos:\n  app: {app}\n  lib: {lib}\n")
    other = tmp_path / "other.yaml"
    other.write_text(f"repos:\n  secret: {outside}\n")
    return {"app": app, "lib": lib, "outside": outside, "admin": admin, "other": other, "root": tmp_path}


@pytest.fixture
def allow(layout: dict[str, Path]) -> RepoAllowList:
    return RepoAllowList.load(layout["admin"])


# ---- loading the list ---------------------------------------------------------------------


def test_the_list_names_its_repositories_by_their_real_paths(
    allow: RepoAllowList, layout: dict[str, Path]
) -> None:
    assert allow.keys == ("app", "lib")
    assert allow.roots["app"] == Path(os.path.realpath(layout["app"]))
    assert allow.config == Path(os.path.realpath(layout["admin"]))


def test_a_listed_repository_that_does_not_exist_stops_the_server(tmp_path: Path) -> None:
    admin = tmp_path / "repos.yaml"
    admin.write_text(f"repos:\n  gone: {tmp_path / 'gone'}\n")
    with pytest.raises(ValueError, match="gone"):
        RepoAllowList.load(admin)


@pytest.mark.parametrize("text", ["", "repos: []\n", "repos: {}\n", "not: a list\n", "repos:\n  x: [1, 2]\n"])
def test_a_malformed_list_stops_the_server(tmp_path: Path, text: str) -> None:
    admin = tmp_path / "repos.yaml"
    admin.write_text(text)
    with pytest.raises(ValueError):
        RepoAllowList.load(admin)


def test_a_missing_list_file_stops_the_server(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be read"):
        RepoAllowList.load(tmp_path / "nope.yaml")


def test_a_git_url_in_the_list_is_refused_in_v1(tmp_path: Path) -> None:
    admin = tmp_path / "repos.yaml"
    admin.write_text("repos:\n  app: https://github.com/acme/app\n")
    with pytest.raises(ValueError, match="checkout"):
        RepoAllowList.load(admin)


def test_no_list_in_env_means_no_list(monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]) -> None:
    monkeypatch.delenv(REPOS_ENV, raising=False)
    assert allow_list_from_env() is None
    monkeypatch.setenv(REPOS_ENV, str(layout["admin"]))
    found = allow_list_from_env()
    assert found is not None and found.keys == ("app", "lib")


# ---- one rule per argument kind ----------------------------------------------------------


def _ok(allow: RepoAllowList, decl: dict[str, str], **arguments: Any) -> bool:
    return refusal(allow, decl, arguments) is None


def test_a_repository_path_must_be_a_listed_root_or_inside_one(
    allow: RepoAllowList, layout: dict[str, Path]
) -> None:
    decl = {"repo_path": "repo"}
    assert _ok(allow, decl, repo_path=str(layout["app"]))
    assert _ok(allow, decl, repo_path=str(layout["app"] / "pkg"))  # a package of a monorepo
    assert _ok(allow, decl, repo_path=str(layout["lib"]) + "/")
    assert not _ok(allow, decl, repo_path=str(layout["outside"]))
    assert not _ok(allow, decl, repo_path=str(layout["root"] / "repos"))  # the parent of two roots
    assert not _ok(allow, decl, repo_path=str(layout["app"]) + "-sibling")  # a prefix is not inside
    assert not _ok(allow, decl, repo_path=str(layout["app"] / ".." / ".." / "outside"))
    assert _ok(allow, decl, repo_path=str(layout["root"] / "via" / "pkg"))  # judged by where it lands
    assert not _ok(allow, decl, repo_path="/")


@pytest.mark.parametrize("value", ["", ".", "./"])
def test_the_servers_own_directory_is_not_a_repository(allow: RepoAllowList, value: str) -> None:
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=value)


@pytest.mark.parametrize(
    "value", ["https://github.com/acme/app", "git@github.com:acme/app.git", "ssh://git@host/x", "file:///etc"]
)
def test_a_url_is_refused_where_a_repository_is_asked_for(allow: RepoAllowList, value: str) -> None:
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=value)


def test_the_working_directory_is_refused_even_when_it_is_inside_a_listed_repository(
    allow: RepoAllowList, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.`` and an empty path mean "wherever the server happens to run" — refused by rule, not
    by the accident of where that is."""
    monkeypatch.chdir(layout["app"])
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=".")
    assert not _ok(allow, {"repo_path": "repo"}, repo_path="")
    assert not _ok(allow, {"change_path": "inside"}, change_path="")
    assert not _ok(allow, {"source": "source"}, source="file://")
    # A URL resolved as a relative path would land inside the checkout; it is refused as a URL.
    assert not _ok(allow, {"repo_path": "repo"}, repo_path="git@evil.example:org/repo.git")
    # `~` is expanded by the check but not by every tool, which would open it under the working
    # directory instead: refused, so the string checked is the string opened.
    assert not _ok(allow, {"repo_path": "repo"}, repo_path="~")
    assert not _ok(allow, {"repo_path": "repo"}, repo_path="~/x")
    assert not _ok(allow, {"repos": "repos_file"}, repos="~/.spine/repos.yaml")
    assert not _ok(allow, {"source": "source"}, source="file://~/spec.md")
    # A `.git` component: git's own files are not a tool's to read or write.
    assert not _ok(allow, {"change_path": "inside"}, change_path=str(layout["app"] / ".git" / "hooks"))
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=str(layout["app"] / ".git"))
    # Padded values: the tool would read a different string from the one checked.
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=" ")
    assert not _ok(allow, {"repo_path": "repo"}, repo_path=" " + str(layout["app"]))
    assert not _ok(allow, {"change_path": "inside"}, change_path="openspec ")
    assert _ok(allow, {"repo_path": "repo"}, repo_path=str(layout["app"]))


def test_an_omitted_optional_repository_is_the_admins_own_default(allow: RepoAllowList) -> None:
    assert _ok(allow, {"repo": "repo"}, repo=None)
    assert _ok(allow, {"repo_path": "repo_optional"}, repo_path="")  # "no repository" is a real answer
    assert not _ok(allow, {"repo_path": "repo_optional"}, repo_path=".")
    # Whitespace is not "no repository": the tool would strip it to "." — the server's own directory.
    for blank in (" ", "\n", "\t "):
        assert not _ok(allow, {"repo_path": "repo_optional"}, repo_path=blank)
        assert not _ok(allow, {"repo_path": "repo", "repos": "repos_file"}, repo_path=blank, repos=None)


def test_a_repos_file_must_be_the_admins_own_list(allow: RepoAllowList, layout: dict[str, Path]) -> None:
    decl = {"repo_path": "repo", "repos": "repos_file"}
    assert _ok(allow, decl, repo_path="", repos=str(layout["admin"]))  # answers across the listed set
    assert not _ok(allow, decl, repo_path="", repos=str(layout["other"]))
    assert not _ok(allow, decl, repo_path="", repos=str(layout["app"] / "repos.yaml"))
    assert _ok(allow, decl, repo_path=str(layout["app"]), repos=None)


def test_the_default_relative_config_works_from_the_servers_directory(
    allow: RepoAllowList, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(layout["admin"].parent.parent)
    assert _ok(allow, {"config": "repos_file"}, config=".spine/repos.yaml")
    monkeypatch.chdir(layout["root"])
    assert not _ok(allow, {"config": "repos_file"}, config=".spine/repos.yaml")


def test_a_path_inside_a_repository_must_stay_inside(allow: RepoAllowList, layout: dict[str, Path]) -> None:
    decl = {"change_path": "inside"}
    assert _ok(allow, decl, change_path=str(layout["app"] / "openspec" / "changes" / "c1"))
    assert not _ok(allow, decl, change_path=str(layout["outside"] / "secret"))
    assert _ok(allow, decl, change_path=str(layout["root"] / "via" / "openspec" / "changes" / "c1"))
    assert not _ok(allow, decl, change_path="")


def test_an_intake_source_may_only_be_a_file_inside_a_repository(
    allow: RepoAllowList, layout: dict[str, Path]
) -> None:
    decl = {"source": "source"}
    assert _ok(allow, decl, source=f"file://{layout['app'] / 'spec.md'}")
    assert not _ok(allow, decl, source=f"file://{layout['outside'] / 'secret' / 'spec.md'}")
    assert not _ok(allow, decl, source="file://./spec.md")  # the server's own directory
    for uri in ("jira://PROJ-1", "confluence://123", "notion://abc", "https://example.test/spec"):
        assert not _ok(allow, decl, source=uri)  # the server's credentials are not the caller's


def test_a_refusal_names_the_argument_and_the_listed_keys_but_no_other_path(
    allow: RepoAllowList, layout: dict[str, Path]
) -> None:
    message = refusal(allow, {"repo_path": "repo"}, {"repo_path": str(layout["outside"])})
    assert message is not None
    assert "repo_path" in message and "app" in message and "lib" in message
    assert str(layout["app"]) not in message  # keys, not the server's paths


def test_an_unknown_kind_is_a_programming_error(allow: RepoAllowList) -> None:
    with pytest.raises(KeyError):
        refusal(allow, {"repo_path": "sideways"}, {"repo_path": "x"})


# ---- every tool declares its doors ---------------------------------------------------------


def test_every_tool_declares_its_path_arguments() -> None:
    from orchestrator.plugin.server import _PATH_ARGS, _TOOLS, PATH_LIKE, path_like_parameters

    names = {fn.__name__ for fn in _TOOLS}
    assert set(_PATH_ARGS) == names
    for fn in _TOOLS:
        declared = set(_PATH_ARGS[fn.__name__])
        assert path_like_parameters(fn) <= declared, fn.__name__
        assert declared <= set(inspect.signature(fn).parameters), fn.__name__
    assert PATH_LIKE.search("repo_path") and PATH_LIKE.search("mappings_path") and PATH_LIKE.search("source")
    assert PATH_LIKE.search("out") and PATH_LIKE.search("out_dir") and PATH_LIKE.search("dest")
    assert (
        not PATH_LIKE.search("symbol") and not PATH_LIKE.search("timeout") and not PATH_LIKE.search("layout")
    )


def test_the_measured_doors_are_declared_as_measured() -> None:
    """The five argument kinds the P5 plan found, pinned so a reclassification is a visible diff."""
    from orchestrator.plugin.server import _PATH_ARGS

    assert _PATH_ARGS["blast_radius"] == {"repo_path": "repo", "repos": "repos_file"}
    assert _PATH_ARGS["pkg_joins"] == {"config": "repos_file"}
    assert _PATH_ARGS["requirements_check"] == {"change_path": "inside", "repo_path": "repo_optional"}
    assert _PATH_ARGS["requirements_answer"] == {"change_path": "inside"}
    assert _PATH_ARGS["ingest_preview"] == {"source": "source"}
    assert _PATH_ARGS["sdlc_remediate"] == {
        "report_path": "inside",
        "mappings_path": "inside",
        "repo": "repo",
    }
    assert _PATH_ARGS["doctor"] == {}
    # #636's file_insights: a file named relative to the repository it is given.
    assert _PATH_ARGS["file_insights"] == {"repo_path": "repo", "path": "repo_relative"}
    # The security review found `out`: understand_repo writes and prunes markdown there.
    assert _PATH_ARGS["understand_repo"] == {"repo_path": "repo", "out": "inside"}


def test_a_tool_with_an_undeclared_path_argument_does_not_register(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin import server

    monkeypatch.setitem(server._PATH_ARGS, "map_repo", {})
    with pytest.raises(RuntimeError, match="map_repo"):
        server.build_server(level="pkg")


# ---- through the guard ---------------------------------------------------------------------


async def test_the_guard_refuses_an_unlisted_repository_before_the_tool_runs(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    from orchestrator.plugin import server

    ran: list[str] = []

    def map_repo(repo_path: str = "") -> dict[str, Any]:
        ran.append(repo_path)
        return {"files": 0}

    monkeypatch.setenv(REPOS_ENV, str(layout["admin"]))
    guarded = server._scoped(map_repo, "spine:read")
    out = await guarded(repo_path=str(layout["outside"]))
    assert ran == [] and "not a repository this server serves" in out["error"]
    assert out["allowed"] == ["app", "lib"]
    assert await guarded(repo_path=str(layout["app"])) == {"files": 0}
    assert await guarded(str(layout["lib"])) == {"files": 0}  # positional, as a direct call passes it
    assert ran == [str(layout["app"]), str(layout["lib"])]


async def test_understand_repo_cannot_write_outside_the_list(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    from orchestrator.plugin import server

    ran: list[str | None] = []

    def understand_repo(
        repo_path: str, check: bool = False, refresh: bool = False, out: str | None = None
    ) -> dict[str, Any]:
        ran.append(out)
        return {"written": 0}

    monkeypatch.setenv(REPOS_ENV, str(layout["admin"]))
    guarded = server._scoped(understand_repo, "spine:plan")
    out = await guarded(repo_path=str(layout["app"]), out=str(layout["outside"]))
    assert "out: not a repository this server serves" in out["error"]
    assert await guarded(repo_path=str(layout["app"]), out=str(layout["app"] / "episteme")) == {"written": 0}
    assert await guarded(repo_path=str(layout["app"])) == {"written": 0}  # the default: inside the repo
    assert ran == [str(layout["app"] / "episteme"), None]


async def test_without_a_list_the_laptop_is_unrestricted(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    from orchestrator.plugin import server

    def map_repo(repo_path: str = "") -> dict[str, Any]:
        return {"files": 0}

    monkeypatch.delenv(REPOS_ENV, raising=False)
    assert await server._scoped(map_repo, "spine:read")(repo_path=str(layout["outside"])) == {"files": 0}


async def test_an_unreadable_list_at_call_time_refuses_everything(
    monkeypatch: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    from orchestrator.plugin import server

    def map_repo(repo_path: str = "") -> dict[str, Any]:
        return {"files": 0}

    monkeypatch.setenv(REPOS_ENV, str(layout["admin"]))
    guarded = server._scoped(map_repo, "spine:read")
    assert await guarded(repo_path=str(layout["app"])) == {"files": 0}  # the good list, read once
    layout["admin"].write_text("garbage: [")
    out = await guarded(repo_path=str(layout["app"]))
    assert "repository list" in out["error"]  # the edit is seen, and the broken list serves nothing
    layout["admin"].write_text(f"repos:\n  lib: {layout['lib']}\n")
    out = await guarded(repo_path=str(layout["app"]))
    assert "not a repository this server serves" in out["error"]  # an admin's edit applies at once


# ---- the server and its entry point ---------------------------------------------------------


@pytest.fixture
def no_auth(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "ORCHESTRATOR_MCP_TOKEN",
        "ORCHESTRATOR_MCP_INTROSPECTION_URL",
        "ORCHESTRATOR_MCP_ISSUER_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(REPOS_ENV, raising=False)
    return monkeypatch


def test_a_public_server_without_a_repository_list_refuses_to_start(no_auth: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    from orchestrator.plugin.server import build_http_server

    no_auth.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    no_auth.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.test")
    with pytest.raises(RuntimeError, match="ORCHESTRATOR_MCP_REPOS"):
        build_http_server(host="0.0.0.0", port=8080)
    assert build_http_server(host="0.0.0.0", port=8080, allow_any_repo=True).server is not None
    assert build_http_server(host="127.0.0.1", port=8080).server is not None  # loopback: a developer's own


def test_a_public_server_with_a_list_starts_and_a_bad_list_stops_it(
    no_auth: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    pytest.importorskip("mcp")
    from orchestrator.plugin.server import build_http_server

    no_auth.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    no_auth.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.test")
    no_auth.setenv(REPOS_ENV, str(layout["admin"]))
    assert build_http_server(host="0.0.0.0", port=8080).server is not None
    no_auth.setenv(REPOS_ENV, str(layout["root"] / "missing.yaml"))
    with pytest.raises(ValueError, match="cannot be read"):
        build_http_server(host="0.0.0.0", port=8080)


def test_the_stdio_server_checks_a_configured_list_at_start(
    no_auth: pytest.MonkeyPatch, layout: dict[str, Path]
) -> None:
    pytest.importorskip("mcp")
    from orchestrator.plugin.server import build_server

    no_auth.setenv(REPOS_ENV, str(layout["root"] / "missing.yaml"))
    with pytest.raises(ValueError, match="cannot be read"):
        build_server(level="pkg")


def test_the_flag_and_the_audit_stream_reach_the_http_server(
    no_auth: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import orchestrator.plugin.__main__ as entry
    from orchestrator.plugin import audit
    from orchestrator.plugin import server as mod

    no_auth.chdir(tmp_path)
    no_auth.delenv("ORCHESTRATOR_DOTENV", raising=False)
    seen: dict[str, Any] = {}

    class _Stub:
        def run(self) -> None:
            seen["ran"] = True

    def fake_build_http_server(**kwargs: Any) -> _Stub:
        seen.update(kwargs)
        return _Stub()

    no_auth.setattr(mod, "build_http_server", fake_build_http_server)
    no_auth.setattr(audit, "configure_audit_logging", lambda: seen.setdefault("audit_logging", True))
    entry.main(["--http", "--allow-any-repo"])
    assert seen["allow_any_repo"] is True and seen["ran"] is True and seen["audit_logging"] is True
    seen.clear()
    entry.main(["--http"])
    assert seen["allow_any_repo"] is False


# ---- a checkout holding a link that leaves it is not served ----------------------------------
#
# A listed repository is content other people push. A committed link (`episteme -> /etc`,
# `.spine/plans/x-build.md -> ~/.bashrc`) would carry a tool's reads and writes out of it, past
# the list — three review rounds found a new reader or writer each time. So the property is
# checked once, for the whole checkout: any link whose target leaves it, dangling or not, and the
# server refuses the repository until the admin removes the link.


def _list(layout: dict[str, Path]) -> RepoAllowList:
    return RepoAllowList.load(layout["admin"])


@pytest.mark.parametrize(
    ("link", "target"),
    [
        ("episteme", "outside"),  # a directory link out
        ("episteme/README.md", "outside/secret/spec.md"),  # a file link out
        (".spine/plans/x-build.md", "outside/new-file.md"),  # dangling: a write would create it
        ("deep/er/n.md", "repos/lib/x.md"),  # into another listed repo still leaves this one
    ],
)
def test_a_link_leaving_the_checkout_refuses_the_whole_repository(
    layout: dict[str, Path], link: str, target: str
) -> None:
    app = layout["app"]
    (app / link).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(layout["root"] / target, app / link)
    allow = _list(layout)
    message = refusal(allow, {"repo_path": "repo"}, {"repo_path": str(app)})
    assert message is not None and "app" in message and link in message
    assert str(layout["root"]) not in message  # the link's name in the repo, never the target
    assert refusal(allow, {"repo_path": "repo"}, {"repo_path": str(layout["lib"])}) is None  # lib is clean
    across = {"repo_path": "repo", "repos": "repos_file"}
    assert refusal(allow, across, {"repo_path": "", "repos": str(layout["admin"])})  # every listed repo


def test_a_path_inside_a_checkout_with_a_link_out_is_refused_too(layout: dict[str, Path]) -> None:
    os.symlink(layout["outside"], layout["app"] / "pkg" / "escape")
    message = refusal(_list(layout), {"repo_path": "repo"}, {"repo_path": str(layout["app"] / "openspec")})
    assert message is not None and "pkg/escape" in message


def test_links_that_stay_inside_are_fine(layout: dict[str, Path]) -> None:
    app = layout["app"]
    os.symlink(app / "spec.md", app / "alias.md")
    os.symlink(app / "pkg", app / "pkg-alias")
    assert refusal(_list(layout), {"repo_path": "repo"}, {"repo_path": str(app)}) is None


def test_a_link_added_later_is_seen(layout: dict[str, Path]) -> None:
    app = layout["app"]
    allow = _list(layout)
    assert refusal(allow, {"repo_path": "repo"}, {"repo_path": str(app)}) is None
    os.symlink(layout["outside"], app / "late")
    assert refusal(allow, {"repo_path": "repo"}, {"repo_path": str(app)}) is not None


@pytest.mark.parametrize(
    ("decl", "value"),
    [
        ({"source": "source"}, "file://{app}/spec.md"),
        ({"change_path": "inside"}, "{app}/openspec/changes/c1"),
        ({"repo_path": "repo_optional"}, "{app}"),
    ],
)
def test_every_kind_that_reaches_a_checkout_sees_its_links(
    layout: dict[str, Path], decl: dict[str, str], value: str
) -> None:
    app = layout["app"]
    (name,) = decl
    allow = _list(layout)
    assert refusal(allow, decl, {name: value.format(app=app)}) is None
    os.symlink(layout["outside"], app / "openspec" / "leak")
    assert refusal(allow, decl, {name: value.format(app=app)}) is not None


def test_a_tilde_is_refused_even_where_it_would_expand_into_the_list(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check would expand ``~`` into a listed checkout; ``load_repo_config`` and
    ``pkg_grounding`` would not, and open ``./~/…`` under the working directory instead."""
    allow = _list(layout)
    monkeypatch.setenv("HOME", str(layout["app"]))
    assert not _ok(allow, {"repo_path": "repo"}, repo_path="~/pkg")
    assert not _ok(allow, {"source": "source"}, source="file://~/spec.md")
    assert _ok(allow, {"repo_path": "repo"}, repo_path=str(layout["app"] / "pkg"))  # spelled out: fine
    monkeypatch.setenv("HOME", str(layout["admin"].parent.parent))
    assert not _ok(allow, {"repos": "repos_file"}, repos="~/.spine/repos.yaml")
    assert _ok(allow, {"repos": "repos_file"}, repos=str(layout["admin"]))


def test_a_repository_relative_path_is_the_tools_to_confine_unless_it_is_absolute(
    allow: RepoAllowList, layout: dict[str, Path]
) -> None:
    """``file_insights(repo_path, path)``: ``path`` is relative to the repository the call names
    (already checked) and the tool confines it there (``server._repo_file``); an absolute one must
    lie inside a listed checkout like any other path."""
    decl = {"repo_path": "repo", "path": "repo_relative"}
    app = str(layout["app"])
    assert _ok(allow, decl, repo_path=app, path="pkg/mod.py")
    assert _ok(allow, decl, repo_path=app, path="")  # the tool answers "provide a path"
    assert _ok(allow, decl, repo_path=app, path=str(layout["app"] / "spec.md"))
    assert not _ok(allow, decl, repo_path=app, path=str(layout["outside"] / "secret" / "spec.md"))
    for bad in ("~/x.py", ".git/config", "pkg/../.git/HEAD", " pkg/mod.py"):
        assert not _ok(allow, decl, repo_path=app, path=bad), bad
