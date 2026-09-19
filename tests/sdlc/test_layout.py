"""TargetLayout resolution: package-name derivation + auto/new/existing modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.layout import (
    derive_package_name,
    detect_existing_package,
    is_effectively_empty,
    resolve_layout,
)


class TestIsEffectivelyEmpty:
    def test_bare_clone_is_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "README.md").write_text("# repo")
        assert is_effectively_empty(tmp_path) is True

    def test_loose_source_is_not_empty(self, tmp_path: Path) -> None:
        (tmp_path / "stack_decision.py").write_text("x = 1\n")
        assert is_effectively_empty(tmp_path) is False


class TestDerivePackageName:
    def test_repo_url_with_trailing_dot(self) -> None:
        # the real PROJ repo name ends in a literal dot
        url = "https://github.com/synaptixs/Example-Service."
        assert derive_package_name(url) == "example_service"

    def test_strips_dot_git(self) -> None:
        assert derive_package_name("git@github.com:org/My-Repo.git") == "my_repo"

    def test_spaces_and_punctuation(self) -> None:
        assert derive_package_name("My Cool Project!!") == "my_cool_project"

    def test_leading_digit_is_guarded(self) -> None:
        assert derive_package_name("123-service") == "pkg_123_service"

    def test_keyword_is_guarded(self) -> None:
        assert derive_package_name("class") == "class_pkg"

    def test_empty_falls_back(self) -> None:
        assert derive_package_name("---") == "app"


class TestDetectExistingPackage:
    def test_src_layout(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "widget").mkdir(parents=True)
        (tmp_path / "src" / "widget" / "__init__.py").write_text("")
        assert detect_existing_package(tmp_path) == ("widget", "src/widget")

    def test_flat_layout(self, tmp_path: Path) -> None:
        (tmp_path / "gadget").mkdir()
        (tmp_path / "gadget" / "__init__.py").write_text("")
        assert detect_existing_package(tmp_path) == ("gadget", "gadget")

    def test_ignores_tests_and_dotdirs(self, tmp_path: Path) -> None:
        for d in ("tests", "docs", ".hidden"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "__init__.py").write_text("")
        assert detect_existing_package(tmp_path) is None

    def test_empty_repo(self, tmp_path: Path) -> None:
        assert detect_existing_package(tmp_path) is None


class TestResolveLayout:
    def test_auto_empty_repo_is_new_with_src_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="auto", repo="https://x/Example-Service.")
        assert layout.mode == "new"
        assert layout.package_name == "example_service"
        assert layout.source_dir == "src/example_service"
        assert layout.tests_dir == "tests"
        assert layout.src_layout is True

    def test_auto_existing_package_is_existing(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "widget").mkdir(parents=True)
        (tmp_path / "src" / "widget" / "__init__.py").write_text("")
        layout = resolve_layout(tmp_path, mode="auto", repo="https://x/widget")
        assert layout.mode == "existing"
        assert (layout.package_name, layout.source_dir) == ("widget", "src/widget")

    def test_new_forces_scaffold_even_with_existing(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "widget").mkdir(parents=True)
        (tmp_path / "src" / "widget" / "__init__.py").write_text("")
        layout = resolve_layout(tmp_path, mode="new", repo="https://x/widget")
        assert layout.mode == "new"

    def test_existing_without_package_falls_back_no_scaffold(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="existing", repo="https://x/thing")
        assert layout.mode == "existing"  # never scaffolds
        assert layout.source_dir == "src/thing"

    def test_package_name_override_wins(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", package_name="custom", repo="https://x/ignored")
        assert layout.package_name == "custom"
        assert layout.source_dir == "src/custom"

    def test_module_rel_path(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", package_name="pkg")
        assert layout.module_rel_path("page") == "src/pkg/page.py"


class TestJavaLayout:
    def test_derive_java_package(self) -> None:
        from orchestrator.sdlc.layout import derive_java_package

        assert derive_java_package("https://x/Example-Service.") == "org.example.exampleservice"

    def test_new_java_maven_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="java", repo="https://x/widgets")
        assert layout.language == "java" and layout.build_tool == "maven" and layout.mode == "new"
        assert layout.package_name == "org.example.widgets"
        assert layout.source_dir == "src/main/java/org/example/widgets"
        assert layout.tests_dir == "src/test/java/org/example/widgets"
        assert layout.module_rel_path("Widget") == "src/main/java/org/example/widgets/Widget.java"

    def test_detect_existing_java_package(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_java_layout

        pkg = tmp_path / "src" / "main" / "java" / "com" / "demo"
        pkg.mkdir(parents=True)
        (pkg / "Widget.java").write_text("package com.demo;\npublic class Widget {}\n")
        assert detect_java_layout(tmp_path) == (
            "com.demo",
            "src/main/java/com/demo",
            "src/test/java/com/demo",
        )

    def test_auto_existing_java_is_not_scaffolded(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "main" / "java" / "com" / "demo"
        pkg.mkdir(parents=True)
        (pkg / "Widget.java").write_text("package com.demo;\npublic class Widget {}\n")
        (tmp_path / "pom.xml").write_text("<project/>")
        layout = resolve_layout(tmp_path, mode="auto", language="java")
        assert (
            layout.mode == "existing" and layout.package_name == "com.demo" and layout.build_tool == "maven"
        )


class TestTypeScriptLayout:
    def test_derive_npm_package(self) -> None:
        from orchestrator.sdlc.layout import derive_npm_package

        assert derive_npm_package("https://x/Example-Service.") == "example-service"
        assert derive_npm_package("git@github.com:org/My-Repo.git") == "my-repo"
        assert derive_npm_package("---") == "app"

    def test_new_typescript_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="typescript", repo="https://x/widgets")
        assert layout.language == "typescript" and layout.build_tool == "npm" and layout.mode == "new"
        assert layout.package_name == "widgets"
        assert layout.source_dir == "src" and layout.tests_dir == "src"  # co-located tests
        assert layout.module_rel_path("account") == "src/account.ts"

    def test_detect_existing_typescript_project(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_typescript_layout

        (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
        (tmp_path / "src").mkdir()
        assert detect_typescript_layout(tmp_path) == ("my-app", "src", "src")

    def test_auto_existing_ts_reads_name_and_pm(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
        (tmp_path / "pnpm-lock.yaml").write_text("")
        (tmp_path / "src").mkdir()
        layout = resolve_layout(tmp_path, mode="auto", language="typescript")
        assert layout.mode == "existing"
        assert layout.package_name == "my-app" and layout.build_tool == "pnpm"

    def test_yarn_lockfile_detected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text('{"name": "y"}\n')
        (tmp_path / "yarn.lock").write_text("")
        layout = resolve_layout(tmp_path, mode="auto", language="typescript")
        assert layout.build_tool == "yarn"


class TestCSharpLayout:
    def test_derive_csharp_namespace(self) -> None:
        from orchestrator.sdlc.layout import derive_csharp_namespace

        assert derive_csharp_namespace("https://x/Example-Service.") == "ExampleService"
        assert derive_csharp_namespace("git@github.com:org/My-Repo.git") == "MyRepo"
        assert derive_csharp_namespace("9lives") == "App9lives"
        assert derive_csharp_namespace("---") == "App"

    def test_new_csharp_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="csharp", repo="https://x/widgets")
        assert layout.language == "csharp" and layout.build_tool == "dotnet" and layout.mode == "new"
        assert layout.package_name == "Widgets"
        assert layout.source_dir == "src/Widgets"
        assert layout.tests_dir == "tests/Widgets.Tests"
        assert layout.module_rel_path("Widget") == "src/Widgets/Widget.cs"

    def test_detect_existing_csharp_project(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_csharp_layout

        src = tmp_path / "src" / "Shop"
        src.mkdir(parents=True)
        (src / "Shop.csproj").write_text("<Project/>")
        tst = tmp_path / "tests" / "Shop.Tests"
        tst.mkdir(parents=True)
        (tst / "Shop.Tests.csproj").write_text("<Project/>")
        assert detect_csharp_layout(tmp_path) == ("Shop", "src/Shop", "tests/Shop.Tests")

    def test_auto_existing_csharp_is_not_scaffolded(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "Shop"
        src.mkdir(parents=True)
        (src / "Shop.csproj").write_text("<Project/>")
        layout = resolve_layout(tmp_path, mode="auto", language="csharp")
        assert layout.mode == "existing"
        assert layout.package_name == "Shop" and layout.build_tool == "dotnet"
        # no test project present → tests dir is derived from the source project name.
        assert layout.tests_dir == "tests/Shop.Tests"


class TestCLayout:
    def test_new_c_cmake_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="c", repo="https://x/Calc-Lib")
        assert layout.language == "c" and layout.build_tool == "cmake" and layout.mode == "new"
        assert layout.package_name == "calc_lib"
        assert layout.source_dir == "src" and layout.tests_dir == "tests"
        assert layout.module_rel_path("vector") == "src/vector.c"

    def test_detect_existing_cmake_project(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_c_layout

        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.15)\nproject(mylib C)\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.c").write_text("int x;\n")
        (tmp_path / "tests").mkdir()
        # package comes from the CMake project() name; src/ holds .c so it's the source dir.
        assert detect_c_layout(tmp_path) == ("mylib", "src", "tests")

    def test_auto_existing_make_project(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\tcc -o app main.c\n")
        layout = resolve_layout(tmp_path, mode="auto", language="c")
        assert layout.mode == "existing" and layout.build_tool == "make"

    def test_detects_meson_build_tool(self, tmp_path: Path) -> None:
        # Meson projects are recognized as C with build_tool=meson, so
        # the runner can fail fast with a clear "CMake-only" message (not a cryptic
        # cmake error). CMake remains the supported brownfield build path.
        from orchestrator.sdlc.layout import detect_c_layout

        (tmp_path / "meson.build").write_text("project('demo', 'c')\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.c").write_text("int x;\n")
        assert detect_c_layout(tmp_path) is not None
        layout = resolve_layout(tmp_path, mode="existing", language="c")
        assert layout.build_tool == "meson"


class TestCppLayout:
    def test_new_cpp_cmake_layout(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="cpp", repo="https://x/Vec-Lib")
        assert layout.language == "cpp" and layout.build_tool == "cmake" and layout.mode == "new"
        assert layout.package_name == "vec_lib"
        assert layout.source_dir == "src" and layout.tests_dir == "tests"
        assert layout.module_rel_path("vector") == "src/vector.cpp"

    def test_detect_existing_cpp_project(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_cpp_layout

        (tmp_path / "CMakeLists.txt").write_text("project(engine CXX)\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.cpp").write_text("int x;\n")
        (tmp_path / "tests").mkdir()
        # src holds .cpp → it's the source dir; package from the CMake project() name.
        assert detect_cpp_layout(tmp_path) == ("engine", "src", "tests")


class TestGoLayout:
    def test_derive_go_module(self) -> None:
        from orchestrator.sdlc.layout import derive_go_module

        assert derive_go_module("https://x/Example-Service.") == "exampleservice"
        assert derive_go_module("git@github.com:org/My-Repo.git") == "myrepo"
        assert derive_go_module("9lives") == "pkg9lives"
        assert derive_go_module("func") == "funcpkg"  # Go keyword guarded
        assert derive_go_module("---") == "app"

    def test_new_go_layout_is_root_package(self, tmp_path: Path) -> None:
        layout = resolve_layout(tmp_path, mode="new", language="go", repo="https://x/widget")
        assert layout.language == "go" and layout.build_tool == "go" and layout.mode == "new"
        assert layout.package_name == "widget"
        # Greenfield Go: a single package at the module root; tests co-located.
        assert layout.source_dir == "." and layout.tests_dir == "."
        assert layout.module_rel_path("account") == "./account.go"

    def test_detect_existing_go_module_root(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_go_layout

        (tmp_path / "go.mod").write_text("module github.com/acme/widget\n\ngo 1.22\n")
        (tmp_path / "widget.go").write_text("package widget\n")
        # The dir's EXISTING package clause (not the module path); co-located tests.
        assert detect_go_layout(tmp_path) == ("widget", ".", ".")

    def test_detect_existing_go_module_subdir(self, tmp_path: Path) -> None:
        from orchestrator.sdlc.layout import detect_go_layout

        (tmp_path / "go.mod").write_text("module acme\n")
        pkg = tmp_path / "internal" / "core"
        pkg.mkdir(parents=True)
        (pkg / "core.go").write_text("package core\n")
        # Placement = the lib package; package clause = its own `package core`.
        assert detect_go_layout(tmp_path) == ("core", "internal/core", "internal/core")

    def test_brownfield_placement_prefers_root_module_lib_over_main_and_demo(self, tmp_path: Path) -> None:
        # Mirrors the OTel shape that caused 4.4's false green: a root module with a lib
        # package under tool/, plus a `main` and a nested-module demo. Placement must pick
        # the root-module lib (tool/util, package util), not demo/ or a main.
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        (tmp_path / "cmd").mkdir()
        (tmp_path / "cmd" / "main.go").write_text("package main\n\nfunc main() {}\n")
        (tmp_path / "tool" / "util").mkdir(parents=True)
        (tmp_path / "tool" / "util" / "util.go").write_text("package util\n")
        demo = tmp_path / "demo" / "svc"  # a nested module — must be skipped
        demo.mkdir(parents=True)
        (demo / "go.mod").write_text("module example.com/app/demo/svc\n")
        (demo / "svc.go").write_text("package svc\n")
        from orchestrator.sdlc.layout import detect_go_layout

        detected = detect_go_layout(tmp_path)
        assert detected is not None
        pkg, source_dir, _ = detected
        assert (pkg, source_dir) == ("util", "tool/util")

    def test_auto_existing_go_is_not_scaffolded(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module acme\n")
        (tmp_path / "acme.go").write_text("package acme\n")
        layout = resolve_layout(tmp_path, mode="auto", language="go")
        assert layout.mode == "existing" and layout.package_name == "acme"


def test_new_sql_layout_is_migrations_dir(tmp_path: Path) -> None:
    layout = resolve_layout(tmp_path, mode="new", language="sql", repo="https://x/shop-db")
    assert layout.language == "sql" and layout.mode == "new"
    assert layout.source_dir == "migrations" and layout.tests_dir == "migrations"
    assert layout.build_tool == "postgres"  # dialect carried on build_tool
    assert layout.module_rel_path("orders") == "migrations/orders.sql"


def test_auto_detects_existing_migrations_dir(tmp_path: Path) -> None:
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001_init.sql").write_text("CREATE TABLE t (id INT);\n")
    layout = resolve_layout(tmp_path, mode="auto", language="sql")
    assert layout.language == "sql" and layout.mode == "existing"


# ---- B10: which project, when a repository holds several -------------------------------------


def _nss_1239(root: Path) -> None:
    """The NSS-1239 shape: three projects, the work in the one that sorts last, and a generated
    `obj/` tree that must not vote."""
    for proj, files in (
        ("ApiClient", ["Auction.cs"]),
        ("WebApp", ["C1.cs", "C2.cs", "C3.cs", "C4.cs", "C5.cs"]),
        ("UnitTests", ["AuctionTests.cs"]),
    ):
        (root / proj).mkdir(parents=True, exist_ok=True)
        for f in files:
            (root / proj / f).write_text("class X {}\n", encoding="utf-8")
    (root / "ApiClient" / "ApiClient.csproj").write_text("<Project/>\n", encoding="utf-8")
    (root / "UnitTests" / "UnitTests.csproj").write_text("<Project/>\n", encoding="utf-8")
    (root / "WebApp" / "commercial-secondary-sales.csproj").write_text("<Project/>\n", encoding="utf-8")
    ui = root / "WebApp" / "Features" / "Common" / "Auctions" / "Ui"
    ui.mkdir(parents=True)
    (ui / "AuctionCoilsUi.razor").write_text("<div/>\n", encoding="utf-8")
    obj = root / "ApiClient" / "obj" / "Generated"
    obj.mkdir(parents=True)
    for i in range(50):
        (obj / f"G{i}.cs").write_text("class G {}\n", encoding="utf-8")


def test_nss_1239_the_project_holding_the_design_s_files_is_the_target(tmp_path: Path) -> None:
    """NSS-1239 scaffolded into `ApiClient` — first in sorted order — while its own plan named
    five files under `WebApp/`. Codegen could not resolve `Product`, spent every refine on
    `using` directives, and ended FAILED after six test runs."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    _nss_1239(tmp_path)
    design = [
        "WebApp/Features/Common/Auctions/Ui/AuctionCoilsUi.razor",
        "WebApp/C1.cs",
    ]
    assert detect_csharp_layout(tmp_path, prefer_paths=design) == (
        "commercial-secondary-sales",
        "WebApp",
        "UnitTests",
    )


