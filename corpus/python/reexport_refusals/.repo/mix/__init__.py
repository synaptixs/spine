"""Three more bindings a static reader must not follow: a re-export that a later star from
outside the tree may replace, one rebound by assignment, and — through `import mix.store` in
the caller — a name that belongs to this package, not to the submodule the caller imported."""

from .fast import rate, speed
from numpy import *


def _tuned(f):
    return f


rate = _tuned(rate)


def start():
    return 0
