"""PKG: Compose navigation as ``NAV`` endpoints (P4, D14).

The behaviours here are the ones that decide whether in-app navigation is visible
at all. Real Compose code names a route once as a constant and imports it, so the
literal-only reading a first pass would write finds almost nothing; and a
declaration and its call site spell the same route differently, so without
normalisation nothing ever pairs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

NAV_KT = """\
package shop.nav

import androidx.navigation.compose.composable

const val cartRoute = "cart_route"
const val itemIdArg = "itemId"

fun navigateToCart() {
    navigate(cartRoute)
}

fun navigateToItem(id: String) {
    navigate("item_route/$id")
}

fun cartScreen() {
    composable(route = cartRoute) { CartRoute() }
}

fun itemScreen() {
    composable(route = "item_route/{$itemIdArg}") { ItemRoute() }
}

fun splitScreen() {
    composable(route = "split_route") { LeftPane(); RightPane() }
}

fun computedScreen() {
    composable(route = buildRoute()) { HiddenRoute() }
}

fun CartRoute() {}
fun ItemRoute() {}
fun LeftPane() {}
fun RightPane() {}
fun HiddenRoute() {}
fun buildRoute(): String = "nope"
"""


def _facts(tmp_path: Path, src: str = NAV_KT, name: str = "Nav.kt") -> FactBatch:
    (tmp_path / name).write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _endpoints(batch: FactBatch) -> set[str]:
    return {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def test_a_route_constant_becomes_an_endpoint(tmp_path: Path) -> None:
    """Routes are named once and imported — literal-only reading finds nothing."""
    assert "NAV cart_route" in _endpoints(_facts(tmp_path))


def test_an_interpolated_constant_resolves_into_the_route_name(tmp_path: Path) -> None:
    """`"item_route/{$itemIdArg}"` → `item_route/{itemId}`, D14's own example."""
    assert "NAV item_route/{itemId}" in _endpoints(_facts(tmp_path))


