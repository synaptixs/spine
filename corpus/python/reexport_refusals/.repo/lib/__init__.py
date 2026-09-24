"""Two bindings a static reader must not resolve: one that depends on which import succeeds,
and one a module-level `__getattr__` produces at run time."""

try:
    from .fast import compute
except ImportError:
    from .slow import compute


def __getattr__(name):
    if name == "Lazy":
        from .lazy import Lazy

        return Lazy
    raise AttributeError(name)
