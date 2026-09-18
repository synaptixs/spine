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
    assert resolve("elsewhere/real.py", tmp_path) is None  # has a directory: not a bare name


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
    assert index["Product.cs"] == ["a/Product.cs", "b/Product.cs"]  # bounded at two, as the lookup is
    assert resolve("EBSOrderApiClient.cs", tmp_path, index=index) == "FunctionsApp/Utils/EBSOrderApiClient.cs"
    assert resolve("Product.cs", tmp_path, index=index) is None
    assert resolve("Ghost.cs", tmp_path, index=index) is None
    assert resolve("Phantom.cs", tmp_path, index=index) is None
    assert len(walks) == 1
