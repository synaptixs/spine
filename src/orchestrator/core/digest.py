"""One canonical serialisation, and the digest taken over it.

**Why this is its own module.** `runtime.tool_registry` owns the tool registry and must import
`sdlc.evidence` to register the SDLC's tools. `sdlc.evidence` needs the digest. Keeping the
digest in `tool_registry` meant `evidence` importing it back — a cycle, which CodeQL flagged on
the 3.20.0 promotion.

Making one side's import lazy did not fix it, and that is the lesson worth recording: **two lazy
imports are still a cycle.** The first attempt moved which side deferred and left the loop
standing, because the cycle is a property of the dependency graph rather than of import timing.
The only fix is for one direction to stop existing, so the shared thing moved to a module that
depends on neither.

`core/` is the right home on its own merits: this is a serialisation rule, not orchestration.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

__all__ = ["SupportsRegister", "canonical_json", "digest_of"]


class SupportsRegister(Protocol):
    """What a registration function needs of a registry — nothing more.

    Typed structurally so `sdlc.evidence` can annotate `register_sdlc_tools` without importing
    `runtime.tool_registry`, which is the import that made the cycle in the first place.
    """

    def register(self, name: str, fn: Any) -> None: ...


def canonical_json(value: Any) -> str:
    """The one serialisation a digest is taken over.

    ``sort_keys`` is load-bearing: Python dicts preserve insertion order, so two runs that
    computed identical facts in a different order would otherwise digest differently and read as
    a divergence. Same reason the ``state`` area sort had to become total in 3.19.0.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
