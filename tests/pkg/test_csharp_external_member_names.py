"""B26: a static call inside a class that derives from an external base class.

`Helper.Run()` inside `class Home : ControllerBase` was refused, because an external base
class might declare a member called `Helper` (member lookup finds it before any type), and the
pass cannot see inside the framework. On one .NET service that refused 91 true static calls, 81 of
them under `ControllerBase`. The committed table `EXTERNAL_MEMBER_NAMES` lists what ASP.NET's two
controller bases declare, read from the reference assemblies: a name absent from it is the type.

What must keep refusing: a type named like a member the base does declare (`User`, `File`, `Url`,
`ViewBag`), a base the table does not list, and a base written without the `using` that makes it
the framework's.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, RepoCodeExtractor
from orchestrator.pkg.typed_receivers import EXTERNAL_MEMBER_NAMES

pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")

HELPER = "namespace App;\npublic static class Helper { public static void Run() {} }\n"
MVC = "using Microsoft.AspNetCore.Mvc;\nnamespace App;\n"


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def test_a_static_call_in_a_controller_base_subclass_resolves(tmp_path: Path) -> None:
    files = {
        "Helper.cs": HELPER,
        "Home.cs": MVC + "public class Home : ControllerBase { public void Go() { Helper.Run(); } }\n",
    }
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") in _calls(tmp_path, files)


def test_it_resolves_through_an_in_repo_base_over_the_framework_base(tmp_path: Path) -> None:
    """The common shape: every controller derives from the project's own base class."""
    files = {
        "Helper.cs": HELPER,
        "Base.cs": MVC + "public class AppController : ControllerBase { }\n",
        "Home.cs": (
            "namespace App;\npublic class Home : AppController { public void Go() { Helper.Run(); } }\n"
        ),
    }
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") in _calls(tmp_path, files)


def test_it_resolves_for_the_view_controller_base(tmp_path: Path) -> None:
    files = {
        "Helper.cs": HELPER,
        "Home.cs": MVC + "public class Home : Controller { public void Go() { Helper.Run(); } }\n",
    }
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") in _calls(tmp_path, files)


@pytest.mark.parametrize(
    ("name", "why"),
    [
        pytest.param("User", "a property of ControllerBase", id="property"),
        pytest.param("File", "a method of ControllerBase: `File.Read()` binds the method group", id="method"),
        pytest.param("Equals", "a member every class inherits from object", id="object-member"),
    ],
)
def test_a_type_named_like_a_member_the_base_declares_still_refuses(
    tmp_path: Path, name: str, why: str
) -> None:
    files = {
        "T.cs": f"namespace App;\npublic static class {name} {{ public static void Read() {{}} }}\n",
        "Home.cs": MVC + f"public class Home : ControllerBase {{ public void Go() {{ {name}.Read(); }} }}\n",
    }
    assert ("csharp:App.Home.Go", f"csharp:App.{name}.Read") not in _calls(tmp_path, files), why


def test_a_name_only_the_view_controller_declares_refuses_there_and_resolves_under_the_plain_base(
    tmp_path: Path,
) -> None:
    t = "namespace App;\npublic static class ViewBag { public static void Read() {} }\n"
    plain = _calls(
        tmp_path / "a",
        {
            "T.cs": t,
            "H.cs": MVC + "public class H : ControllerBase { public void Go() { ViewBag.Read(); } }\n",
        },
    )
    view = _calls(
        tmp_path / "b",
        {"T.cs": t, "H.cs": MVC + "public class H : Controller { public void Go() { ViewBag.Read(); } }\n"},
    )
    assert ("csharp:App.H.Go", "csharp:App.ViewBag.Read") in plain
    assert ("csharp:App.H.Go", "csharp:App.ViewBag.Read") not in view


def test_a_base_the_table_does_not_list_still_refuses(tmp_path: Path) -> None:
    files = {
        "Helper.cs": HELPER,
        "Home.cs": (
            "using Lib.Unknown;\nnamespace App;\n"
            "public class Home : SomeLibBase { public void Go() { Helper.Run(); } }\n"
        ),
    }
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") not in _calls(tmp_path, files)


def test_the_framework_base_without_its_using_is_not_assumed_to_be_the_frameworks(tmp_path: Path) -> None:
    """`ControllerBase` written bare could be anyone's; only the namespace makes it Microsoft's."""
    files = {
        "Helper.cs": HELPER,
        "Home.cs": (
            "namespace App;\npublic class Home : ControllerBase { public void Go() { Helper.Run(); } }\n"
        ),
    }
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") not in _calls(tmp_path, files)


def test_a_field_the_repository_declares_still_wins_over_the_type(tmp_path: Path) -> None:
    files = {
        "Helper.cs": HELPER,
        "Home.cs": MVC
        + "public class Home : ControllerBase {\n  private Helper Helper;\n"
        + "  public void Go() { Helper.Run(); }\n}\n",
    }
    # `Helper` is the field here (a member wins over a type); its declared type is `Helper`
    assert ("csharp:App.Home.Go", "csharp:App.Helper.Run") in _calls(tmp_path, files)


def test_the_table_is_a_closed_superset_of_the_documented_members() -> None:
    """The names ASP.NET documents for each base are all in the table (guards typos and drift). Skips
    when the reference pack is not on the machine."""
    packs = sorted(
        glob.glob(str(Path("~/.nuget/packages/microsoft.aspnetcore.app.ref/*/ref/*").expanduser()))
    )
    if not packs:
        pytest.skip("the ASP.NET reference pack is not installed")
    docs = Path(packs[-1])
    for xml, type_name, key in (
        (
            "Microsoft.AspNetCore.Mvc.Core.xml",
            "ControllerBase",
            "csharp:Microsoft.AspNetCore.Mvc.ControllerBase",
        ),
        (
            "Microsoft.AspNetCore.Mvc.ViewFeatures.xml",
            "Controller",
            "csharp:Microsoft.AspNetCore.Mvc.Controller",
        ),
    ):
        text = (docs / xml).read_text(encoding="utf-8")
        documented = set(re.findall(rf'name="[PFME]:Microsoft\.AspNetCore\.Mvc\.{type_name}\.(\w+)', text))
        assert documented, f"no members found for {type_name} in {xml}"
        assert documented <= EXTERNAL_MEMBER_NAMES[key], sorted(documented - EXTERNAL_MEMBER_NAMES[key])


def test_the_view_base_includes_everything_the_plain_base_declares() -> None:
    plain = EXTERNAL_MEMBER_NAMES["csharp:Microsoft.AspNetCore.Mvc.ControllerBase"]
    view = EXTERNAL_MEMBER_NAMES["csharp:Microsoft.AspNetCore.Mvc.Controller"]
    assert plain < view
    assert {"ToString", "User", "Url"} <= plain
    assert {"ViewBag", "TempData", "ViewData", "View"} <= view - plain
