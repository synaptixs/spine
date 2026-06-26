"""Resolve per-tool credentials at invocation time.

Slim version: ``none`` and ``api_key`` only. For api_key tools the
credential is read from an environment variable named after the tool id::

    tool.web_search@0.2.1 -> TOOL_TOOL__WEB_SEARCH_0_2_1_API_KEY

That convention is verbose on purpose: a tool can specify
``authentication.secret_ref`` in its ToolContract to override the lookup
key with something cleaner once a real secret store is wired up.

OAuth2 and mTLS are deferred until a tool actually needs them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from orchestrator.registry.tool_contract import AuthType


class CredentialError(RuntimeError):
    """Raised when a required credential can't be resolved."""


@dataclass(frozen=True)
class ResolvedCredentials:
    """Credentials handed to a handler for one invocation."""

    headers: dict[str, str]
    secrets: dict[str, str]


def _env_key_for(contract_id: str, version: str) -> str:
    safe = contract_id.replace(".", "__").replace("-", "_")
    return f"TOOL_{safe.upper()}_{version.replace('.', '_').replace('-', '_').upper()}_API_KEY"


def resolve_credentials(
    *, contract_id: str, version: str, auth_spec: dict[str, str] | None
) -> ResolvedCredentials:
    auth_spec = auth_spec or {}
    auth_type = AuthType(auth_spec.get("type", AuthType.NONE.value))

    if auth_type is AuthType.NONE:
        return ResolvedCredentials(headers={}, secrets={})

    if auth_type is AuthType.API_KEY:
        secret_ref = auth_spec.get("secret_ref") or _env_key_for(contract_id, version)
        value = os.getenv(secret_ref)
        if not value:
            raise CredentialError(
                f"missing API key for {contract_id}@{version} (env var {secret_ref!r} unset)"
            )
        return ResolvedCredentials(headers={"Authorization": f"Bearer {value}"}, secrets={secret_ref: value})

    raise CredentialError(f"authentication.type={auth_type.value!r} is not supported yet")
