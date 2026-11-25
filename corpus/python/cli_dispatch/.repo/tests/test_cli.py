"""One test runs a command by its literal argv; one only asks for help."""

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_add_user() -> None:
    """Runs `add-user`: the framework calls `add_user`, nothing in Python does."""
    assert runner.invoke(app, ["add-user", "Ada"]).exit_code == 0


def test_remove_help() -> None:
    """`--help` stops before the handler, so this test does not run `remove_user`."""
    assert runner.invoke(app, ["users", "remove", "--help"]).exit_code == 0
