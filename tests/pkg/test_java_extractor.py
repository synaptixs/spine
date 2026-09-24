"""PKG G6: the Java front-end maps Java source onto the universal facts.

tree-sitter is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind, Provenance
from orchestrator.pkg.java_extractor import JavaExtractor

pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")

ACCOUNT = """\
package com.example.bank;

import com.example.base.Base;
import java.util.List;

public class Account extends Base implements Closeable {
    private final String owner;
    private int balance = 0;

    public Account(String owner) {
        this.owner = owner;
    }

    public void deposit(int amount) {
        this.balance += amount;
    }

    enum Status { OPEN, CLOSED }
}
"""


def _facts(tmp_path: Path, src: str = ACCOUNT, name: str = "Account.java") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = JavaExtractor()
    module = ex.module_name(f, tmp_path)
    return ex.extract(path=f, module=module, rel=f"src/{name}"), module


def test_module_name_is_the_package(tmp_path: Path) -> None:
    _, module = _facts(tmp_path)
    assert module == "com.example.bank"


def test_emits_type_method_field_nodes(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["java:com.example.bank.Account"].kind is NodeKind.TYPE
    assert by_id["java:com.example.bank.Account.deposit"].kind is NodeKind.FUNCTION
    assert by_id["java:com.example.bank.Account.owner"].kind is NodeKind.FIELD
    assert by_id["java:com.example.bank.Account.balance"].kind is NodeKind.FIELD
    # nested enum is a Type contained by the class
    assert by_id["java:com.example.bank.Account.Status"].kind is NodeKind.TYPE


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("java:com.example.bank", "java:com.example.base.Base", EdgeKind.IMPORTS) in edges
    assert (
        "java:com.example.bank.Account",
        "java:com.example.bank.Account.deposit",
        EdgeKind.CONTAINS,
    ) in edges


def test_implements_resolves_import_and_same_package(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # extends Base → resolved via the import to its FQN
    assert ("java:com.example.bank.Account", "java:com.example.base.Base") in impls
    # implements Closeable → unimported, resolved to a same-package sibling
    assert ("java:com.example.bank.Account", "java:com.example.bank.Closeable") in impls


def test_does_not_emit_calls(tmp_path: Path) -> None:
    # Precision-first: Java call resolution needs type inference, so no CALLS.
    batch, _ = _facts(tmp_path)
    assert not [e for e in batch.edges if e.kind is EdgeKind.CALLS]


def test_repo_extractor_dispatches_java_by_suffix(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor

    (tmp_path / "A.java").write_text(ACCOUNT, encoding="utf-8")
    (tmp_path / "m.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    batch = RepoCodeExtractor([PythonExtractor(), JavaExtractor()]).extract(tmp_path)
    langs = {n.language for n in batch.nodes}
    assert "java" in langs and "python" in langs


def test_unpackaged_file_falls_back_to_path(tmp_path: Path) -> None:
    _, module = _facts(tmp_path, "class Bare {}\n", name="Bare.java")
    assert module == "Bare.java"


_CALLS_SRC = """\
package com.ex;

import com.other.Helper;

class Foo {
    void a() {
        b();
        this.b();
        Helper.help();
        obj.ignored();
    }
    void b() {}
}
"""


def test_emits_calls_for_sibling_and_static(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, _CALLS_SRC, "Foo.java")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    # bare b() and this.b() both resolve to the sibling (deduped)
    assert ("java:com.ex.Foo.a", "java:com.ex.Foo.b") in calls
    # Helper.help() waits for finalize, which emits it only if the repo declares Helper.help (B21):
    # on its own this file declares neither, so no edge to a member nobody saw
    assert ("java:com.ex.Foo.a", "java:com.other.Helper.help") not in calls
    # obj.ignored() (instance call on a variable) is skipped — no type inference
    assert not any(dst.endswith(".ignored") for _, dst in calls)


JAX_RS_RESOURCE = """\
package com.example.orders;

import jakarta.ws.rs.*;

@Path("/orders")
public class OrderResource {
    @GET
    @Path("/{id}")
    public Order getOrder() { return find(); }

    @POST
    public Order create() { return save(); }

    @jakarta.ws.rs.PUT
    @jakarta.ws.rs.Path(value = "/{id}")
    public Order replace() { return save(); }

    @DELETE
    @Path("/{id}")
    public void delete() {}

    @PATCH
    @Path("/{id}")
    public Order patch() { return save(); }

    @HEAD
    public void head() {}

    @OPTIONS
    public void options() {}

    @Path("/children")
    public ChildResource children() { return childResource; }

