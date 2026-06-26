"""Bet 2c-ii: Principal identity + key→principal resolution (no DB).

Fast unit coverage of the auth seam that RBAC + multi-tenancy ride on:
``Principal.has_role`` and the two modes of ``require_principal`` (single-key
default vs. a configured principals map).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.deps import Principal, require_principal


def _request(settings: Settings) -> object:
    """Stub Request exposing app.state.settings, as the dep reads it."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


# ---- Principal.has_role ------------------------------------------------------


def test_has_role_matches_a_held_role() -> None:
    p = Principal(id="alice", tenant_id="acme", roles=frozenset({"dba", "dev"}))
    assert p.has_role("dba")
    assert p.has_role("sre", "dev")  # any-of
    assert not p.has_role("admin")


def test_wildcard_principal_satisfies_every_role() -> None:
    p = Principal(id="root", roles=frozenset({"*"}))
    assert p.has_role("dba") and p.has_role("anything")


def test_any_required_role_is_satisfied_by_anyone() -> None:
    # An approval that names "any" is decidable by any authenticated principal.
    p = Principal(id="bob", roles=frozenset())
    assert p.has_role("any")
    assert not p.has_role("dba")  # but a specific role still gates


# ---- require_principal: single-key default mode ------------------------------


def test_default_mode_returns_wildcard_principal() -> None:
    settings = Settings(api_key="secret")  # no principals map
    principal = require_principal(_request(settings), x_api_key="secret")  # type: ignore[arg-type]
    assert principal.id == "secret"
    assert principal.tenant_id == "default"
    assert principal.has_role("dba")  # wildcard → everyone can approve, as before


def test_default_mode_rejects_bad_key() -> None:
    settings = Settings(api_key="secret")
    with pytest.raises(Exception) as exc:
        require_principal(_request(settings), x_api_key="wrong")  # type: ignore[arg-type]
    assert "API key" in str(exc.value.detail)  # type: ignore[attr-defined]


# ---- require_principal: principals-map mode ----------------------------------


def _mapped() -> Settings:
    return Settings(
        api_key="unused",
        principals={
            "alice-key": {"id": "alice", "tenant_id": "acme", "roles": ["dba"]},
            "bob-key": {"id": "bob", "tenant_id": "globex", "roles": ["dev"]},
        },
    )


def test_map_mode_resolves_tenant_and_roles() -> None:
    p = require_principal(_request(_mapped()), x_api_key="alice-key")  # type: ignore[arg-type]
    assert (p.id, p.tenant_id) == ("alice", "acme")
    assert p.roles == frozenset({"dba"}) and not p.has_role("dev")


def test_map_mode_unknown_key_is_401() -> None:
    with pytest.raises(Exception) as exc:
        require_principal(_request(_mapped()), x_api_key="ghost-key")  # type: ignore[arg-type]
    assert "API key" in str(exc.value.detail)  # type: ignore[attr-defined]


def test_map_mode_ignores_the_single_api_key() -> None:
    # Once a map is configured, the legacy single key no longer authenticates.
    with pytest.raises(Exception):
        require_principal(_request(_mapped()), x_api_key="unused")  # type: ignore[arg-type]


def test_principals_parsed_from_json_string() -> None:
    # Env vars arrive as strings; the validator accepts a JSON object.
    settings = Settings(principals='{"k": {"id": "u", "tenant_id": "t", "roles": ["r"]}}')  # type: ignore[arg-type]
    p = require_principal(_request(settings), x_api_key="k")  # type: ignore[arg-type]
    assert (p.id, p.tenant_id, p.roles) == ("u", "t", frozenset({"r"}))
