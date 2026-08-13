"""Sibling import — one dot, resolved against the parent package."""

from .util import helper


def use() -> int:
    """Calls across a single-dot relative import."""
    return helper()
