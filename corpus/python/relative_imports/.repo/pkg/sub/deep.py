"""Climbing import — two dots, out of the nested package and back down."""

from ..util import helper


def reach() -> int:
    """Calls across a climbing relative import."""
    return helper()
