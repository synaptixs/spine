"""The secrets seam — one place the service asks for a credential. Phase 2 of
``docs/specs/secrets-vault-and-identity.md``.

**The default implementation is today's behaviour, exactly.** ``get_secret("ORCHESTRATOR_API_KEY")``
reads the environment variable of that name, which is what every call site did before this module
existed. A vault is a *second* provider, selected by ``ORCHESTRATOR_SECRETS_PROVIDER``; nobody who
does not set it ever learns this seam is here. That is the same two-mode shape as
``resolve_principal_from_key``: no ``principals`` map → the single key → today's behaviour.

**This module imports no vault client, and must not.** A provider that needs one registers itself
from behind an extra (``pip install synaptixs-spine[vault]``), the way every language parser
already does. Otherwise "optional" still costs every developer a dependency and an import — and
the read-only path, which phase 1 pins to an empty environment, would start needing one.

Names are the **environment-variable names** — ``OBJECT_STORE_SECRET_ACCESS_KEY``, not
``object_store.secret`` — so a vault path mirrors the variable an operator already knows, and the
same name means the same thing whichever provider answers.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

#: Selects the provider. Unset or ``env`` is the default and is today's behaviour.
PROVIDER_ENV = "ORCHESTRATOR_SECRETS_PROVIDER"


class SecretProvider(Protocol):
    """Answer ``get(name)`` with the secret's value, or ``None`` when it is not held."""

    def get(self, name: str) -> str | None: ...


class EnvSecretProvider:
    """``os.environ`` — the provider every call site was using implicitly."""

    def get(self, name: str) -> str | None:
        return os.environ.get(name)


class SecretProviderError(RuntimeError):
    """A provider was named that nothing registered — misconfiguration, not a missing secret."""


_REGISTRY: dict[str, Callable[[], SecretProvider]] = {"env": EnvSecretProvider}


def register_provider(name: str, factory: Callable[[], SecretProvider]) -> None:
    """How a provider behind an extra makes itself selectable. Idempotent by name."""
    _REGISTRY[name] = factory


def secret_provider() -> SecretProvider:
    """The provider ``ORCHESTRATOR_SECRETS_PROVIDER`` selects; ``env`` when unset.

    Built per call rather than cached: the choice is a process-level setting, and a cached
    instance is one more thing a test has to know how to reset.
    """
    name = (os.environ.get(PROVIDER_ENV) or "env").strip().lower()
    factory = _REGISTRY.get(name)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY))
        raise SecretProviderError(
            f"{PROVIDER_ENV}={name!r} names no registered secrets provider (known: {known}). "
            "A vault provider lives behind an extra — is it installed?"
        )
    return factory()


def get_secret(name: str, default: str | None = None) -> str | None:
    """The credential ``name``, from whichever provider is configured, else ``default``.

    Under the default provider this is ``os.environ.get(name, default)`` — and it is written so
    that it *cannot* be anything else there, because that is the promise phase 2 makes.
    """
    value = secret_provider().get(name)
    return default if value is None else value


__all__ = [
    "PROVIDER_ENV",
    "EnvSecretProvider",
    "SecretProvider",
    "SecretProviderError",
    "get_secret",
    "register_provider",
    "secret_provider",
]
