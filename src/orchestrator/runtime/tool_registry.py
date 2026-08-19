"""In-process registry of the deterministic tools a GraphIR ``tool`` node can name.

**Why in-process rather than the registry database.** Agent nodes resolve their
``template_id`` against ``AgentTemplateRow`` and therefore need an ``AsyncSession``; the IR
validator skips that check entirely when no session is supplied. ``sdlc autorun`` runs with no
registry service — on a laptop, in a CI job, against a cloned worktree — so a database-backed
tool lookup would leave the SDLC's own graph unvalidatable in exactly the context that runs it.
A tool is code in this process; the honest place to resolve it is this process.

**What a tool promises.** Same ``(commit, inputs)`` → same bytes out. No model call, no network,
no clock read, no RNG draw. Every result is digested, so "deterministic" is a property that can
be checked rather than a claim in a docstring — which is the whole point of the class existing
(see ``docs/specs/graphir-sdlc-workflow.md``, "The determinism boundary").

The digest covers the canonical JSON form of the value: sorted keys, no whitespace, so two runs
that agree on content agree on bytes regardless of dict insertion order.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ToolError",
    "ToolResult",
    "ToolRegistry",
    "canonical_json",
    "digest_of",
    "default_registry",
]


class ToolError(RuntimeError):
    """A tool was named that is not registered, or one raised while running."""


def canonical_json(value: Any) -> str:
    """The one serialisation a digest is taken over.

    ``sort_keys`` is load-bearing: Python dicts preserve insertion order, so two runs that
    computed identical facts in a different order would otherwise digest differently and read
    as a divergence. Same reason the ``state`` area sort had to become total in 3.19.0.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolResult:
    """What a tool node produced, and the digest that makes it comparable."""

    name: str
    value: Any
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "digest": self.digest, "value": self.value}


class ToolRegistry:
    """Name → deterministic callable.

    Callables may be sync or async; ``run`` awaits whatever is awaitable. Registration is
    idempotent for the same function object so that importing a registration module twice —
    which happens whenever a lazy import races a direct one — is not an error, while a genuine
    redefinition still is.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        existing = self._tools.get(name)
        if existing is not None and existing is not fn:
            raise ToolError(f"tool {name!r} is already registered to a different callable")
        self._tools[name] = fn

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def get(self, name: str) -> Callable[..., Any]:
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(self.names()) or "none registered"
            raise ToolError(f"unknown tool {name!r}; registered: {known}") from None

    async def run(self, name: str, /, **kwargs: Any) -> ToolResult:
        """Run a tool and digest its output.

        The tool returns a JSON-serialisable value; anything else is a programming error here
        rather than a runtime condition to handle, because a value that cannot be serialised
        cannot be digested and therefore cannot be compared.
        """
        fn = self.get(name)
        out = fn(**kwargs)
        if inspect.isawaitable(out):
            out = await out
        return ToolResult(name=name, value=out, digest=digest_of(out))


_DEFAULT = ToolRegistry()
_DEFAULTS_LOADED = False


def default_registry() -> ToolRegistry:
    """The process-wide registry, with the SDLC tools registered on first use.

    Registration is lazy and imported here rather than at module import: ``runtime`` must not
    depend on ``sdlc`` at import time, and the validator needs the names resolvable without the
    caller having remembered to import anything.
    """
    global _DEFAULTS_LOADED
    if not _DEFAULTS_LOADED:
        # Set first: the import below registers into ``_DEFAULT`` and may itself reach back
        # here, and a re-entrant call must not start the import a second time.
        _DEFAULTS_LOADED = True
        from orchestrator.sdlc.evidence import register_sdlc_tools

        register_sdlc_tools(_DEFAULT)
    return _DEFAULT
