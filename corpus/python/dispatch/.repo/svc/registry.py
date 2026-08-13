"""Dispatch through a lookup table — the class is never named at the call site."""

from svc.handlers import Handler

TABLE = {"default": Handler}


def build(name: str) -> Handler:
    """Instantiate via a table lookup."""
    cls = TABLE[name]
    return cls()


def invoke(name: str, payload: dict) -> str:
    """Call a method on the return value of another call."""
    return build(name).run(payload)
