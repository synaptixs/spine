"""PKG: Kotlin server routes — the Ktor DSL, and Spring through the Kotlin grammar (P6, D16).

The Ktor half is where this front-end earns the word *provider*. Its difficulty is
not the DSL but the ambiguity around it: ``get`` is one of the most common method
names in Kotlin, and the sample corpus has 341 calls spelled ``get("…")`` of which
only a minority are routes. Most of these tests are about what is **not** read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

KTOR_KT = """\
package svc

import io.ktor.server.application.*
import io.ktor.server.auth.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        get("/health") { }
        get("/ping", ::pingHandler)
        authenticate("session") {
            get("/me") { }
        }
        route("/api") {
            route("/users") {
                get { }
                post { }
                get("/{id}") { }
            }
        }
        route("/v1") {
            orders()
        }
        get(buildPath()) { }
    }
}

fun pingHandler() {}

fun buildPath(): String = "/computed"
"""

ORDERS_KT = """\
package svc

import io.ktor.server.routing.*

fun Route.orders() {
    get("/orders") { }
    route("/orders/{id}") {
        delete { }
    }
}

fun Route.orphan() {
    get("/never-mounted") { }
}
"""

SPRING_KT = """\
package shop.api

import org.springframework.stereotype.Controller
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/api")
class TopicController(val repo: TopicRepository) {

    @GetMapping("/topics")
    fun list(): String = "topics"

    @GetMapping
    fun root(): String = "api"

    @RequestMapping(value = "/topics/{id}", method = [RequestMethod.PUT])
    fun replace(): String = "replaced"

    @RequestMapping("/topics/anything")
    fun anything(): String = "any"
}

@Controller
class PageController {

    @GetMapping("vets.json", produces = ["application/json"])
    fun vets(): String = "vets"
}

interface TopicClient {

    @GetMapping("/remote/topics")
    fun remote(): String
}

interface TopicRepository
"""


def _facts(tmp_path: Path, **files: str) -> FactBatch:
    for name, src in files.items():
        (tmp_path / f"{name}.kt").write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _ktor(tmp_path: Path) -> FactBatch:
    return _facts(tmp_path, App=KTOR_KT, Orders=ORDERS_KT)


def _spring(tmp_path: Path) -> FactBatch:
    return _facts(tmp_path, Controllers=SPRING_KT)


def _endpoints(batch: FactBatch) -> set[str]:
    return {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}


def _exposes(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}


# ---- Ktor: what is read -----------------------------------------------------


def test_a_routing_block_declares_an_endpoint(tmp_path: Path) -> None:
    assert "GET /health" in _endpoints(_ktor(tmp_path))


def test_nested_route_groups_compose_into_one_path(tmp_path: Path) -> None:
    assert "GET /api/users/{id}" in _endpoints(_ktor(tmp_path))


def test_a_bare_verb_serves_the_group_its_own_path(tmp_path: Path) -> None:
    """`route("/users") { get { } }` is `GET /api/users`, not `GET /api/users/`."""
    endpoints = _endpoints(_ktor(tmp_path))
    assert {"GET /api/users", "POST /api/users"} <= endpoints


def test_a_wrapper_block_does_not_change_the_path(tmp_path: Path) -> None:
    """`authenticate("session")` nests routes without prefixing them."""
    endpoints = _endpoints(_ktor(tmp_path))
    assert "GET /me" in endpoints and "GET /session/me" not in endpoints


def test_a_route_module_is_mounted_at_its_callers_prefix(tmp_path: Path) -> None:
    """`fun Route.orders()` is in another file; `route("/v1") { orders() }` sites it."""
    assert "GET /v1/orders" in _endpoints(_ktor(tmp_path))


def test_a_group_inside_a_route_module_composes_with_the_mount(tmp_path: Path) -> None:
    assert "DELETE /v1/orders/{id}" in _endpoints(_ktor(tmp_path))


def test_a_route_module_named_after_a_verb_is_still_mounted(tmp_path: Path) -> None:
    """`fun Route.delete(dao)` is a route module that happens to be called `delete`.

    Ktor code really writes this — the sample corpus has one. Reading the mount call as
    a malformed verb call instead of a mount drops the whole module silently.
    """
    src = """\
