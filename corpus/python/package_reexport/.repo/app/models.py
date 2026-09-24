"""Star-exported, with a literal `__all__` that names only one of its classes."""

__all__ = ["Order"]


class Order:
    """In `__all__`, so the package's `from .models import *` binds it."""


class Draft:
    """Public, but not in `__all__`: the star import does not bind it."""
