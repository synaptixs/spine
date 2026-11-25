"""typer / click commands → ``Endpoint`` (``CLI <path>``) + ``EXPOSES``; a test running
``X.invoke(app, [literal argv])`` → ``CONSUMES`` to the one command it names (B130).

Pinned here: the frameworks' own naming defaults (D19), that a computed name yields no node,
and the argv rule (D20) — an edge only where the literal prefix names a command, never for
``--help``, an option first, a group alone or a computed argv. A wrong edge would label a
command as reached that no test runs, which is the gap this exists to stop hiding.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, Node, NodeKind


def _extract(tmp_path: Path, files: dict[str, str]) -> tuple[list[Node], list[Edge], list[Edge]]:
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    endpoints = [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]
    exposes = [e for e in batch.edges if e.kind is EdgeKind.EXPOSES]
    consumes = [e for e in batch.edges if e.kind is EdgeKind.CONSUMES]
    return endpoints, exposes, consumes


def _names(endpoints: list[Node]) -> set[str]:
    return {n.name for n in endpoints}


def _exposed(exposes: list[Edge]) -> dict[str, str]:
    return {e.src.removeprefix("py:endpoint:"): e.dst for e in exposes}


def _consumed(consumes: list[Edge]) -> set[tuple[str, str]]:
    return {(e.src, e.dst.removeprefix("py:endpoint:")) for e in consumes}


_SUB = (
    "import typer\n\n"
    "sdlc_app = typer.Typer()\n\n\n"
    "@sdlc_app.command()\n"
    "def plan_ticket(source: str) -> None:\n    pass\n\n\n"
    '@sdlc_app.command("run")\n'
    "def _run() -> None:\n    pass\n\n\n"
    '@sdlc_app.command(name="status", hidden=True)\n'
    "def show_status() -> None:\n    pass\n"
)
_ROOT = "import typer\n\napp = typer.Typer()\n\n\n@app.command()\ndef understand() -> None:\n    pass\n"
# The package __init__ mounts sub-apps on the app another module defines and re-exports it,
# which is Spine's own shape (``cli/_app.py`` + ``cli/__init__.py``).
_INIT = 'from cli._app import app\nfrom cli import sdlc\n\napp.add_typer(sdlc.sdlc_app, name="sdlc")\n'


def _typer_repo(test_source: str) -> dict[str, str]:
    return {
        "cli/_app.py": _ROOT,
        "cli/sdlc.py": _SUB,
        "cli/__init__.py": _INIT,
        "tests/test_cli.py": test_source,
    }


# ---- command facts -----------------------------------------------------------


def test_typer_commands_take_the_frameworks_names_across_modules(tmp_path: Path) -> None:
    endpoints, exposes, _ = _extract(tmp_path, _typer_repo("x = 1\n"))
    assert _names(endpoints) == {
        "CLI understand",
        "CLI sdlc plan-ticket",  # default: function name, `_` → `-`
        "CLI sdlc run",  # positional name
        "CLI sdlc status",  # name= keyword; hidden commands are still commands
    }
    assert _exposed(exposes) == {
        "CLI understand": "py:cli._app.understand",
        "CLI sdlc plan-ticket": "py:cli.sdlc.plan_ticket",
        "CLI sdlc run": "py:cli.sdlc._run",
        "CLI sdlc status": "py:cli.sdlc.show_status",
    }


def test_a_mounted_sub_app_is_not_also_emitted_at_its_own_root(tmp_path: Path) -> None:
    endpoints, _, _ = _extract(tmp_path, _typer_repo("x = 1\n"))
    assert "CLI plan-ticket" not in _names(endpoints)


def test_unnamed_add_typer_uses_typer_name_then_callback_then_merges(tmp_path: Path) -> None:
    endpoints, _, _ = _extract(
        tmp_path,
        {
            "app.py": (
                "import typer\n\n"
                'named = typer.Typer(name="db")\n'
                "with_cb = typer.Typer()\n"
                "plain = typer.Typer()\n"
                "app = typer.Typer()\n\n\n"
                "@named.command()\ndef migrate() -> None:\n    pass\n\n\n"
                "@with_cb.callback()\ndef cache_tools() -> None:\n    pass\n\n\n"
                "@with_cb.command()\ndef clear() -> None:\n    pass\n\n\n"
                "@plain.command()\ndef version() -> None:\n    pass\n\n\n"
                "@app.command()\ndef serve() -> None:\n    pass\n\n\n"
                "app.add_typer(named)\napp.add_typer(with_cb)\napp.add_typer(plain)\n"
            ),
        },
    )
    assert _names(endpoints) == {"CLI serve", "CLI db migrate", "CLI cache-tools clear", "CLI version"}


def test_click_groups_commands_and_add_command(tmp_path: Path) -> None:
    endpoints, exposes, _ = _extract(
        tmp_path,
        {
            "tool.py": (
                "import click\n\n\n"
                "@click.group()\ndef cli() -> None:\n    pass\n\n\n"
                "@cli.command()\ndef init_db() -> None:\n    pass\n\n\n"
                "@cli.command()\ndef export_cmd() -> None:\n    pass\n\n\n"  # click 8.2 strips `_cmd`
                "@cli.group()\ndef users_group() -> None:\n    pass\n\n\n"  # … and `_group`
                '@users_group.command("add")\ndef add_user() -> None:\n    pass\n\n\n'
                "@click.command()\ndef standalone() -> None:\n    pass\n\n\n"
                'cli.add_command(standalone, name="solo")\n'
            ),
        },
    )
    assert _names(endpoints) == {"CLI init-db", "CLI export", "CLI users add", "CLI solo"}
    assert _exposed(exposes)["CLI users add"] == "py:tool.add_user"


def test_a_computed_name_yields_no_node(tmp_path: Path) -> None:
    endpoints, _, _ = _extract(
        tmp_path,
        {
            "app.py": (
                "import typer\n\n"
                'NAME = "dyn"\n'
                "app = typer.Typer()\n"
                "sub = typer.Typer()\n\n\n"
                "@app.command(NAME)\ndef dynamic() -> None:\n    pass\n\n\n"
                "@app.command()\ndef fixed() -> None:\n    pass\n\n\n"
                "@sub.command()\ndef inner() -> None:\n    pass\n\n\n"
                "app.add_typer(sub, name=NAME)\n"
            ),
        },
    )
    # Neither the computed command nor anything under the computed mount — and the sub-app is
    # not promoted to a root of its own, which would put `inner` at a path that does not exist.
    assert _names(endpoints) == {"CLI fixed"}


def test_no_cli_framework_means_no_cli_endpoints(tmp_path: Path) -> None:
    endpoints, _, consumes = _extract(
        tmp_path,
        {
            "chain.py": (
                "class Chain:\n    def command(self):\n        pass\n\n\n"
                "def run(c):\n    c.invoke(c, ['x'])\n"
            )
        },
    )
    assert endpoints == [] and consumes == []


# ---- invoke → CONSUMES (D15, D20, D21) ---------------------------------------


def test_a_literal_argv_consumes_the_one_command_it_names(tmp_path: Path) -> None:
    _, _, consumes = _extract(
        tmp_path,
        _typer_repo(
            "from typer.testing import CliRunner\n\n"
            "from cli import app\n\n"
            "runner = CliRunner()\n\n\n"
            "def test_plan() -> None:\n"
            '    runner.invoke(app, ["sdlc", "plan-ticket", "file://x", "--json"])\n\n\n'
            "class TestTop:\n"
            "    def test_understand(self) -> None:\n"
            '        CliRunner().invoke(app, ["understand", "."])\n'
        ),
    )
    assert _consumed(consumes) == {
        ("py:tests.test_cli.test_plan", "CLI sdlc plan-ticket"),
        ("py:tests.test_cli.TestTop.test_understand", "CLI understand"),
    }


def test_argv_that_cannot_run_a_named_handler_gets_no_edge(tmp_path: Path) -> None:
    _, _, consumes = _extract(
        tmp_path,
        _typer_repo(
            "from typer.testing import CliRunner\n\n"
            "from cli import app\n\n"
            "runner = CliRunner()\n\n\n"
            'def test_help() -> None:\n    runner.invoke(app, ["sdlc", "run", "--help"])\n\n\n'
            'def test_short_help() -> None:\n    runner.invoke(app, ["sdlc", "-h"])\n\n\n'
            'def test_group_only() -> None:\n    runner.invoke(app, ["sdlc"])\n\n\n'
            'def test_option_first() -> None:\n    runner.invoke(app, ["--verbose", "understand"])\n\n\n'
            "def test_computed(args: list[str]) -> None:\n    runner.invoke(app, args)\n\n\n"
            'def test_unknown() -> None:\n    runner.invoke(app, ["sdlc", "nope"])\n\n\n'
            'def test_computed_first(cmd: str) -> None:\n    runner.invoke(app, [cmd, "run"])\n'
        ),
    )
    assert consumes == []


def test_a_helper_that_invokes_is_the_edge_source(tmp_path: Path) -> None:
    _, _, consumes = _extract(
        tmp_path,
        _typer_repo(
            "from typer.testing import CliRunner\n\n"
            "from cli import app\n\n\n"
            "def _run_status():\n"
            '    return CliRunner().invoke(app, args=["sdlc", "status"])\n\n\n'
            "def test_status() -> None:\n    _run_status()\n"
        ),
    )
    assert _consumed(consumes) == {("py:tests.test_cli._run_status", "CLI sdlc status")}


def test_invoking_a_sub_app_directly_reaches_its_mounted_command(tmp_path: Path) -> None:
    _, _, consumes = _extract(
        tmp_path,
        _typer_repo(
            "from typer.testing import CliRunner\n\n"
            "from cli.sdlc import sdlc_app\n\n\n"
            'def test_run() -> None:\n    CliRunner().invoke(sdlc_app, ["run"])\n'
        ),
    )
    assert _consumed(consumes) == {("py:tests.test_cli.test_run", "CLI sdlc run")}


def test_a_single_command_app_runs_without_naming_it(tmp_path: Path) -> None:
    """Typer runs a lone command (no callback, no sub-app) directly; click runs a standalone
    command directly. Either way the argv carries no command name, so options first is fine."""
    endpoints, _, consumes = _extract(
        tmp_path,
        {
            "one.py": (
                "import typer\n\napp = typer.Typer()\n\n\n"
                "@app.command()\ndef main(n: int = 1) -> None:\n    pass\n"
            ),
            "hello.py": "import click\n\n\n@click.command()\ndef hello_cmd() -> None:\n    pass\n",
            "tests/test_one.py": (
                "from click.testing import CliRunner\n\n"
                "from one import app\nfrom hello import hello_cmd\n\n\n"
                'def test_main() -> None:\n    CliRunner().invoke(app, ["--n", "2"])\n\n\n'
                "def test_hello() -> None:\n    CliRunner().invoke(hello_cmd, [])\n\n\n"
                'def test_hello_help() -> None:\n    CliRunner().invoke(hello_cmd, ["--help"])\n'
            ),
        },
    )
    assert _names(endpoints) == {"CLI main", "CLI hello"}
    assert _consumed(consumes) == {
        ("py:tests.test_one.test_main", "CLI main"),
        ("py:tests.test_one.test_hello", "CLI hello"),
    }


def test_a_cli_endpoint_never_joins_an_http_call(tmp_path: Path) -> None:
    """``python_client`` joins on ``(verb, path)``: a ``CLI`` verb can never equal an HTTP one."""
    _, _, consumes = _extract(
        tmp_path,
        {
            "cli.py": (
                "import typer\n\napp = typer.Typer()\n\n\n"
                "@app.command()\ndef runs() -> None:\n    pass\n\n\n"
                "@app.command()\ndef other() -> None:\n    pass\n"
            ),
            "client.py": 'import httpx\n\n\ndef fetch():\n    return httpx.get("runs")\n',
        },
    )
    assert consumes == []
