"""Validation services for registry writes.

Two layers:

1. **Pydantic shape validation** — the ``AgentTemplate`` and
   ``ToolContract`` models already enforce id pattern, semver,
   mandatory outputs, the three tool invariants (audit mandatory,
   idempotency_key for non-idempotent, always-approval for
   destructive), and so on. ``validate_payload`` wraps Pydantic's
   ``ValidationError`` into the structured ``ValidationReport``
   below so HTTP responses can render field paths uniformly.

2. **Database-context rules** — ``check_published_immutability``
   asserts that a published row cannot be overwritten by a re-POST
   of the same ``(id, version)``. Cross-reference checks
   (``allowed_tools``, ``policies``, ``evals``) are deliberately
   deferred until Phase 1, when there are multiple entities in the
   registry to cross-reference against.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from orchestrator.registry._common import LifecycleState
from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.db.models import AgentTemplateRow, GlossaryEntryRow, ToolContractRow
from orchestrator.registry.glossary import GlossaryEntry
from orchestrator.registry.repositories import ImmutablePublishedError, VersionedRepo
from orchestrator.registry.tool_contract import ToolContract


class ValidationFailure(BaseModel):
    """A single rule violation with a JSON pointer-style field path."""

    model_config = ConfigDict(extra="forbid")

    field: str
    message: str
    rule: str


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: list[ValidationFailure] = []

    @property
    def ok(self) -> bool:
        return not self.failures


def _pydantic_to_failures(exc: PydanticValidationError) -> list[ValidationFailure]:
    out: list[ValidationFailure] = []
    for err in exc.errors():
        path = ".".join(str(part) for part in err["loc"])
        out.append(ValidationFailure(field=path or "<root>", message=err["msg"], rule=err["type"]))
    return out


def validate_agent_template_payload(payload: dict[str, Any]) -> tuple[AgentTemplate | None, ValidationReport]:
    """Validate the raw payload against the AgentTemplate model."""
    try:
        return AgentTemplate.model_validate(payload), ValidationReport()
    except PydanticValidationError as exc:
        return None, ValidationReport(failures=_pydantic_to_failures(exc))


def validate_tool_contract_payload(payload: dict[str, Any]) -> tuple[ToolContract | None, ValidationReport]:
    try:
        return ToolContract.model_validate(payload), ValidationReport()
    except PydanticValidationError as exc:
        return None, ValidationReport(failures=_pydantic_to_failures(exc))


def validate_glossary_entry_payload(
    payload: dict[str, Any],
) -> tuple[GlossaryEntry | None, ValidationReport]:
    try:
        return GlossaryEntry.model_validate(payload), ValidationReport()
    except PydanticValidationError as exc:
        return None, ValidationReport(failures=_pydantic_to_failures(exc))


async def check_published_immutability(
    repo: VersionedRepo[AgentTemplateRow] | VersionedRepo[ToolContractRow] | VersionedRepo[GlossaryEntryRow],
    *,
    id: str,
    version: str,
) -> None:
    """Raise ``ImmutablePublishedError`` if (id, version) is already published.

    Call this before any registration that would overwrite an existing
    row. Draft rows are still mutable via ``update_spec_if_draft``.
    """
    existing = await repo.get_by_id_version(id, version)
    if existing is not None and existing.status != LifecycleState.DRAFT.value:
        raise ImmutablePublishedError(f"{id}@{version} is {existing.status} and cannot be modified.")
