"""The shared target every other module imports."""


def helper() -> int:
    """Imported by a sibling and by a nested submodule."""
    return 1
