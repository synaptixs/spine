"""The class most callers reach through the package, never by its own path."""


class Store:
    """Re-exported by the package's `__init__`."""

    def get(self) -> int:
        return 1