    private Order find() { return null; }
    private Order save() { return null; }
}
"""


def test_jax_rs_endpoints_expose_grounded_handlers(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, JAX_RS_RESOURCE, "OrderResource.java")
    by_id = {node.id: node for node in batch.nodes}
    exposes = {(edge.src, edge.dst): edge for edge in batch.edges if edge.kind is EdgeKind.EXPOSES}
    base = "java:com.example.orders.OrderResource"
    expected = {
        "java:endpoint:GET /orders/{id}": (f"{base}.getOrder", 7),
        "java:endpoint:POST /orders": (f"{base}.create", 11),
        "java:endpoint:PUT /orders/{id}": (f"{base}.replace", 14),
        "java:endpoint:DELETE /orders/{id}": (f"{base}.delete", 18),
        "java:endpoint:PATCH /orders/{id}": (f"{base}.patch", 22),
        "java:endpoint:HEAD /orders": (f"{base}.head", 26),
        "java:endpoint:OPTIONS /orders": (f"{base}.options", 29),
    }

    for endpoint_id, (handler_id, line) in expected.items():
        endpoint = by_id[endpoint_id]
        handler = by_id[handler_id]
        edge = exposes[(endpoint_id, handler_id)]
        assert endpoint.kind is NodeKind.ENDPOINT
        assert endpoint.name == endpoint_id.removeprefix("java:endpoint:")
        assert endpoint.provenance == handler.provenance == edge.provenance
        assert endpoint.provenance == Provenance("src/OrderResource.java", line)
        assert handler.kind is NodeKind.FUNCTION
        assert handler.grounded

    # A @Path-only method is a sub-resource locator, not an HTTP endpoint.
    assert not [
        node for node in batch.nodes if node.kind is NodeKind.ENDPOINT and node.name.endswith("/children")
    ]


ASYMMETRIC_JAX_RS_PATHS = """\
package com.example.health;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@javax.ws.rs.Path("/health")
class HealthResource {
    @javax.ws.rs.GET
    String status() { return "ok"; }
}

class MetricsResource {
    @GET
    @Path("/metrics")
    String metrics() { return "ok"; }
}

@Path(ROOT_PATH)
class DynamicResource {
    @GET
    String unresolved() { return "unknown"; }
}
"""


def test_jax_rs_supports_asymmetric_and_qualified_annotations(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, ASYMMETRIC_JAX_RS_PATHS, "Resources.java")
    exposes = {(edge.src, edge.dst) for edge in batch.edges if edge.kind is EdgeKind.EXPOSES}
    assert (
        "java:endpoint:GET /health",
        "java:com.example.health.HealthResource.status",
    ) in exposes
    assert (
        "java:endpoint:GET /metrics",
        "java:com.example.health.MetricsResource.metrics",
    ) in exposes
    assert not any(dst.endswith(".unresolved") for _, dst in exposes)


@pytest.mark.parametrize("namespace", ["jakarta.ws.rs", "javax.ws.rs"])
def test_jax_rs_unqualified_annotations_require_explicit_import(tmp_path: Path, namespace: str) -> None:
    source = f"""\
package com.example.health;

import {namespace}.GET;
import {namespace}.Path;

@Path("/health")
class HealthResource {{
    @GET
    String status() {{ return "ok"; }}
}}
"""
    batch, _ = _facts(tmp_path, source, "HealthResource.java")
    exposes = {(edge.src, edge.dst) for edge in batch.edges if edge.kind is EdgeKind.EXPOSES}
    assert (
        "java:endpoint:GET /health",
        "java:com.example.health.HealthResource.status",
    ) in exposes


RETROFIT_CLIENT = """\
package com.example.client;

import retrofit2.http.GET;
import retrofit2.http.Path;

interface UserClient {
    @GET("users/{id}")
    User getUser(@Path("id") String id);
}
"""


def test_jax_rs_does_not_treat_retrofit_clients_as_endpoints(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, RETROFIT_CLIENT, "UserClient.java")
    assert not [node for node in batch.nodes if node.kind is NodeKind.ENDPOINT]
    assert not [edge for edge in batch.edges if edge.kind is EdgeKind.EXPOSES]


def test_jax_rs_explicit_non_jax_rs_import_overrides_wildcard(tmp_path: Path) -> None:
    source = RETROFIT_CLIENT.replace(
        "import retrofit2.http.GET;",
        "import jakarta.ws.rs.*;\nimport retrofit2.http.GET;",
    )
    batch, _ = _facts(tmp_path, source, "UserClient.java")
    assert not [node for node in batch.nodes if node.kind is NodeKind.ENDPOINT]


def test_jax_rs_skips_unresolved_unqualified_annotations(tmp_path: Path) -> None:
    source = """\
