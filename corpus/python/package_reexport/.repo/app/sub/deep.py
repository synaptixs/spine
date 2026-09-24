"""Where the chained re-export ends."""


class Engine:
    """Reached as `app.Engine`, through `app.sub`."""

    def start(self) -> int:
        return 5
