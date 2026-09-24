"""ASP.NET Core DI registrations become `PROVIDES` (B21, D6/D12).

Every consumer of a DI-bound service is handed the interface, so nothing calls the
implementation by name and `blast_radius` on it found nothing. The two-type registration is the
fact that connects them — and the only form read: a factory returns whatever its lambda builds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, RepoCodeExtractor

pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")

SERVICES = """\
namespace App.Services;
public interface IMailer { void Send(); }
public class SmtpMailer : IMailer { public void Send() {} }
public interface IRepo<T> { T Get(); }
public class UserRepo : IRepo<int> { public int Get() => 0; }
public class Worker { }
public interface IAudit { }
public class DbAudit : IAudit { }
"""


def _provides(tmp_path: Path, startup: str) -> set[tuple[str, str]]:
    (tmp_path / "Services.cs").write_text(SERVICES, encoding="utf-8")
    (tmp_path / "Program.cs").write_text(startup, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.PROVIDES}


def test_two_type_registrations_bind_the_implementation_to_the_interface(tmp_path: Path) -> None:
    got = _provides(
        tmp_path,
        "using App.Services;\nvar builder = WebApplication.CreateBuilder(args);\n"
        "builder.Services.AddScoped<IMailer, SmtpMailer>();\n"
        "builder.Services.TryAddSingleton<IRepo<int>, UserRepo>();\n",
    )
    assert got == {
        ("csharp:App.Services.SmtpMailer", "csharp:App.Services.IMailer"),
        ("csharp:App.Services.UserRepo", "csharp:App.Services.IRepo"),
    }


def test_single_type_factory_and_framework_registrations_bind_nothing(tmp_path: Path) -> None:
    got = _provides(
        tmp_path,
        "using App.Services;\nnamespace App;\npublic static class Startup {\n"
        "  public static void Register(IServiceCollection services) {\n"
        "    services.AddTransient<Worker>();\n"
        "    services.AddScoped<IAudit>(sp => new DbAudit());\n"
        "    services.AddSingleton<IHttpClientFactory, DefaultHttpClientFactory>();\n"
        "  }\n}\n",
    )
    assert got == set()
