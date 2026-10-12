"""The .NET runner against a real `dotnet`, where there is one (B73).

Everything else about the .NET baseline is tested on recorded output, because CI has no .NET SDK.
These two run the real compiler and the real NuGet client on a project small enough to need no
package from the network, so a change in what `dotnet` prints is caught on a machine that has it.
They are skipped where `dotnet` is absent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.sdlc.testrunner import DotnetTestRunner

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK on PATH")


def _tfm() -> str:
    version = subprocess.run(["dotnet", "--version"], capture_output=True, text=True, check=False).stdout
    match = re.match(r"(\d+)\.", version.strip())
    return f"net{match.group(1) if match else '8'}.0"


def _project(root: Path, body: str, program: str, *, nuget_config: str = "") -> Path:
    # IsTestProject: without it `dotnet test` restores a console project and builds nothing.
    (root / "Acme.Widgets.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        f"<IsTestProject>true</IsTestProject><TargetFramework>{_tfm()}</TargetFramework></PropertyGroup>"
        f"{body}</Project>\n",
        encoding="utf-8",
    )
    (root / "Program.cs").write_text(program, encoding="utf-8")
    if nuget_config:
        (root / "nuget.config").write_text(nuget_config, encoding="utf-8")
    return root


async def test_a_real_compile_error_is_a_build_failure_with_a_position(tmp_path: Path) -> None:
    root = _project(tmp_path, "", 'int x = "not an int";\n')

    result = await DotnetTestRunner().run(path=str(root))

    assert not result.passed and result.failure == "build"
    assert result.problems == ()
    assert result.output.startswith("COMPILER ERRORS")
    assert "Program.cs(1,9): error CS0029" in result.output  # relative, with its position


async def test_a_real_unreachable_feed_is_a_feed_failure_not_a_build_error(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        '<ItemGroup><PackageReference Include="Acme.Does.Not.Exist" Version="1.0.0" /></ItemGroup>',
        "System.Console.WriteLine(1);\n",
        nuget_config=(
            '<?xml version="1.0" encoding="utf-8"?><configuration><packageSources><clear />'
            '<add key="dead" value="http://127.0.0.1:9/v3/index.json" /></packageSources></configuration>'
        ),
    )

    result = await DotnetTestRunner().run(path=str(root))

    assert not result.passed and result.failure == "feed"
    assert "error NU130" in result.output  # NU1302 here: NuGet refuses an HTTP source


async def test_a_real_directory_with_nothing_to_build_is_a_project_failure(tmp_path: Path) -> None:
    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert not result.passed and result.failure == "project"
    assert "MSB1003" in result.output