package svc

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module(dao: Dao) {
    routing {
        route("/api") {
            delete(dao)
        }
    }
}

fun Route.delete(dao: Dao) {
    get("/trash") { }
}
"""
    assert "GET /api/trash" in _endpoints(_facts(tmp_path, M=src))


# ---- Ktor: what is refused --------------------------------------------------


def test_an_unmounted_route_module_yields_nothing(tmp_path: Path) -> None:
    """Its path is genuinely unknown; rooting it at `/` would be a lucky guess."""
    assert not any("never-mounted" in name for name in _endpoints(_ktor(tmp_path)))


def test_a_computed_path_yields_nothing(tmp_path: Path) -> None:
    assert not any("computed" in name for name in _endpoints(_ktor(tmp_path)))


def test_a_computed_group_silences_every_route_inside_it(tmp_path: Path) -> None:
    """D16, the PHP lesson: a wrong prefix is worse than a missing route."""
    src = KTOR_KT.replace(
        'route("/v1") {\n            orders()\n        }',
        'route(Paths.ADMIN) {\n            get("/secret") { }\n        }',
    )
    assert not any("secret" in name for name in _endpoints(_facts(tmp_path, App=src)))


def test_a_method_call_that_shares_a_verbs_name_is_not_a_route(tmp_path: Path) -> None:
    """`client.get(url)` and `board.post(text)` have receivers; the DSL never does."""
    src = """\
package svc

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module(board: Board, client: Client) {
    routing {
        get("/real") {
            board.post("hi")
            client.get("/external")
        }
    }
}
"""
    assert _endpoints(_facts(tmp_path, R=src)) == {"GET /real"}


def test_a_typed_resource_route_yields_nothing(tmp_path: Path) -> None:
    """`get<Index> { }` takes its path from a `@Resource` on another class.

    The plain `get("/real")` beside it is a **positive control**, and it is the whole
    reason this test means anything: an empty endpoint set is also what a reader that had
    stopped working entirely would produce, so without a route that must be found the
    assertion passes for any reason at all — including the grammar happening to parse
    `get<Index>` into a shape the walk never reaches.
    """
    src = """\
package svc

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        get<Index> { }
        get("/real") { }
    }
}
"""
    assert _endpoints(_facts(tmp_path, V=src)) == {"GET /real"}


# ---- Ktor: EXPOSES, and the closure rule ------------------------------------


def test_a_function_reference_handler_gets_an_exposes_edge(tmp_path: Path) -> None:
    assert ("java:endpoint:GET /ping", "java:svc.pingHandler") in _exposes(_ktor(tmp_path))


def test_an_inline_lambda_handler_gets_no_exposes_edge(tmp_path: Path) -> None:
    """The closure rule: an anonymous lambda has no id, and the enclosing function
    registers the route rather than serving it."""
    assert not any(src == "java:endpoint:GET /health" for src, _ in _exposes(_ktor(tmp_path)))


def test_a_chained_call_still_reads_the_route_underneath_it(tmp_path: Path) -> None:
    """`get("/x") { }.describe { }` is one call *on* another — httpbin writes this,
    and reading only the outer half loses every route in the file."""
    src = """\
