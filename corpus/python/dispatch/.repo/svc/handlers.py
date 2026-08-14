"""Handlers invoked through a parameter rather than by name."""


class Handler:
    """Formats a payload."""

    def run(self, payload: dict) -> str:
        """Entry point — calls a sibling method through ``self``."""
        return self.format(payload)

    def format(self, payload: dict) -> str:
        """Render the payload."""
        return str(payload)


def dispatch(handler: Handler, payload: dict) -> str:
    """Call a method on a parameter whose type is annotated but not inferred."""
    return handler.run(payload)
