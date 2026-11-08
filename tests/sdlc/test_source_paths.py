"""`sdlc.source_paths`: the one regex and resolver behind `design._stated_paths` and
`codegen._paths_from`. Until they shared it, each carried a Python-only copy, and the file a
.NET ticket named (NSS-1231: `EBSOrderApiClient.cs`, bare, no directory) was invisible to both."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.sdlc.source_paths import PATH_RE, basename_index, find_by_basename, named_paths, resolve


def test_every_front_end_suffix_is_a_path_and_prose_is_not() -> None:
    text = (
        "Edit `EBSOrderApiClient.cs`, src/orchestrator/cli.py, WebApp/Grid.razor, api/handler.go and "
        "pkg/Foo.java; see README.md, https://example.com and version 3.36.0. Don't touch e.g. c.h."
    )
    assert named_paths(text) == [
        "EBSOrderApiClient.cs",
        "src/orchestrator/cli.py",
        "WebApp/Grid.razor",
        "api/handler.go",
        "pkg/Foo.java",
    ]


def test_windows_separators_are_normalised_and_duplicates_collapse() -> None:
    assert named_paths(r"Shared\Enums\ProductGroup.cs and Shared/Enums/ProductGroup.cs") == [
        "Shared/Enums/ProductGroup.cs"
    ]
    assert named_paths("./src/a.py and src/a.py") == ["src/a.py"]


def test_the_regex_does_not_bleed_into_neighbouring_words() -> None:
    assert PATH_RE.findall("filename.cs.bak") == ["filename.cs"]
    assert PATH_RE.findall("x.pyc") == []


def _tree(root: Path, *files: str) -> None:
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("//\n", encoding="utf-8")


def test_a_written_path_that_exists_is_taken_as_written(tmp_path: Path) -> None:
    _tree(tmp_path, "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs")
    assert resolve("FunctionsApp/Shared/Utils/EBSOrderApiClient.cs", tmp_path) == (
        "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"
    )
    assert resolve(r"FunctionsApp\Shared\Utils\EBSOrderApiClient.cs", tmp_path) == (
        "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"
    )


def test_nss_1231_a_bare_basename_resolves_to_its_one_location(tmp_path: Path) -> None:
    _tree(tmp_path, "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs", "FunctionsApp/Program.cs")
    assert resolve("EBSOrderApiClient.cs", tmp_path) == "FunctionsApp/Shared/Utils/EBSOrderApiClient.cs"


def test_an_ambiguous_basename_is_a_guess_and_is_dropped(tmp_path: Path) -> None:
    _tree(tmp_path, "a/Product.cs", "b/Product.cs")
    assert find_by_basename(tmp_path, "Product.cs") == ["a/Product.cs", "b/Product.cs"]
    assert resolve("Product.cs", tmp_path) is None


def test_a_path_that_does_not_exist_is_dropped_and_a_directory_path_is_never_searched(tmp_path: Path) -> None:
    _tree(tmp_path, "src/real.py")
    assert resolve("src/ghost.py", tmp_path) is None
    assert resolve("elsewhere/real.py", tmp_path) is None  # no file ends in /elsewhere/real.py


def test_the_walk_skips_what_the_extractors_skip(tmp_path: Path) -> None:
    _tree(tmp_path, "src/Only.cs", "node_modules/Only.cs", ".hidden/Only.cs", "vendored/Only.cs")
    (tmp_path / "vendored" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a submodule
    assert find_by_basename(tmp_path, "Only.cs") == ["src/Only.cs"]


def test_a_path_that_leaves_the_root_is_not_a_path_under_it(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.cs").write_text("//\n", encoding="utf-8")
    assert resolve(f"../{outside.name}/secret.cs", tmp_path) is None


def test_prose_shaped_tokens_are_not_paths() -> None:
    assert named_paths("see section 3.c, Fig. 2.c, pip install a.b.c and v1.2.pl") == []
    assert named_paths("the code-behind App.razor.cs and Program.cs") == ["App.razor.cs", "Program.cs"]


def test_the_index_answers_every_bare_name_from_one_walk(tmp_path: Path, monkeypatch: Any) -> None:
    """`find_by_basename` stops at two hits, which bounds an ambiguous name — but a name that
    does not exist walked the whole tree, once per name. Several names now cost one walk."""
    import os

    _tree(tmp_path, "FunctionsApp/Utils/EBSOrderApiClient.cs", "a/Product.cs", "b/Product.cs")
    walks: list[int] = []
    real_walk = os.walk

    def _counted(*a: Any, **k: Any) -> Any:
        walks.append(1)
        return real_walk(*a, **k)

    monkeypatch.setattr(os, "walk", _counted)

    index = basename_index(tmp_path)
    assert index["EBSOrderApiClient.cs"] == ["FunctionsApp/Utils/EBSOrderApiClient.cs"]
    assert index["Product.cs"] == ["a/Product.cs", "b/Product.cs"]  # every location (B159)
    assert resolve("EBSOrderApiClient.cs", tmp_path, index=index) == "FunctionsApp/Utils/EBSOrderApiClient.cs"
    assert resolve("Product.cs", tmp_path, index=index) is None
    assert resolve("Ghost.cs", tmp_path, index=index) is None
    assert resolve("Phantom.cs", tmp_path, index=index) is None
    assert len(walks) == 1


# ---- B159 / SSPN-375: a partial path is resolved by its one suffix match ---------------------
#
# SSPN-286 named its files package-relative — `pkg/walk_rules.py`, `catalog/profile.py` — and every
# one of them was refused because it held a `/` but was not the path from the root. The build
# document then listed the real fix files as "named but absent — a file to create".


def test_b159_a_partial_path_resolves_to_its_one_location(tmp_path: Path) -> None:
    _tree(tmp_path, "src/orchestrator/pkg/walk_rules.py", "src/orchestrator/catalog/profile.py")
    assert resolve("pkg/walk_rules.py", tmp_path) == "src/orchestrator/pkg/walk_rules.py"
    index = basename_index(tmp_path)
    assert resolve("catalog/profile.py", tmp_path, index=index) == "src/orchestrator/catalog/profile.py"
    windows = r"orchestrator\pkg\walk_rules.py"
    assert resolve(windows, tmp_path, index=index) == "src/orchestrator/pkg/walk_rules.py"


def test_b159_a_partial_path_matches_whole_directory_names_only(tmp_path: Path) -> None:
    _tree(tmp_path, "src/mypkg/walk_rules.py")
    assert resolve("pkg/walk_rules.py", tmp_path) is None
    assert resolve("pkg/walk_rules.py", tmp_path, index=basename_index(tmp_path)) is None


def test_b159_an_ambiguous_partial_path_is_a_guess_and_is_dropped(tmp_path: Path) -> None:
    _tree(tmp_path, "svc_a/api/models.py", "svc_b/api/models.py")
    assert resolve("api/models.py", tmp_path) is None
    assert resolve("api/models.py", tmp_path, index=basename_index(tmp_path)) is None
    # Written in full, it is taken as written.
    assert resolve("svc_a/api/models.py", tmp_path) == "svc_a/api/models.py"


def test_b159_a_third_namesake_is_not_lost_to_a_cap(tmp_path: Path) -> None:
    """Capped at two per name, the index kept `a/pkg/util.py` and `b/util.py`, dropped `c/pkg/util.py`,
    and the suffix filter saw one match where there are two."""
    _tree(tmp_path, "a/pkg/util.py", "b/util.py", "c/pkg/util.py")
    assert basename_index(tmp_path)["util.py"] == ["a/pkg/util.py", "b/util.py", "c/pkg/util.py"]
    assert resolve("pkg/util.py", tmp_path, index=basename_index(tmp_path)) is None
    assert resolve("pkg/util.py", tmp_path) is None
    assert resolve("util.py", tmp_path) is None  # a bare name with three locations is still a guess


def test_b159_every_consumer_sees_the_partial_path(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import _named_absent_rows
    from orchestrator.sdlc.codegen import _paths_from
    from orchestrator.sdlc.design import _stated_paths
    from orchestrator.sdlc.validity import _check_named_paths

    _tree(tmp_path, "src/orchestrator/pkg/walk_rules.py", "src/web/editor.ts")
    spec = {
        "title": "Skip gitignored trees",
        "description": "Add the helper to pkg/walk_rules.py; the editor lives in web/editor.ts.",
    }
    full = "src/orchestrator/pkg/walk_rules.py"
    assert full in _stated_paths(spec, tmp_path)
    assert full in _paths_from(spec, "", tmp_path)
    assert _named_absent_rows(spec, tmp_path, "python", set()) == []  # not "a file to create"
    # A TypeScript file on a Python run is flagged only when it does not exist; this one does.
    assert _check_named_paths(spec, tmp_path, "python") == []


# ---- B159 review: a path written from the root is never redirected to a namesake below it -----
#
# A ticket that says "add tests/test_walk.py" means the root `tests/`. Matched by suffix, it became
# `plugins/foo/tests/test_walk.py` — an existing file codegen was then told to change, and the
# file to create vanished from §7. A first segment that is a directory at the root says the
# path is written from the root; only a path whose first segment is not one is package-relative.


def test_b159_a_path_written_from_the_root_is_never_redirected(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        "tests/conftest.py",
        "plugins/foo/tests/test_walk.py",
        "src/app.ts",
        "packages/a/src/index.ts",
    )
    index = basename_index(tmp_path)
    for rel in ("tests/test_walk.py", "src/index.ts"):
        assert resolve(rel, tmp_path) is None, rel
        assert resolve(rel, tmp_path, index=index) is None, rel
    # Package-relative still resolves: the root has no `foo/`.
    assert resolve("foo/tests/test_walk.py", tmp_path) == "plugins/foo/tests/test_walk.py"


def test_b159_a_file_to_create_at_the_root_stays_a_file_to_create(tmp_path: Path) -> None:
    from orchestrator.sdlc.builddoc import _named_absent_rows
    from orchestrator.sdlc.codegen import _paths_from

    _tree(tmp_path, "tests/conftest.py", "plugins/foo/tests/test_walk.py")
    spec = {"title": "Walk test", "description": "Add a new test tests/test_walk.py for the walker."}
    assert "plugins/foo/tests/test_walk.py" not in _paths_from(spec, "", tmp_path)
    rows = _named_absent_rows(spec, tmp_path, "python", set())
    assert any("tests/test_walk.py" in row for row in rows), rows


def test_b159_a_root_file_wins_over_a_nested_namesake(tmp_path: Path) -> None:
    _tree(tmp_path, "pkg/x.py", "src/pkg/x.py")
    assert resolve("pkg/x.py", tmp_path) == "pkg/x.py"


def test_b159_the_design_read_list_resolves_from_one_walk(tmp_path: Path, monkeypatch: Any) -> None:
    """`_design_read_paths` resolved with no index, so every partial path walked the whole tree."""
    import os

    from orchestrator.sdlc.codegen import _design_read_paths
    from orchestrator.sdlc.scope import READ_HEADING

    _tree(tmp_path, "src/orchestrator/pkg/walk_rules.py", "src/orchestrator/catalog/profile.py")
    design = f"## {READ_HEADING}\n- pkg/walk_rules.py\n- catalog/profile.py\n- pkg/ghost.py\n"
    walks: list[int] = []
    real_walk = os.walk

    def _counted(*a: Any, **k: Any) -> Any:
        walks.append(1)
        return real_walk(*a, **k)

    monkeypatch.setattr(os, "walk", _counted)
    assert _design_read_paths(design, tmp_path) == [
        "src/orchestrator/pkg/walk_rules.py",
        "src/orchestrator/catalog/profile.py",
    ]
    assert len(walks) == 1
