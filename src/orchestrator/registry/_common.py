"""Shared types and validators for registry models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

ResourceId = Annotated[str, StringConstraints(pattern=ID_PATTERN, min_length=1, max_length=128)]
SemVer = Annotated[str, StringConstraints(pattern=SEMVER_PATTERN)]


class LifecycleState(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    version: SemVer
    description: str = Field(min_length=1, max_length=1024)
    tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Status(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: LifecycleState = LifecycleState.DRAFT
    replacement: ResourceId | None = None


def is_valid_id(value: str) -> bool:
    return re.fullmatch(ID_PATTERN, value) is not None


def is_valid_semver(value: str) -> bool:
    return re.fullmatch(SEMVER_PATTERN, value) is not None