package com.example.unknown;

@Path("/unknown")
class UnknownResource {
    @GET
    String get() { return "unknown"; }
}
"""
    batch, _ = _facts(tmp_path, source, "UnknownResource.java")
    assert not [node for node in batch.nodes if node.kind is NodeKind.ENDPOINT]


def test_jax_rs_extraction_is_deterministic(tmp_path: Path) -> None:
    first, _ = _facts(tmp_path, JAX_RS_RESOURCE, "OrderResource.java")
    second, _ = _facts(tmp_path, JAX_RS_RESOURCE, "OrderResource.java")
    assert first.nodes == second.nodes
    assert first.edges == second.edges


# ---- Spring MVC, the Java grammar half ----------------------------------------
#
# `test_jvm_routes.py`'s docstring has always said "the grammar halves are tested in
# `test_kotlin_routes.py` and `test_java_extractor`'s Spring cases" — and there were no
# Spring cases here. 104 new lines of Java route reading shipped covered by one corpus
# case, which is how §11 finding 4 (a `@Value` placeholder read as a literal path) reached
# a released language's front-end.

SPRING_JAVA = """\
package com.example.web;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;

@Controller
@RequestMapping("/api")
public class TopicController {
    @GetMapping("/topics")
    public String list() { return ""; }

    @PostMapping("/topics")
    public String create() { return ""; }

    @RequestMapping("/any")
    public String any() { return ""; }
}
"""


def _java_facts(tmp_path: Path, src: str, name: str) -> FactBatch:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = JavaExtractor()
    return ex.extract(path=f, module=ex.module_name(f, tmp_path), rel=name)


def _endpoints(batch: FactBatch) -> set[str]:
    return {n.name for n in batch.nodes if n.kind is NodeKind.ENDPOINT}


def test_java_spring_endpoints_compose_the_class_prefix(tmp_path: Path) -> None:
    """A class-level `@RequestMapping` is a prefix; a method-level one with no verb is not
    an endpoint, because inventing `ANY` would put a verb in the graph nobody wrote."""
    batch = _java_facts(tmp_path, SPRING_JAVA, "TopicController.java")
    assert _endpoints(batch) == {"GET /api/topics", "POST /api/topics"}


def test_java_spring_endpoint_exposes_its_handler(tmp_path: Path) -> None:
    batch = _java_facts(tmp_path, SPRING_JAVA, "TopicController.java")
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    assert ("java:endpoint:GET /api/topics", "java:com.example.web.TopicController.list") in exposes


def test_a_java_property_placeholder_is_not_a_path(tmp_path: Path) -> None:
    """§11 finding 4. `@GetMapping("${api.base}/topics")` is an ordinary Java
    `string_literal`, so it sailed through the node-type test that was supposed to refuse
    it and produced the endpoint `GET /${api.base}/topics`.

    `jvm_routes`'s module docstring already stated the rule — "a path argument that is not
    a string literal — a constant, a concatenation, a `@Value` placeholder — yields `None`
    rather than a guess". The placeholder case needed testing *after* the literal is read,
    not only at the node type.
    """
    src = """\
package com.example.web;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;

@Controller
public class C {
    @GetMapping("${api.base}/topics")
    public String list() { return ""; }
}
"""
    batch = _java_facts(tmp_path, src, "C.java")
    assert not _endpoints(batch)


def test_a_java_annotation_named_getmapping_from_elsewhere_is_not_springs(tmp_path: Path) -> None:
    """Feign puts identical annotations on a *client*; the import is what decides."""
    src = """\
package com.example.web;

import com.acme.rpc.Controller;
import com.acme.rpc.GetMapping;

@Controller
public class C {
    @GetMapping("/topics")
    public String list() { return ""; }
}
"""
    batch = _java_facts(tmp_path, src, "C.java")
    assert not _endpoints(batch)


def test_a_static_call_lands_once_the_repo_declares_the_member(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import RepoCodeExtractor

    (tmp_path / "Foo.java").write_text(_CALLS_SRC, encoding="utf-8")
    (tmp_path / "Helper.java").write_text(
        "package com.other;\npublic class Helper { public static void help() {} }\n", encoding="utf-8"
    )
    calls = {(e.src, e.dst) for e in RepoCodeExtractor().extract(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert ("java:com.ex.Foo.a", "java:com.other.Helper.help") in calls