def test_without_a_design_the_project_with_the_most_source_wins(tmp_path: Path) -> None:
    """`sdlc feature` can run with no plan behind it. Alphabetical is not an answer; how much
    source a project holds is at least evidence — and a generated `obj/` tree does not vote."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    _nss_1239(tmp_path)
    detected = detect_csharp_layout(tmp_path)
    assert detected is not None and detected[1] == "WebApp"


def test_the_layout_says_which_rule_chose_the_project(tmp_path: Path) -> None:
    """A run that built in the wrong place should not make the reader guess whether the project
    was derived from the ticket or defaulted."""
    from orchestrator.sdlc.layout import resolve_layout

    _nss_1239(tmp_path)
    named = resolve_layout(tmp_path, mode="existing", language="csharp", prefer_paths=["WebApp/C1.cs"])
    assert named.chosen_reason == "holds 1 of 1 file(s) the design names"
    assert resolve_layout(tmp_path, mode="existing", language="csharp").chosen_reason.startswith(
        "most source"
    )


def test_a_single_project_repo_is_unchanged_and_says_so(tmp_path: Path) -> None:
    from orchestrator.sdlc.layout import resolve_layout

    (tmp_path / "Shop").mkdir()
    (tmp_path / "Shop" / "Shop.csproj").write_text("<Project/>\n", encoding="utf-8")
    (tmp_path / "Shop" / "Cart.cs").write_text("class Cart {}\n", encoding="utf-8")
    layout = resolve_layout(tmp_path, mode="existing", language="csharp")
    assert (layout.package_name, layout.source_dir) == ("Shop", "Shop")
    assert layout.chosen_reason == "only candidate"


def test_the_deepest_project_owns_its_own_files(tmp_path: Path) -> None:
    """Nested projects are the normal .NET shape. `WebApp/Tests/` owns what is under it, not
    `WebApp/`, or every nested file would vote for its parent."""
    from orchestrator.sdlc.layout import choose_project

    outer = tmp_path / "WebApp" / "WebApp.csproj"
    inner = tmp_path / "WebApp" / "Integration" / "Integration.csproj"
    inner.parent.mkdir(parents=True)
    outer.write_text("<Project/>\n", encoding="utf-8")
    inner.write_text("<Project/>\n", encoding="utf-8")

    chosen = choose_project([outer, inner], prefer_paths=["WebApp/Integration/Thing.cs"], root=tmp_path)
    assert chosen == inner


def test_the_explicit_package_name_still_outranks_the_chooser(tmp_path: Path) -> None:
    """A flag is a human's instruction; the chooser is an inference."""
    from orchestrator.sdlc.layout import resolve_layout

    _nss_1239(tmp_path)
    layout = resolve_layout(
        tmp_path, mode="existing", language="csharp", package_name="Chosen", prefer_paths=["WebApp/C1.cs"]
    )
    assert layout.package_name == "Chosen"


