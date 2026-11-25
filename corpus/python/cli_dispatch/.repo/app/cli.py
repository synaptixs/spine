"""A typer app with a mounted sub-app — the command half of the EXPOSES join (B130)."""

from typer import Typer

app = Typer()
users = Typer()


def normalise(name: str) -> str:
    """Called by both handlers; nothing in Python calls the handlers but the framework."""
    return name.strip().lower()


@app.command()
def add_user(name: str) -> None:
    """No name given: typer names it `add-user` (lower-cased, `_` -> `-`)."""
    print(normalise(name))


@users.command("remove")
def remove_user(name: str) -> None:
    """Named `remove`, reached as `users remove` through the mount below."""
    print(normalise(name))


app.add_typer(users, name="users")