package svc

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        get("/get") { }.describe {
            summary = "docs"
        }
    }
}
"""
    assert "GET /get" in _endpoints(_facts(tmp_path, D=src))


# ---- Spring, through the Kotlin grammar -------------------------------------


def test_a_rest_controller_prefix_joins_its_methods(tmp_path: Path) -> None:
    assert "GET /api/topics" in _endpoints(_spring(tmp_path))


def test_a_bare_mapping_serves_the_class_prefix(tmp_path: Path) -> None:
    assert "GET /api" in _endpoints(_spring(tmp_path))


def test_a_request_method_argument_names_the_verb(tmp_path: Path) -> None:
    assert "PUT /api/topics/{id}" in _endpoints(_spring(tmp_path))


def test_a_verb_less_request_mapping_yields_nothing(tmp_path: Path) -> None:
    """The no-`ANY` rule — and the path must not be mistaken for the verb either."""
    assert not any("anything" in name for name in _endpoints(_spring(tmp_path)))


def test_plain_controller_routes_too(tmp_path: Path) -> None:
    """The Spring validation repository uses `@Controller` on every one of its controllers."""
    assert "GET /vets.json" in _endpoints(_spring(tmp_path))


def test_a_mapping_without_a_stereotype_yields_nothing(tmp_path: Path) -> None:
    """The Feign shape: the same annotation on a client interface."""
    assert not any("remote" in name for name in _endpoints(_spring(tmp_path)))


def test_a_non_literal_class_prefix_silences_the_class(tmp_path: Path) -> None:
    src = SPRING_KT.replace('@RequestMapping("/api")', "@RequestMapping(ADMIN_PREFIX)")
    assert not any(name.endswith("/topics") for name in _endpoints(_facts(tmp_path, C=src)))


def test_the_handler_gets_an_exposes_edge(tmp_path: Path) -> None:
    assert ("java:endpoint:GET /api/topics", "java:shop.api.TopicController.list") in _exposes(
        _spring(tmp_path)
    )


def test_stranded_annotations_are_recovered(tmp_path: Path) -> None:
    """tree-sitter-kotlin 1.1.0 parks a parenthesised top-level annotation outside the
    declaration it decorates, stripping its `modifiers`. 24 files in the validation app hit
    it; without recovery `TopicController` reads as a plain class.

    `@RequestMapping("/api")` is the stranded one — it is the annotation with parentheses —
    so the observable difference is the class-level **prefix**. Asserting the prefixed id
    alone only repeats what `test_the_handler_gets_an_exposes_edge` already needs; the
    unprefixed form has to be asserted *absent*, because that is precisely what would be
    emitted if the annotation were lost and the rest of the reader carried on working.
    """
    endpoints = _endpoints(_spring(tmp_path))
    assert "GET /api/topics" in endpoints
    assert "GET /topics" not in endpoints, "the class prefix was dropped, i.e. not recovered"


# ---- §11 finding 7: a bare name is not a repository-wide address ----


def _multi(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


_MOUNTED = """\
package {pkg}

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Route.health() {{
    get("/health") {{ }}
}}

fun Application.module() {{
    routing {{
        route("/{prefix}") {{ health() }}
    }}
}}
"""


def test_a_mount_resolves_in_its_own_package_not_across_services(tmp_path: Path) -> None:
    """Two services each declaring `fun Route.health()` is the normal shape of a monorepo.

    Resolving the mount by bare name across the whole tree mounted service A's routes under
    service B's prefix — a route B does not serve, provenanced to a file that never
    mentions it — or, when both declared the name, silently dropped both.
    """
    batch = _multi(
        tmp_path,
        {
            "a/App.kt": _MOUNTED.format(pkg="svc.a", prefix="a"),
            "b/App.kt": _MOUNTED.format(pkg="svc.b", prefix="b"),
        },
    )
    endpoints = {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}
    assert endpoints == {"GET /a/health", "GET /b/health"}


def test_a_mount_with_one_declaration_still_does_not_cross_services(tmp_path: Path) -> None:
    """#395. The one-declaration variant of the test above.

    Two candidates never reached the repo-wide fallback tier at all — this pins the
    tier itself. `svc.a` mounts `health()` with no import of `svc.b`, where the only
    `fun Route.health()` in the whole tree is declared. Before #395's fix, "exactly
    one declaration in the repository" resolved this regardless of package or
    import, mounting `svc.b`'s route under `svc.a`'s prefix — a route `svc.a` does
    not serve and Kotlin itself could not have compiled the call against.
    """
    batch = _multi(
        tmp_path,
        {
            "a/App.kt": """\
