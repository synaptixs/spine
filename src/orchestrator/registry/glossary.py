"""GlossaryEntry: pinned definitions that agents and verifiers treat as authoritative.

Sprint 11 in the spec lifts this to a full state-channel-pinning feature.
Sprint 7 only needs the entity + lookup endpoint so the ``fetch_metric_definition``
tool has something to read.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from orchestrator.registry._common import Metadata, Status

TERM_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
GlossaryTerm = Annotated[str, StringConstraints(pattern=TERM_PATTERN, min_length=1, max_length=128)]


class GlossaryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: GlossaryTerm
    canonical_value: str = Field(min_length=1, max_length=2048)
    definition: str = Field(min_length=1, max_length=4096)
    source: str = Field(min_length=1, max_length=256, default="org_default")
    owner: str | None = None
    formula: str | None = Field(default=None, max_length=2048)


class GlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: Metadata
    spec: GlossaryDefinition
    status: Status = Field(default_factory=Status)