def test_a_computed_route_yields_no_endpoint(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    assert not any("nope" in name or "buildRoute" in name for name in _endpoints(batch))


def test_the_route_exposes_the_single_screen_it_shows(tmp_path: Path) -> None:
    exposes = _edges(_facts(tmp_path), EdgeKind.EXPOSES)
    assert ("java:endpoint:NAV cart_route", "java:shop.nav.CartRoute") in exposes


def test_a_lambda_showing_two_screens_exposes_neither(tmp_path: Path) -> None:
    """The closure rule: visit order is not evidence about which screen is 'the' one."""
    batch = _facts(tmp_path)
    assert "NAV split_route" in _endpoints(batch)  # the route is still real
    targets = {dst for src, dst in _edges(batch, EdgeKind.EXPOSES) if "split_route" in src}
    assert targets == set()


def test_navigating_consumes_the_route_across_differing_spellings(tmp_path: Path) -> None:
    """`navigate("item_route/$id")` must pair with `composable("item_route/{itemId}")`."""
    consumes = _edges(_facts(tmp_path), EdgeKind.CONSUMES)
    assert (
        "java:shop.nav.navigateToItem",
        "java:endpoint:NAV item_route/{itemId}",
    ) in consumes
    assert ("java:shop.nav.navigateToCart", "java:endpoint:NAV cart_route") in consumes


def test_navigating_to_an_undeclared_route_consumes_nothing(tmp_path: Path) -> None:
    """Inventing the destination would make `pkg verify` report zero dangling for it."""
    src = 'package shop.nav\n\nfun go() {\n    navigate("nowhere_route")\n}\n'
    batch = _facts(tmp_path, src, "Go.kt")
    assert _edges(batch, EdgeKind.CONSUMES) == set()
    assert _endpoints(batch) == set()


def test_a_route_constant_declared_in_another_file_resolves_through_a_wildcard_import(
    tmp_path: Path,
) -> None:
    """The case that forces a whole-repo pass: the constant is never local.

    #395: this used to resolve with *no* import at all, purely because exactly one
    package in the whole repository declared `searchRoute` — a repo-wide address
    Kotlin itself could not have compiled the reference against. It resolves now
    only because `shop.ui` wildcard-imports `shop.nav`, which is what makes the
    name reachable from this file in the first place.
    """
    (tmp_path / "Routes.kt").write_text(
        'package shop.nav\n\nconst val searchRoute = "search_route"\n', encoding="utf-8"
    )
    (tmp_path / "Screen.kt").write_text(
        "package shop.ui\n\nimport androidx.navigation.compose.composable\nimport shop.nav.*\n\n"
        "fun searchScreen() {\n    composable(route = searchRoute) { SearchRoute() }\n}\n\n"
        "fun SearchRoute() {}\n",
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert "NAV search_route" in _endpoints(batch)


def test_a_route_constant_with_no_import_at_all_does_not_cross_packages(tmp_path: Path) -> None:
    """#395. Same shape as above, minus the wildcard import — must resolve nothing.

    A repo-wide address is not the same as a name Kotlin could actually compile the
    reference against, even when the name happens to be unique across the tree.
    """
    (tmp_path / "Routes.kt").write_text(
        'package shop.nav\n\nconst val searchRoute = "search_route"\n', encoding="utf-8"
    )
    (tmp_path / "Screen.kt").write_text(
        "package shop.ui\n\nimport androidx.navigation.compose.composable\n\n"
        "fun searchScreen() {\n    composable(route = searchRoute) { SearchRoute() }\n}\n\n"
        "fun SearchRoute() {}\n",
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert _endpoints(batch) == set()


def test_nav_endpoints_cannot_collide_with_an_http_verb(tmp_path: Path) -> None:
    """`NAV` keeps in-app routes out of the cross-repo HTTP join entirely."""
    names = _endpoints(_facts(tmp_path))
    assert names and all(name.startswith("NAV ") for name in names)


# ---- §11 finding 10: a route constant is scoped to its package ----


def _multi(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


_FEATURE = """\
package feature.{name}

const val route = "{name}_route"

fun {name}Nav() {{
    composable(route = route) {{ {Name}Screen() }}
}}

fun {Name}Screen() {{}}
"""


def test_two_features_may_declare_the_same_route_constant(tmp_path: Path) -> None:
    """A flat repo-wide constant table let the survivor win.

    The other module's `composable` was then credited with a path its source never
    contains, and one endpoint collected an `EXPOSES` to both screens. Two feature modules
    each declaring `const val route` is the ordinary Compose convention.
    """
    batch = _multi(
        tmp_path,
        {
            "one/Nav.kt": _FEATURE.format(name="one", Name="One"),
            "two/Nav.kt": _FEATURE.format(name="two", Name="Two"),
        },
    )
    endpoints = {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}
    assert endpoints == {"NAV one_route", "NAV two_route"}
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert exposes == {
        ("java:endpoint:NAV one_route", "java:feature.one.OneScreen"),
        ("java:endpoint:NAV two_route", "java:feature.two.TwoScreen"),
    }


def test_a_concatenated_route_is_not_a_route(tmp_path: Path) -> None:
    """`composable(route = "topic/" + BASE)` is an `additive_expression`, not a literal.

    The route used to be the argument's *raw source text*, recognised as a literal by
    `startswith('"')` and then stripped of its first and last character — so this produced
    the endpoint `NAV topic/" + BAS`.
    """
    batch = _multi(
        tmp_path,
        {
            "Nav.kt": """\
package app

const val BASE = "x"

fun nav() {
    composable(route = "topic/" + BASE) { TopicRoute() }
}

fun TopicRoute() {}
"""
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]


def test_a_route_constant_resolves_inside_the_default_package(tmp_path: Path) -> None:
    """#395's fix keyed on the package, and a file declaring none has no package.

    `module_name` falls back to the repo-relative path, so `Routes.kt` and `Screen.kt`
    keyed on two different strings and the constant was never found — though Kotlin
    resolves it with no import at all and the source compiles.
    """
    (tmp_path / "Routes.kt").write_text('const val searchRoute = "search_route"\n', encoding="utf-8")
    (tmp_path / "Screen.kt").write_text(
        "import androidx.navigation.compose.composable\n\n"
        "fun searchScreen() {\n    composable(route = searchRoute) { SearchRoute() }\n}\n\n"
        "fun SearchRoute() {}\n",
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert "NAV search_route" in _endpoints(batch)


def test_a_default_package_screen_does_not_reach_a_packaged_constant(tmp_path: Path) -> None:
    """A file with no package cannot see `shop.nav`, with or without the widened tier."""
    (tmp_path / "Routes.kt").write_text(
        'package shop.nav\n\nconst val searchRoute = "search_route"\n', encoding="utf-8"
    )
    (tmp_path / "Screen.kt").write_text(
        "import androidx.navigation.compose.composable\n\n"
        "fun searchScreen() {\n    composable(route = searchRoute) { SearchRoute() }\n}\n\n"
        "fun SearchRoute() {}\n",
        encoding="utf-8",
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert _endpoints(batch) == set()