package svc.a

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        route("/a") { health() }
    }
}
""",
            "b/Health.kt": """\
package svc.b

import io.ktor.server.routing.*

fun Route.health() {
    get("/health") { }
}
""",
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]


def test_a_mount_resolves_through_a_wildcard_import(tmp_path: Path) -> None:
    """The genuine case the repo-wide fallback tier exists for: `svc.a` wildcard-

    imports `svc.b`, so `health()` is reachable from `svc.a` even with no explicit
    `import svc.b.health` line — #395's fix restricts the fallback to exactly this,
    rather than removing it.
    """
    batch = _multi(
        tmp_path,
        {
            "a/App.kt": """\
package svc.a

import io.ktor.server.application.*
import io.ktor.server.routing.*
import svc.b.*

fun Application.module() {
    routing {
        route("/a") { health() }
    }
}
""",
            "b/Health.kt": """\
package svc.b

import io.ktor.server.routing.*

fun Route.health() {
    get("/health") { }
}
""",
        },
    )
    endpoints = {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}
    assert endpoints == {"GET /a/health"}


def test_a_spring_property_placeholder_is_not_a_path(tmp_path: Path) -> None:
    """Kotlin refuses an interpolated path at the grammar, but `"\\${api.base}/x"` is an
    *escaped* dollar and decodes to the same Spring placeholder — a path resolved from
    configuration at boot, which the source does not state."""
    batch = _multi(
        tmp_path,
        {
            "C.kt": """\
package svc

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class C {
    @GetMapping("\\${api.base}/topics")
    fun list() {}
}
"""
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]


def test_a_mount_resolves_inside_the_default_package(tmp_path: Path) -> None:
    """#395's fix compared packages, and a file that declares none has no package.

    `module_name` falls back to the repo-relative path there — 14 of 263 files in the
    validation app — so two default-package files read as two different packages and the
    own-package tier could never match them. Kotlin needs no import to resolve this, and
    the source compiles, so the mount was a real edge lost rather than a guess refused.
    """
    batch = _multi(
        tmp_path,
        {
            "App.kt": """\
import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        route("/a") { health() }
    }
}
""",
            "Health.kt": """\
import io.ktor.server.routing.*

fun Route.health() {
    get("/health") { }
}
""",
        },
    )
    assert {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT} == {"GET /a/health"}


def test_two_default_package_route_modules_of_one_name_stay_unresolved(tmp_path: Path) -> None:
    """The default package is one package, so two `health()` in it are ambiguous.

    Same "exactly one, or unresolved" rule every other tier uses — the fix widens what
    counts as the same package, it does not weaken what counts as a unique answer.
    """
    batch = _multi(
        tmp_path,
        {
            "App.kt": """\
import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        route("/a") { health() }
    }
}
""",
            "One.kt": 'import io.ktor.server.routing.*\n\nfun Route.health() {\n    get("/one") { }\n}\n',
            "Two.kt": 'import io.ktor.server.routing.*\n\nfun Route.health() {\n    get("/two") { }\n}\n',
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT and n.name.startswith("GET /a")]


def test_a_default_package_mount_does_not_capture_a_packaged_module(tmp_path: Path) -> None:
    """A file with no package cannot see `svc.b`, and must not mount its route.

    The widened tier keys on "declares no package" on *both* ends, so it cannot reach a
    declaration that sits in a real package — which is what #395 closed in the first place.
    """
    batch = _multi(
        tmp_path,
        {
            "App.kt": """\
import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        route("/a") { health() }
    }
}
""",
            "b/Health.kt": """\
package svc.b

import io.ktor.server.routing.*

fun Route.health() {
    get("/health") { }
}
""",
        },
    )
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]
