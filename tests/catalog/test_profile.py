"""ProjectProfile.from_repo — deterministic, marker-driven detection."""

from __future__ import annotations

from pathlib import Path

from orchestrator.catalog import ProjectProfile, task_type_from_intent


def _write(root: Path, rel: str, body: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_python_django_with_db(tmp_path: Path) -> None:
    _write(tmp_path, "manage.py")
    _write(tmp_path, "app/models.py", "class M: pass")
    _write(tmp_path, "pyproject.toml", "[project]\ndependencies=['django','psycopg']\n")
    _write(tmp_path, "app/migrations/0001_initial.py")
    prof = ProjectProfile.from_repo(tmp_path)
    assert "python" in prof.languages
    assert prof.framework == "django"
    assert prof.has_db is True and prof.has_migrations is True
    assert prof.test_runner == "pytest"  # python default
    assert prof.task_type == "feature"


def test_java_repo(tmp_path: Path) -> None:
    _write(tmp_path, "src/Main.java", "class Main {}")
    _write(
        tmp_path,
        "pom.xml",
        "<project><dependency>org.springframework</dependency><artifactId>junit</artifactId></project>",
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert prof.languages == frozenset({"java"})
    assert prof.framework == "spring"
    assert prof.test_runner == "junit"


def test_csharp_aspnet_with_xunit(tmp_path: Path) -> None:
    _write(tmp_path, "src/Api/Program.cs", "namespace App; public class P { }")
    _write(
        tmp_path,
        "src/Api/Api.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web">'
        '<ItemGroup><PackageReference Include="Microsoft.AspNetCore.Mvc"/></ItemGroup></Project>',
    )
    _write(
        tmp_path,
        "tests/Api.Tests/Api.Tests.csproj",
        '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>'
        '<PackageReference Include="xunit"/>'
        '<PackageReference Include="Microsoft.NET.Test.Sdk"/></ItemGroup></Project>',
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert "csharp" in prof.languages
    assert prof.framework == "aspnet"
    assert prof.test_runner == "xunit"


def test_php_laravel_with_pest(tmp_path: Path) -> None:
    _write(tmp_path, "app/Http/Controllers/OrderController.php", "<?php\nclass OrderController {}\n")
    _write(
        tmp_path,
        "composer.json",
        '{"require": {"laravel/framework": "^11.0"}, "require-dev": {"pestphp/pest": "^2.0"}}\n',
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert "php" in prof.languages
    assert prof.framework == "laravel"
    assert prof.test_runner == "pest"


def test_perl_mojolicious_with_prove(tmp_path: Path) -> None:
    _write(tmp_path, "lib/Shop/Cart.pm", "package Shop::Cart;\n\nsub total { 0 }\n")
    _write(tmp_path, "t/cart.t", "use Test::More;\ndone_testing;\n")
    _write(tmp_path, "cpanfile", "requires 'Mojolicious';\nrequires 'Test::More';\n")
    prof = ProjectProfile.from_repo(tmp_path)
    assert "perl" in prof.languages
    assert prof.framework == "mojolicious"
    assert prof.test_runner == "prove"


def test_kotlin_android_with_the_gradle_kotlin_dsl(tmp_path: Path) -> None:
    """D12: a Gradle-Kotlin repo reported `languages: []` because only the Groovy
    `build.gradle` was read and `.kt` mapped to nothing — measured on the validation
    app, which is 263 Kotlin files and 33 `.kts` scripts."""
    _write(
        tmp_path,
        "core/data/src/main/java/com/demo/TopicRepository.kt",
        "package com.demo\n\nclass TopicRepository {\n    fun topics() = emptyList<String>()\n}\n",
    )
    _write(
        tmp_path,
        "build.gradle.kts",
        'plugins { id("com.android.application") }\n'
        'dependencies {\n  implementation("androidx.core:core-ktx:1.13.1")\n'
        '  testImplementation("junit:junit:4.13.2")\n}\n',
    )
    _write(tmp_path, "settings.gradle.kts", 'include(":core:data")\n')
    prof = ProjectProfile.from_repo(tmp_path)
    assert "kotlin" in prof.languages
    assert prof.framework == "android"
    assert prof.test_runner == "junit"


def test_kotlin_spring_service_is_not_mistaken_for_android(tmp_path: Path) -> None:
    """The framework name is what separates a route *provider* from a consumer (D10)."""
    _write(
        tmp_path,
        "src/main/kotlin/com/demo/OwnerController.kt",
        "package com.demo\n\nclass OwnerController\n",
    )
    _write(
        tmp_path,
        "build.gradle.kts",
        'plugins { kotlin("jvm") }\n'
        'dependencies { implementation("org.springframework.boot:spring-boot-starter-web") }\n',
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert "kotlin" in prof.languages
    assert prof.framework == "spring"


def test_kts_build_script_alone_is_a_marker_not_a_language(tmp_path: Path) -> None:
    """D11/D12: a Gradle script is a build file. It must not make the repo "Kotlin"."""
    _write(tmp_path, "build.gradle.kts", 'plugins { id("com.android.library") }\n')
    prof = ProjectProfile.from_repo(tmp_path)
    assert "kotlin" not in prof.languages


def test_greenfield_empty_repo(tmp_path: Path) -> None:
    prof = ProjectProfile.from_repo(tmp_path)
    assert prof.languages == frozenset()
    assert prof.framework is None
    assert prof.has_db is False and prof.has_migrations is False
    assert prof.test_runner is None


def test_ignores_vendored_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "print(1)")
    _write(tmp_path, "node_modules/pkg/index.js", "module.exports={}")
    _write(tmp_path, ".venv/lib/thing.py", "x=1")
    prof = ProjectProfile.from_repo(tmp_path)
    # node_modules + .venv are ignored, so only the real python file counts.
    assert prof.languages == frozenset({"python"})


def test_task_type_classification() -> None:
    assert task_type_from_intent("Migrate users to new schema") == "migration"
    assert task_type_from_intent("Fix the off-by-one bug") == "bugfix"
    assert task_type_from_intent("Add CSV export") == "feature"
    assert task_type_from_intent(None, None) == "feature"


def test_profile_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "a.py")
    _write(tmp_path, "b.ts")
    assert ProjectProfile.from_repo(tmp_path) == ProjectProfile.from_repo(tmp_path)


def test_cpp_reached_headers_do_not_falsely_report_c(tmp_path: Path) -> None:
    import pytest

    pytest.importorskip("tree_sitter_cpp")
    _write(tmp_path, "main.cpp", '#include "api.h"\n')
    _write(tmp_path, "api.h", "class Widget {};")
    assert ProjectProfile.from_repo(tmp_path).languages == frozenset({"cpp"})
    _write(tmp_path, "unused.h", "struct CRecord { int value; };")
    assert ProjectProfile.from_repo(tmp_path).languages == frozenset({"c", "cpp"})


def test_cpp_header_routing_stops_at_nested_checkout(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import RepoCodeExtractor

    for marker in ("directory", "file"):
        root = tmp_path / marker
        _write(root, "api.h", "struct Entry { int value; };\n")
        _write(root, "child/main.cpp", '#include "../api.h"\n')
        if marker == "directory":
            (root / "child/.git").mkdir()
        else:
            _write(root, "child/.git", "gitdir: /unused/submodule\n")
        assert ProjectProfile.from_repo(root).languages == frozenset({"c"})
        batch = RepoCodeExtractor().extract(root)
        assert not any(n.language == "cpp" for n in batch.nodes)


def test_a_blazor_project_is_named_blazor_not_aspnet(tmp_path: Path) -> None:
    """Every Blazor project also references Microsoft.AspNetCore; the Components package or the
    WebAssembly SDK is the tell, and it must be checked first."""
    _write(tmp_path, "src/Web/App.razor", "<h1>Hi</h1>\n")
    _write(
        tmp_path,
        "src/Web/Web.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup>'
        '<PackageReference Include="Microsoft.AspNetCore.Components.Web"/></ItemGroup></Project>',
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert "csharp" in prof.languages
    assert prof.framework == "blazor"
