"""Authoritative layout guidance, selected by the codegen toolchain registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.sdlc.csharp_names import is_namespace

if TYPE_CHECKING:
    from orchestrator.sdlc.layout import TargetLayout


def java_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Java package is `{layout.package_name}`. Put each public class at "
        f"`{layout.source_dir}/<ClassName>.java`, one public class per file, starting "
        f"with `package {layout.package_name};`.\n"
        f"- Put JUnit 5 tests at `{layout.tests_dir}/<ClassName>Test.java` in the same package.\n"
        "- Declare any new dependency in `pom.xml` (edit it); don't invent unrelated paths.\n\n"
    )


def kotlin_guidance(layout: TargetLayout) -> str:
    """Layout guidance for Kotlin/JVM codegen (P8, D13).

    Two things differ from Java and both bite a model that assumes Java's rules. Kotlin
    has **no one-public-class-per-file rule**, so the file is named for what it holds
    rather than for a single class; and the test dependency is ``kotlin("test")``, whose
    ``kotlin.test.Test`` maps onto whichever engine the build uses — so a generated test
    that reaches for ``org.junit.jupiter`` compiles against a dependency the scaffold
    never declared.
    """
    build_file = "build.gradle.kts" if layout.build_tool != "maven" else "pom.xml"
    if layout.module:
        # In a 28-module build the root script configures the build, not this module's
        # dependencies; editing it declares the dependency for nothing.
        build_file = f"{layout.module}/{build_file}"
    # Whichever library the repo's tests already use — a generated `import kotlin.test.Test`
    # does not compile in a project that depends on JUnit and nothing else, and the Spring
    # validation repo is exactly that. Greenfield has no existing tests, so the scaffold's
    # own `kotlin("test")` is the default.
    if layout.module and not layout.test_library:
        # The module has no test dependency and no tests to copy a convention from. Saying
        # "use kotlin.test" here produces `Unresolved reference: test`, so the dependency has
        # to be part of the instruction rather than an assumption.
        test_rule = (
            "using `kotlin.test` (`import kotlin.test.Test`, "
            "`import kotlin.test.assertEquals`) — and note that this module declares **no** "
            f'test dependency yet, so you must also add `testImplementation(kotlin("test"))` '
            f"to `{build_file}` or the test will not compile"
        )
    elif layout.test_library == "junit4":
        test_rule = (
            "using JUnit 4 (`import org.junit.Test`, `import org.junit.Assert`) — this module's "
            "tests are written against JUnit 4"
        )
    elif layout.test_library == "junit5":
        test_rule = (
            "using JUnit 5 (`import org.junit.jupiter.api.Test`, "
            "`import org.junit.jupiter.api.Assertions`) — this project depends on JUnit "
            "and NOT on `kotlin.test`, so a `kotlin.test` import will not compile"
        )
    else:
        test_rule = (
            "using `kotlin.test` (`import kotlin.test.Test`, "
            "`import kotlin.test.assertEquals`), which is what this project's tests are "
            "written against"
        )
    # P9, D13. Two rules an Android module needs that a Kotlin/JVM one does not, and both
    # decide whether the generated suite can run at all rather than merely where it sits.
    android_rules = ""
    if layout.android:
        android_rules = (
            f"- This is an Android module (`:{layout.module.replace('/', ':')}`). Tests here "
            "run on the JVM, with no emulator and no device.\n"
            "- Write a plain JVM unit test. Do NOT write an instrumented test: no "
            "`src/androidTest/`, no `@RunWith(AndroidJUnit4::class)`, no Espresso, no "
            "`androidx.test.*`. Those need a device and will not be run.\n"
            "- Keep the code under test free of Android framework types (`Context`, `Log`, "
            "`SharedPreferences`, …). In a unit test the framework is a stub whose methods "
            "return defaults, so logic that touches it is not actually verified. Put the "
            "logic in a plain class, ViewModel or repository and test that.\n"
            "- If the feature genuinely needs UI verification, say so as an explicit "
            "`// TODO: UI test` comment next to the code — do not quietly leave it untested.\n"
        )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        + android_rules
        + f"- Kotlin package is `{layout.package_name}`. Put new code at "
        f"`{layout.source_dir}/<Name>.kt`, starting with `package {layout.package_name}` "
        "(no trailing semicolon). A Kotlin file may hold several declarations and "
        "top-level functions; name the file after what it holds, not after one class.\n"
        f"- Put tests at `{layout.tests_dir}/<Name>Test.kt` in the same package, {test_rule}.\n"
        f"- Declare any new dependency in `{build_file}` (edit it); don't invent unrelated "
        "paths.\n\n"
    )


def typescript_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put new modules at `{layout.source_dir}/<name>.ts`. Use ES module "
        "`import`/`export`; import a sibling module by relative path with a `.js` "
        'extension (NodeNext), e.g. `import { x } from "./<name>.js"`.\n'
        f"- Put Vitest tests co-located beside the code as `{layout.tests_dir}/<name>.test.ts`.\n"
        "- Declare any new dependency in `package.json` (edit it); don't invent unrelated paths.\n\n"
    )


def csharp_guidance(layout: TargetLayout) -> str:
    """The C# layout block. The namespace is ``layout.namespace`` — read from the project —
    never ``package_name``, which for an existing repository is the `.csproj` file stem that
    selects the project (``commercial-secondary-sales``) and was handed to the model as the
    namespace on NSS-1243, three runs of three."""
    tfm = layout.target_framework or "net8.0"
    ns = layout.namespace or layout.package_name
    test_ns = layout.test_namespace or f"{ns}.Tests"
    if layout.mode != "existing":
        source = (
            f"- C# namespace is `{ns}`. Put each public type at "
            f"`{layout.source_dir}/<TypeName>.cs`, one public type per file, declaring "
            f"`namespace {ns};` (target {tfm}, nullable enabled).\n"
        )
    else:
        style = (
            "block `namespace X { ... }`"
            if layout.file_scoped_namespace is False
            else "file-scoped `namespace X;`"
        )
        evidence = f" ({layout.namespace_note})" if layout.namespace_note else ""
        source = (
            f"- Source project: `{layout.source_dir}/`. Its root namespace is `{ns}`{evidence}. "
            "Namespaces follow folders: a file at "
            f"`{layout.source_dir}/<Folder>/<Sub>/<TypeName>.cs` declares `namespace {ns}.<Folder>.<Sub>`, "
            f"in the {style} style this project uses (target {tfm}, nullable enabled).\n"
            "- Put a new type in the folder of the feature it belongs to (the files the design names "
            "show where), one public type per file named after the type; only a type with no clear "
            f"home goes at `{layout.source_dir}/<TypeName>.cs`. To use a type from another folder, "
            f"add `using {ns}.<Folder>;`.\n"
            "- A Razor component takes its namespace from its folder: never add `@namespace` to a "
            "`.razor` file. Its code-behind `<Name>.razor.cs` declares the same namespace as the folder.\n"
        )
        if layout.package_name and not is_namespace(layout.package_name):
            source += (
                f"- `{layout.package_name}` is the project's file name, not a namespace — never use "
                "it in a `namespace` or `using` line.\n"
            )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        + source
        + f"- Put xUnit tests at `{layout.tests_dir}/<TypeName>Tests.cs`, declaring `namespace {test_ns}` "
        f"and importing the code under test with `using {ns}.<Folder>;` for the folder it lives in "
        "(the test project already references the source project).\n"
        "- Declare any new dependency as a `<PackageReference>` in the source "
        "`.csproj` (edit it); don't invent unrelated paths.\n\n"
    )


def _sam_guidance(layout: TargetLayout) -> str:
    """CB-764: a SAM function is a directory Lambda runs from, not a package to scaffold."""
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- This is an AWS SAM repository. The change belongs in the Lambda function at "
        f"`{layout.source_dir}/` (a `CodeUri` in `template.yaml`): put new modules at "
        f"`{layout.source_dir}/<module>.py`, beside its handler.\n"
        f"- Lambda runs the function from `{layout.source_dir}/`, so modules inside it import each "
        "other by bare name (`from <module> import ...`). Tests import them the way this "
        "repository's existing tests already do — read one before writing yours.\n"
        f"- Put tests under `{layout.tests_dir}/` as `{layout.tests_dir}/test_<name>.py`.\n"
        f"- A dependency the function needs goes in `{layout.source_dir}/requirements.txt` "
        "(`boto3` is provided by the Lambda runtime).\n"
        "- Do NOT create a new package, a new top-level directory or a `pyproject.toml`.\n\n"
    )


def c_guidance(layout: TargetLayout) -> str:
    if layout.build_tool == "meson":
        build_line = (
            "- This project uses **Meson** (`meson.build`), which does NOT glob: "
            "register every new file — add new `.c` sources to the library/target "
            "source list, and add an `executable(...)` + `test(...)` for each new "
            f"`{layout.tests_dir}/test_<name>.c`. Edit `meson.build` to do so. "
            "Prefer extending existing files (no `meson.build` change needed) when you can."
        )
    else:
        build_line = (
            "- New `src/*.c` and `tests/*.c` are auto-discovered by CMake's glob; "
            "edit `CMakeLists.txt` only to add an external dependency."
        )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put implementation at `{layout.source_dir}/<name>.c` and DECLARE its "
        f"public functions in a header `{layout.source_dir}/<name>.h` (with an "
        "`#ifndef`/`#define` guard); C11, standard library only.\n"
        f"- Put tests at `{layout.tests_dir}/test_<name>.c` — each a standalone "
        "`int main(void)` returning non-zero on failure, `#include`-ing the "
        f"header from `{layout.source_dir}/`.\n"
        f"{build_line} Don't invent unrelated paths.\n\n"
    )


def cpp_guidance(layout: TargetLayout) -> str:
    meson = layout.build_tool == "meson"
    build_line = (
        "- Meson (`meson.build`) does NOT glob: register new `.cpp` sources + an "
        "`executable()`+`test()` per new `tests/*.cpp` in `meson.build`."
        if meson
        else "- New `src/*.cpp` and `tests/*.cpp` are auto-discovered by CMake's glob; "
        "edit `CMakeLists.txt` only to add a dependency."
    )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Declare classes/functions in `{layout.source_dir}/<name>.hpp` (include "
        f"guard or `#pragma once`) and define them in `{layout.source_dir}/<name>.cpp`; "
        "modern C++17, RAII, standard library only.\n"
        f"- Put tests at `{layout.tests_dir}/test_<name>.cpp` — each a standalone "
        "`int main()` returning non-zero on failure, `#include`-ing the header from "
        f"`{layout.source_dir}/`.\n"
        f"{build_line} Don't invent unrelated paths.\n\n"
    )


def php_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative):\n"
        f"- PHP {layout.mode} project, dependencies via {layout.build_tool}.\n"
        f"- Source directory: `{layout.source_dir}`; "
        f"namespace: `{layout.package_name or '(global; no namespace)'}`.\n"
        f"- Tests: `{layout.tests_dir}/<Name>{layout.test_suffix}`; class name matches filename.\n"
        f"- Bootstrap: `{layout.test_bootstrap or '(none; use require_once with __DIR__)'}`.\n"
        "- Preserve existing namespaces, require_once imports and file naming. "
        "Use strict_types only in greenfield. New files end in .php.\n\n"
    )


def go_guidance(layout: TargetLayout) -> str:
    pkg = layout.package_name.rstrip("/").rsplit("/", 1)[-1]
    loc = "the module root" if layout.source_dir == "." else f"`{layout.source_dir}/`"
    prefix = "" if layout.source_dir == "." else f"{layout.source_dir}/"
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Put new Go source at `{prefix}<name>.go` in {loc}; every file MUST start "
        f"with `package {pkg}` (match the other files already in that directory).\n"
        f"- Put tests co-located beside the code as `{prefix}<name>_test.go` "
        f"(same `package {pkg}`), using the standard `testing` package.\n"
        "- Declare any new dependency in `go.mod` (edit it); standard library only "
        "otherwise. Don't invent unrelated paths.\n\n"
    )


def sql_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Target SQL dialect: **{layout.build_tool}**. Write standard DDL for it.\n"
        f"- Put migration files under `{layout.source_dir}/` named with a zero-padded "
        f"order prefix, e.g. `{layout.source_dir}/001_<feature>.sql`; they apply in "
        "filename order (define referenced tables before they are referenced).\n"
        "- No application code, no test files, no build files — the migration is the "
        "artifact, validated by applying it to a database. Don't invent unrelated paths.\n\n"
    )


def perl_guidance(layout: TargetLayout) -> str:
    return (
        "PROJECT LAYOUT (authoritative):\n"
        f"- Perl package: `{layout.package_name}`. Sources belong under `{layout.source_dir}/`; "
        "map each package separator `::` to `/` and finish with `.pm`. Match neighboring package clauses.\n"
        f"- Tests belong in `{layout.tests_dir}/<name>.t`; follow existing numbering and use Test::More "
        "or the observed Test2::V0. Run the owning tests then the whole suite.\n"
        "- Use strict and warnings; use the observed Moo/Moose style or classic bless. "
        "Private subs start with `_`; public subs get POD. "
        "Preserve cpanfile/Makefile.PL/Build.PL/dist.ini.\n\n"
    )


def python_guidance(layout: TargetLayout) -> str:
    if layout.framework == "aws-sam":
        return _sam_guidance(layout)
    if layout.mode == "existing" and not layout.package_name:
        return (
            "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
            "- This repository keeps its modules at the top level, with no package. Put new "
            "modules at `<module>.py` in the repository root and import them as "
            "`from <module> import ...`.\n"
            f"- Put tests under `{layout.tests_dir}/` as `{layout.tests_dir}/test_<name>.py`.\n"
            "- Do NOT create a package directory, a `src/` tree or a new `pyproject.toml`.\n\n"
        )
    return (
        "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
        f"- Source package is `{layout.package_name}` under `{layout.source_dir}/`. "
        f"Put new modules at `{layout.source_dir}/<module>.py`.\n"
        f"- Import source as `from {layout.package_name}.<module> import ...` "
        "(the test runner puts the source root on the path).\n"
        f"- Put tests under `{layout.tests_dir}/` as `{layout.tests_dir}/test_<name>.py`.\n"
        f"- Put NEW code and tests under `{layout.source_dir}/` and `{layout.tests_dir}/` "
        "only, and do NOT invent unrelated top-level paths or parallel package trees.\n"
        # The ban used to be absolute — "do NOT create files outside src/ and tests/" —
        # which is right about invented paths and wrong about the repo's own docs. It
        # made every documentation criterion unsatisfiable: the judge required a
        # USER_GUIDE note, this line forbade touching USER_GUIDE.md, and the model
        # obeyed and said so ("outside the allowed src/tests paths so the doc note was
        # not added"). Editing a file the repo already has is not inventing a path.
        "- You MAY edit files that already exist elsewhere in the repo — README, "
        "USER_GUIDE, CHANGELOG, pyproject.toml — when the ticket calls for it. Changing "
        "an existing file is not inventing a path; creating a new top-level one is.\n\n"
    )
