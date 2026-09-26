"""The C# namespace is read from the project, never taken from its file name (NSS-1243).

Three runs of three on a Blazor repository whose project file is `commercial-secondary-sales.csproj`
wrote `using commercial-secondary-sales;` and `@namespace commercial-secondary-sales`: the guidance
told the model the file stem *was* the namespace. The project's `<RootNamespace>` said
`Commercial.Secondary.Sales`, and every existing file declared it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.sdlc.csharp_names import is_namespace, project_namespace, sanitize_namespace
from orchestrator.sdlc.language_guidance import csharp_guidance
from orchestrator.sdlc.layout import _resolve_csharp_layout


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _secondary_sales(root: Path, *, root_namespace: str | None = "Commercial.Secondary.Sales") -> None:
    """The NSS-1243 shape: a hyphen-named Blazor project, feature folders, one UnitTests project."""
    prop = f"<RootNamespace>{root_namespace}</RootNamespace>" if root_namespace else ""
    _write(
        root,
        "WebApp/commercial-secondary-sales.csproj",
        f'<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>{prop}</PropertyGroup></Project>',
    )
    _write(
        root,
        "WebApp/Features/Home/Ui/Home.razor.cs",
        "using System;\n\nnamespace Commercial.Secondary.Sales.Features.Home.Ui;\n\n"
        "public partial class Home {}\n",
    )
    _write(
        root,
        "WebApp/Shared/SSConstants.cs",
        "namespace Commercial.Secondary.Sales.Shared;\n\npublic static class SSConstants {}\n",
    )
    _write(root, "WebApp/Features/Home/Ui/Home.razor", "<h1>Home</h1>\n")
    _write(root, "UnitTests/UnitTests.csproj", '<Project Sdk="Microsoft.NET.Sdk"></Project>')
    _write(root, "UnitTests/Extensions/Fakes.cs", "namespace UnitTests.Extensions;\n\npublic class Fake {}\n")


def test_the_namespace_comes_from_root_namespace_not_the_file_name(tmp_path: Path) -> None:
    _secondary_sales(tmp_path)

    layout = _resolve_csharp_layout(tmp_path, mode="auto", package_name=None, repo=None)

    assert layout.package_name == "commercial-secondary-sales"  # still selects the project
    assert layout.namespace == "Commercial.Secondary.Sales"
    assert layout.test_namespace == "UnitTests"
    assert layout.namespace_note.startswith("from RootNamespace")
    assert layout.file_scoped_namespace is True


def test_the_guidance_never_offers_the_file_name_as_a_namespace(tmp_path: Path) -> None:
    _secondary_sales(tmp_path)
    guidance = csharp_guidance(_resolve_csharp_layout(tmp_path, mode="auto", package_name=None, repo=None))

    declared = re.findall(r"`(?:namespace|using|@namespace) ([^`;]+)", guidance)
    assert declared, guidance
    assert all(is_namespace(n.replace("<Folder>", "F").replace("<Sub>", "S")) for n in declared), declared
    assert "Commercial.Secondary.Sales" in guidance
    assert "never add `@namespace`" in guidance
    # The one mention of the stem is the warning not to use it.
    assert "`commercial-secondary-sales` is the project's file name, not a namespace" in guidance
    # And the example is a real file from the project.
    assert "WebApp/Features/Home/Ui/Home.razor.cs" in guidance


def test_without_root_namespace_the_files_own_declarations_decide(tmp_path: Path) -> None:
    _secondary_sales(tmp_path, root_namespace=None)

    found = project_namespace(tmp_path / "WebApp" / "commercial-secondary-sales.csproj", tmp_path)

    assert (found.root, found.source) == ("Commercial.Secondary.Sales", "declarations")
    assert "declares `namespace Commercial.Secondary.Sales." in found.example


def test_an_msbuild_property_reference_is_not_a_namespace(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "App/my-app.csproj",
        "<Project><PropertyGroup><RootNamespace>$(MSBuildProjectName)</RootNamespace>"
        "<AssemblyName>$(MSBuildProjectName)</AssemblyName></PropertyGroup></Project>",
    )

    found = project_namespace(tmp_path / "App" / "my-app.csproj", tmp_path)

    # Nothing to read, so the stem made legal the way the .NET templates do it.
    assert (found.root, found.source) == ("my_app", "project name")


def test_assembly_name_is_used_when_nothing_else_speaks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "App/my-app.csproj",
        "<Project><PropertyGroup><AssemblyName>Acme.App</AssemblyName></PropertyGroup></Project>",
    )

    assert project_namespace(tmp_path / "App" / "my-app.csproj", tmp_path).root == "Acme.App"


def test_block_namespaces_are_reported_as_block_style(tmp_path: Path) -> None:
    _write(tmp_path, "Lib/Lib.csproj", "<Project></Project>")
    _write(tmp_path, "Lib/Things/A.cs", "namespace Lib.Things\n{\n    public class A {}\n}\n")

    layout = _resolve_csharp_layout(tmp_path, mode="auto", package_name=None, repo=None)

    assert layout.file_scoped_namespace is False
    assert "block `namespace X { ... }`" in csharp_guidance(layout)


def test_a_greenfield_package_name_with_a_hyphen_still_gets_a_legal_namespace(tmp_path: Path) -> None:
    from orchestrator.sdlc.scaffold import _csharp_files

    layout = _resolve_csharp_layout(tmp_path, mode="new", package_name="my-app", repo=None)

    assert layout.namespace == "MyApp"
    csprojs = [text for path, text in _csharp_files(layout).items() if path.endswith(".csproj")]
    assert any("<RootNamespace>MyApp</RootNamespace>" in text for text in csprojs)


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Commercial.Secondary.Sales", True),
        ("commercial_secondary_sales", True),
        ("@class.Foo", True),
        ("commercial-secondary-sales", False),
        ("Sales.", False),
        (".Ui", False),
        ("1Sales", False),
        ("", False),
    ],
)
def test_is_namespace(name: str, ok: bool) -> None:
    assert is_namespace(name) is ok


def test_sanitize_namespace_matches_the_dotnet_templates() -> None:
    assert sanitize_namespace("commercial-secondary-sales") == "commercial_secondary_sales"
    assert sanitize_namespace("9lives.core") == "_9lives.core"
    assert sanitize_namespace("") == "App"


# --- P3: the pre-write directive check -------------------------------------------------------
#
# The three defects NSS-1243's runs wrote, each caught before `dotnet test` ever runs — and the
# legal directive shapes a real .NET repository carries, none of which may be flagged.

_IMPORTS = "@using System.Net.Http\n@using Commercial.Secondary.Sales\n"


@pytest.mark.parametrize(
    ("rel", "source", "needle"),
    [
        # Run A: an `_Imports.razor` edit that left a bare `.Ui` where an @using was.
        ("WebApp/_Imports.razor", _IMPORTS + ".Ui\n@using Nucor.Iron.UI\n", "`.Ui` is not a Razor directive"),
        # Run B: the file stem as a Razor namespace.
        (
            "WebApp/Shared/Components/OilStatus.razor",
            "@namespace commercial-secondary-sales\n<p/>\n",
            "line 1",
        ),
        # Run C: the file stem as a using target.
        (
            "UnitTests/OilQuantityDisplayHelperTests.cs",
            "using Xunit;\nusing commercial-secondary-sales;\n",
            "line 2",
        ),
        ("A.cs", "namespace commercial-secondary-sales;\n", "namespace declaration"),
        ("A.razor", "@using commercial-secondary-sales\n", "@using directive"),
    ],
)
def test_the_field_defects_are_rejected(rel: str, source: str, needle: str) -> None:
    from orchestrator.sdlc.csharp_names import directive_error

    error = directive_error(rel, source)
    assert rel in error and needle in error


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        ("A.cs", "using System;\nusing static System.Math;\nglobal using global::System.Linq;\n"),
        ("A.cs", "using Map = System.Collections.Generic.Dictionary<string, List<int>>;\n"),
        ("A.cs", "using Point = (int X, int Y);\n"),  # C# 12 tuple alias: not a namespace at all
        ("A.cs", "namespace Commercial.Secondary.Sales.Shared\n{\n}\n"),
        (
            "A.cs",
            "class C {\n    void M() {\n        using var s = new MemoryStream();\n"
            "        using (var t = s) {}\n    }\n}\n",
        ),
        ("A.cs", "namespace commercial_secondary_sales;\n"),  # legal, if wrong — P2's job, not this check's
        (
            "WebApp/_Imports.razor",
            _IMPORTS + "@inject NavigationManager Nav\n@* a\n comment *@\n<!-- note -->\n\n",
        ),
        (
            "A.razor",
            '@page "/home"\n@using Commercial.Secondary.Sales.Shared\n<h1>Hi</h1>\n.not-a-directive-here\n',
        ),
        ("a.py", "using commercial-secondary-sales;\n"),  # not C#
    ],
)
def test_legal_directives_pass(rel: str, source: str) -> None:
    from orchestrator.sdlc.csharp_names import directive_error

    assert directive_error(rel, source) == ""


async def test_an_illegal_using_is_never_written_and_the_retry_says_why(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    with pytest.raises(CodegenError) as caught:
        apply_files(
            [
                {
                    "path": "UnitTests/HelperTests.cs",
                    "content": "using Xunit;\nusing commercial-secondary-sales;\n",
                }
            ],
            tmp_path,
            written_tracker={},
            grounded=True,
        )

    assert not (tmp_path / "UnitTests" / "HelperTests.cs").exists()
    assert any("commercial-secondary-sales" in m for m in caught.value.syntax_errors)


async def test_an_edit_that_breaks_imports_razor_is_refused(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    imports = tmp_path / "WebApp" / "_Imports.razor"
    imports.parent.mkdir(parents=True)
    original = _IMPORTS + "@using Commercial.Secondary.Sales.Features.Home.Ui\n"
    imports.write_text(original, encoding="utf-8")

    with pytest.raises(CodegenError):
        apply_files(
            [
                {
                    "path": "WebApp/_Imports.razor",
                    "edits": [
                        {"find": "@using Commercial.Secondary.Sales.Features.Home.Ui", "replace": ".Ui"}
                    ],
                }
            ],
            tmp_path,
            written_tracker={},
            grounded=True,
        )

    assert imports.read_text(encoding="utf-8") == original
