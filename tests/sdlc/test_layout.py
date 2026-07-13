"""TargetLayout resolution: package-name derivation + auto/new/existing modes."""

from __future__ import annotations

from pathlib import Path

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
