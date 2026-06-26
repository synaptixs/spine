"""ToolContract: versioned spec for an external capability."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.registry._common import Metadata, Status
from orchestrator.registry.agent_template import FieldSchema


class SideEffect(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ApprovalPolicy(str, Enum):
    NEVER = "never"
    CONDITIONAL = "conditional"
    ALWAYS = "always"


class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    MTLS = "mtls"


class RateLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int | None = Field(default=None, ge=1)
    requests_per_day: int | None = Field(default=None, ge=1)
    burst: int | None = Field(default=None, ge=1)


class Authentication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AuthType = AuthType.NONE
    secret_ref: str | None = None


class Observability(BaseModel):
    """Audit + telemetry requirements for this tool."""

    model_config = ConfigDict(extra="forbid")

    audit: bool = True
    trace: bool = True


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=1024)
    inputs: list[FieldSchema] = Field(default_factory=list)
    outputs: list[FieldSchema] = Field(default_factory=list)
    side_effects: SideEffect
    idempotent: bool
    contains_pii: bool = False
    data_freshness: str | None = None
    requires_approval: ApprovalPolicy = ApprovalPolicy.NEVER
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    authentication: Authentication = Field(default_factory=Authentication)
    endpoint: str | None = None
    observability: Observability = Field(default_factory=Observability)

    @model_validator(mode="after")
    def _audit_is_mandatory(self) -> ToolSpec:
        if not self.observability.audit:
            raise ValueError("ToolContract observability.audit must be true (audit is mandatory).")
        return self

    @model_validator(mode="after")
    def _non_idempotent_requires_idempotency_key(self) -> ToolSpec:
        if not self.idempotent:
            input_names = {f.name for f in self.inputs}
            if "idempotency_key" not in input_names:
                raise ValueError("Non-idempotent tools must accept an 'idempotency_key' input.")
        return self

    @model_validator(mode="after")
    def _destructive_requires_always_approval(self) -> ToolSpec:
        if (
            self.side_effects is SideEffect.DESTRUCTIVE
            and self.requires_approval is not ApprovalPolicy.ALWAYS
        ):
            raise ValueError("Destructive tools must set requires_approval='always'.")
        return self


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: Metadata
    spec: ToolSpec
    status: Status = Field(default_factory=Status)
