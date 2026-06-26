"""Smoke tests for the repositories module.

Behavioural tests against a real database live in
``tests/integration/test_registry_db.py`` and run with ``-m integration``.
"""

from __future__ import annotations

from orchestrator.registry.db.models import AgentTemplateRow, ToolContractRow
from orchestrator.registry.repositories import (
    AlreadyExistsError,
    AuditLogRepo,
    ImmutablePublishedError,
    NotFoundError,
    VersionedRepo,
)


def test_versioned_repo_generic_accepts_both_entities() -> None:
    """Type-check stand-in: the parametric repo must accept both row types."""
    assert VersionedRepo[AgentTemplateRow] is not None
    assert VersionedRepo[ToolContractRow] is not None


def test_error_hierarchy() -> None:
    assert issubclass(NotFoundError, LookupError)
    assert issubclass(AlreadyExistsError, ValueError)
    assert issubclass(ImmutablePublishedError, ValueError)


def test_repository_public_surface() -> None:
    expected = {
        "create",
        "get_by_id_version",
        "list_versions_for_id",
        "list_page",
        "get_latest_published",
        "publish",
        "mark_deprecated",
        "update_spec_if_draft",
    }
    actual = {m for m in dir(VersionedRepo) if not m.startswith("_")}
    assert expected <= actual, expected - actual


def test_audit_repo_write_signature() -> None:
    assert hasattr(AuditLogRepo, "write")
