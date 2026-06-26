"""Claim + Evidence: structured, verifier-checkable analytical output."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CLAIM_ID_PATTERN = r"^c_[a-z0-9_]{1,64}$"
ClaimId = Annotated[str, StringConstraints(pattern=CLAIM_ID_PATTERN)]


class ClaimType(str, Enum):
    METRIC = "metric"
    QUALITATIVE = "qualitative"
    COMPARISON = "comparison"
    PROJECTION = "projection"


class Evidence(BaseModel):
    """Reference to an artifact that supports a claim."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    locator: str | None = None
    note: str | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ClaimId
    statement: str = Field(min_length=1, max_length=2048)
    claim_type: ClaimType
    supporting_artifacts: list[Evidence] = Field(min_length=1)
    metric_values: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)