@pytest.mark.parametrize(
    "language",
    ["python", "java", "kotlin", "typescript", "csharp", "go", "php", "perl", "c", "cpp", "sql"],
)
def test_every_language_accepts_the_preference(tmp_path: Path, language: str) -> None:
    """`Toolchain.layout` is `Callable[..., TargetLayout]`, so a resolver that did not accept the
    new keyword would raise TypeError at run time and no type checker would have said so."""
    from orchestrator.sdlc.layout import resolve_layout

    assert resolve_layout(tmp_path, mode="auto", language=language, prefer_paths=["src/a.py"])


def test_a_multi_module_java_build_resolves_the_module_the_design_names(tmp_path: Path) -> None:
    """`root/src/main/java` is the single-module shape. A Maven or Gradle monorepo keeps each
    module's tree under `<module>/src/main/java`, where the old lookup saw nothing at all and the
    layout fell through to a package name that does not exist in the repo."""
    from orchestrator.sdlc.layout import detect_java_layout

    for module, pkg in (("api", "com/acme/api"), ("worker", "com/acme/worker")):
        d = tmp_path / module / "src" / "main" / "java" / pkg
        d.mkdir(parents=True)
        (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")

    assert detect_java_layout(tmp_path, prefer_paths=["worker/src/main/java/com/acme/worker/Main.java"]) == (
        "com.acme.worker",
        "worker/src/main/java/com/acme/worker",
        "worker/src/test/java/com/acme/worker",
    )


def test_a_single_module_java_build_is_unchanged(tmp_path: Path) -> None:
    from orchestrator.sdlc.layout import detect_java_layout

    d = tmp_path / "src" / "main" / "java" / "com" / "acme"
    d.mkdir(parents=True)
    (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    assert detect_java_layout(tmp_path) == (
        "com.acme",
        "src/main/java/com/acme",
        "src/test/java/com/acme",
    )


def _two_kotlin_modules(root: Path) -> None:
    for module, pkg in (("api", "com/acme/api"), ("worker", "com/acme/worker")):
        d = root / module / "src" / "main" / "kotlin" / pkg
        d.mkdir(parents=True)
        (d / "Main.kt").write_text("class Main\n", encoding="utf-8")
        (root / module / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    (root / "settings.gradle.kts").write_text('include(":api", ":worker")\n', encoding="utf-8")


def test_an_explicit_package_name_outranks_the_files_a_ticket_mentions(tmp_path: Path) -> None:
    """A ticket may *mention* a file it only reads. An operator naming the package is giving an
    instruction, and an inference must not overrule it (D7)."""
    from orchestrator.sdlc.layout import resolve_layout

    _two_kotlin_modules(tmp_path)
    layout = resolve_layout(
        tmp_path,
        mode="existing",
        language="kotlin",
        package_name="com.acme.api",
        prefer_paths=["worker/src/main/kotlin/com/acme/worker/Main.kt"],
    )
    assert layout.module == "api"
    assert layout.source_dir == "api/src/main/kotlin/com/acme/api"


def test_choosing_a_module_from_the_ticket_takes_that_module_s_own_package(tmp_path: Path) -> None:
    """Keeping the repo-derived name while switching module is how a package nobody declared gets
    written into a brownfield module — the failure `_module_layout` already records once."""
    from orchestrator.sdlc.layout import resolve_layout

    _two_kotlin_modules(tmp_path)
    layout = resolve_layout(
        tmp_path,
        mode="existing",
        language="kotlin",
        prefer_paths=["worker/src/main/kotlin/com/acme/worker/Main.kt"],
    )
    assert layout.module == "worker"
    assert layout.package_name == "com.acme.worker"  # the module's own, never `org.example.<repo>`
    assert layout.source_dir == "worker/src/main/kotlin/com/acme/worker"
    assert layout.chosen_reason == "holds 1 of 1 file(s) the design names"


def test_a_ticket_that_names_nothing_still_refuses_to_guess_a_module(tmp_path: Path) -> None:
    """Two modules and no evidence is the case `kotlin_project_error` exists to report. A guess
    dressed as a choice is worse than the refusal."""
    from orchestrator.sdlc.layout import resolve_layout

    _two_kotlin_modules(tmp_path)
    layout = resolve_layout(tmp_path, mode="existing", language="kotlin")
    assert layout.module == "" and layout.source_dir == ""


def test_a_module_directory_with_a_dot_in_its_name_owns_only_its_own_files(tmp_path: Path) -> None:
    """`acme.worker/` has a suffix by `Path`'s reckoning, so a name-based test made it the
    repository root — and every file in the repo, including a top-level README, counted for it."""
    from orchestrator.sdlc.layout import detect_java_layout, project_holding

    for module in ("acme.worker", "web"):
        d = tmp_path / module / "src" / "main" / "java" / "com" / "acme"
        d.mkdir(parents=True)
        (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")

    assert (
        project_holding(
            [tmp_path / "acme.worker", tmp_path / "web"], prefer_paths=["README.md"], root=tmp_path
        )
        is None
    )
    detected = detect_java_layout(tmp_path, prefer_paths=["web/src/main/java/com/acme/Main.java"])
    assert detected is not None and detected[1] == "web/src/main/java/com/acme"


def test_a_vendored_asset_tree_cannot_outvote_a_real_project(tmp_path: Path) -> None:
    """`wwwroot/lib/` is where an ASP.NET app keeps vendored jQuery and Bootstrap. Counting every
    front-end suffix let ten C# files plus 1,200 vendored `.js` beat an eighty-file API client."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    (tmp_path / "ApiClient").mkdir()
    (tmp_path / "ApiClient" / "ApiClient.csproj").write_text("<Project/>\n", encoding="utf-8")
    for i in range(80):
        (tmp_path / "ApiClient" / f"C{i}.cs").write_text("class C {}\n", encoding="utf-8")
    vendor = tmp_path / "WebApp" / "wwwroot" / "lib" / "jquery"
    vendor.mkdir(parents=True)
    (tmp_path / "WebApp" / "WebApp.csproj").write_text("<Project/>\n", encoding="utf-8")
    for i in range(10):
        (tmp_path / "WebApp" / f"W{i}.cs").write_text("class W {}\n", encoding="utf-8")
    for i in range(1200):
        (vendor / f"v{i}.js").write_text("//\n", encoding="utf-8")

    detected = detect_csharp_layout(tmp_path)
    assert detected is not None and detected[1] == "ApiClient"


def test_a_nested_maven_module_is_found(tmp_path: Path) -> None:
    """`include(":services:worker")` and Maven aggregators nest. One level of globbing found
    nothing at all and the layout fell through to a package absent from the repository."""
    from orchestrator.sdlc.layout import detect_java_layout

    for module in ("api", "worker"):
        d = tmp_path / "services" / module / "src" / "main" / "java" / "com" / "acme" / module
        d.mkdir(parents=True)
        (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")

    detected = detect_java_layout(
        tmp_path, prefer_paths=["services/worker/src/main/java/com/acme/worker/Main.java"]
    )
    assert detected == (
        "com.acme.worker",
        "services/worker/src/main/java/com/acme/worker",
        "services/worker/src/test/java/com/acme/worker",
    )


def test_the_java_layout_says_which_rule_chose_the_module(tmp_path: Path) -> None:
    from orchestrator.sdlc.layout import resolve_layout

    for module in ("api", "worker"):
        d = tmp_path / module / "src" / "main" / "java" / "com" / "acme" / module
        d.mkdir(parents=True)
        (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")

    layout = resolve_layout(
        tmp_path,
        mode="existing",
        language="java",
        prefer_paths=["worker/src/main/java/com/acme/worker/Main.java"],
    )
    assert layout.chosen_reason == "holds 1 of 1 file(s) the design names"


def test_naming_the_project_settles_it_outright(tmp_path: Path) -> None:
    """The lever a human needs when the inference is wrong. Before this, `--package-name`
    renamed the layout without retargeting it, so an operator who *knew* the run was aimed at
    the wrong project had no way to say so — reported from the field on NSS-1239."""
    from orchestrator.sdlc.layout import resolve_layout

    _nss_1239(tmp_path)
    layout = resolve_layout(
        tmp_path,
        mode="existing",
        language="csharp",
        package_name="ApiClient",
        prefer_paths=["WebApp/C1.cs"],  # the inference would say WebApp; the operator says otherwise
    )
    assert (layout.package_name, layout.source_dir) == ("ApiClient", "ApiClient")
    assert layout.chosen_reason == "named explicitly"


def test_a_vendored_java_tree_is_never_a_placement_candidate(tmp_path: Path) -> None:
    """`third_party/` is source-shaped, so the per-language filter cannot help: a 50-file
    vendored Guava beat the repository's own one-file service on "most source", and a ticket
    naming no path would have opened a PR editing somebody else's code."""
    from orchestrator.sdlc.layout import detect_java_layout

    own = tmp_path / "services" / "api" / "src" / "main" / "java" / "com" / "acme"
    own.mkdir(parents=True)
    (own / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    vendored = tmp_path / "third_party" / "guavaish" / "src" / "main" / "java" / "com" / "google"
    vendored.mkdir(parents=True)
    for i in range(50):
        (vendored / f"G{i}.java").write_text("class G {}\n", encoding="utf-8")

    detected = detect_java_layout(tmp_path)
    assert detected is not None and detected[1] == "services/api/src/main/java/com/acme"


def test_the_kotlin_layout_says_why_it_rewrote_the_package(tmp_path: Path) -> None:
    """Both remaining Kotlin branches replace the operator's derived package with the module's
    own — which is right, and is exactly the surprise the `[layout]` line exists to explain."""
    from orchestrator.sdlc.layout import resolve_layout

    d = tmp_path / "app" / "src" / "main" / "kotlin" / "com" / "acme" / "app"
    d.mkdir(parents=True)
    (d / "Main.kt").write_text("class Main\n", encoding="utf-8")
    (tmp_path / "app" / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    (tmp_path / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")

    layout = resolve_layout(tmp_path, mode="existing", language="kotlin")
    assert layout.module == "app"
    assert layout.chosen_reason == "only Kotlin module in the build"


def test_a_vendored_dotnet_project_is_not_a_candidate_either(tmp_path: Path) -> None:
    """The exclusion has to reach the language the track exists for: a 99-file
    `third_party/Vendor.Lib` beat the repository's own five-file project on "most source"."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    (tmp_path / "src" / "Acme.Worker").mkdir(parents=True)
    (tmp_path / "src" / "Acme.Worker" / "Acme.Worker.csproj").write_text("<Project/>\n", encoding="utf-8")
    for i in range(5):
        (tmp_path / "src" / "Acme.Worker" / f"W{i}.cs").write_text("class W {}\n", encoding="utf-8")
    vendored = tmp_path / "third_party" / "Vendor.Lib"
    vendored.mkdir(parents=True)
    (vendored / "Vendor.Lib.csproj").write_text("<Project/>\n", encoding="utf-8")
    for i in range(99):
        (vendored / f"V{i}.cs").write_text("class V {}\n", encoding="utf-8")

    detected = detect_csharp_layout(tmp_path)
    assert detected is not None and detected[1] == "src/Acme.Worker"
    # …but a ticket that names a file inside one is still obeyed.
    named = detect_csharp_layout(tmp_path, prefer_paths=["third_party/Vendor.Lib/V1.cs"])
    assert named is not None and named[1] == "third_party/Vendor.Lib"


def test_a_module_the_ticket_names_survives_the_vendored_prune(tmp_path: Path) -> None:
    """An SDK repository's own `examples/demo` is first-party to whoever filed the ticket about
    it. Pruning it sent the run into a module nobody named, while the `[layout]` line claimed
    there had been only one candidate."""
    from orchestrator.sdlc.layout import detect_java_layout

    for module, pkg in (("services/api", "com/acme"), ("examples/demo", "com/acme/demo")):
        d = tmp_path / module / "src" / "main" / "java" / pkg
        d.mkdir(parents=True)
        (d / "Main.java").write_text("class Main {}\n", encoding="utf-8")

    named = detect_java_layout(tmp_path, prefer_paths=["examples/demo/src/main/java/com/acme/demo/Main.java"])
    assert named is not None and named[1] == "examples/demo/src/main/java/com/acme/demo"
    unnamed = detect_java_layout(tmp_path)
    assert unnamed is not None and unnamed[1] == "services/api/src/main/java/com/acme"


def test_the_test_project_belongs_to_the_project_being_built(tmp_path: Path) -> None:
    """`WebApp.Tests` is not `ApiClient`'s suite. Taking the repository's first `*Tests.csproj`
    was harmless while both followed one inference, and became a mismatch the moment a project
    could be named — the codegen prompt would have told the model to write into another project's
    tests."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    for proj in ("ApiClient", "WebApp", "WebApp.Tests"):
        (tmp_path / proj).mkdir()
        (tmp_path / proj / f"{proj}.csproj").write_text("<Project/>\n", encoding="utf-8")
        (tmp_path / proj / "A.cs").write_text("class A {}\n", encoding="utf-8")

    assert detect_csharp_layout(tmp_path, project="WebApp") == ("WebApp", "WebApp", "WebApp.Tests")
    assert detect_csharp_layout(tmp_path, project="ApiClient") == (
        "ApiClient",
        "ApiClient",
        "tests/ApiClient.Tests",
    )


def test_a_solution_with_one_shared_suite_still_uses_it(tmp_path: Path) -> None:
    """NSS-1239's real shape: `UnitTests` is named after no project, so it is the repository's
    one suite and every project's tests belong in it."""
    from orchestrator.sdlc.layout import detect_csharp_layout

    for proj in ("ApiClient", "WebApp", "UnitTests"):
        (tmp_path / proj).mkdir()
        (tmp_path / proj / f"{proj}.csproj").write_text("<Project/>\n", encoding="utf-8")
        (tmp_path / proj / "A.cs").write_text("class A {}\n", encoding="utf-8")

    assert detect_csharp_layout(tmp_path, project="WebApp") == ("WebApp", "WebApp", "UnitTests")


def test_the_kotlin_layout_names_the_package_match_too(tmp_path: Path) -> None:
    """The second of the two branches that rewrite the package — asserted so the string cannot be
    dropped without a failure."""
    from orchestrator.sdlc.layout import resolve_layout

    d = tmp_path / "app" / "src" / "main" / "kotlin" / "com" / "acme" / "app"
    d.mkdir(parents=True)
    (d / "Main.kt").write_text("class Main\n", encoding="utf-8")
    (tmp_path / "app" / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")
    (tmp_path / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")

    layout = resolve_layout(tmp_path, mode="existing", language="kotlin", package_name="com.acme.app")
    assert layout.chosen_reason == "module already holds this package"
