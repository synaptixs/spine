"""Multiple inheritance: Python's MRO decides, not the order bases happen to be scanned."""

from somelib import Widget


class Left:
    def ping(self) -> str:
        return "left"


class Right:
    def ping(self) -> str:
        return "right"


class Both(Left, Right):
    pass


class Wrapped(Widget):
    """An external base may define any member: only what this class declares is known."""

    def own(self) -> str:
        return "own"
