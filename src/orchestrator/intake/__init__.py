"""Block B — Confluence → structured Jira backlog (SDLC orchestrator).

The second adopter wedge (see ``docs/specs/SDLC-ORCHESTRATOR-PLAN.md`` §6
Block B). Reads requirements from a source system (Confluence first),
derives intents, flags gaps, drafts specs, and creates a Jira backlog —
read-only by default, writes gated by the intent-approval bookend.

Surface (built incrementally B.1–B.6):
  - ``source``: the ``SourceAdapter`` protocol + ``SourceDocument`` /
    ``SourceRef`` models that decouple intent extraction from any one
    requirements system.
  - ``confluence``: the first concrete ``SourceAdapter``.

Intents, gap analysis, specs, the Jira adapter, and the ingest CLI land
in subsequent commits.
"""

from orchestrator.intake.gaps import (
    GapAnalyzer,
    GapFinding,
    GapRule,
    GapSeverity,
    blocks_approval,
    load_gap_rules,
)
from orchestrator.intake.intents import Intent, IntentExtractor
from orchestrator.intake.jira import (
    CreatedIssue,
    IssueLink,
    IssueRequest,
    IssueTrackerAdapter,
    JiraAdapter,
    JiraConfig,
)
from orchestrator.intake.source import SourceAdapter, SourceDocument, SourceRef
from orchestrator.intake.specs import FeatureSpec, SpecWriter

__all__ = [
    "CreatedIssue",
    "FeatureSpec",
    "GapAnalyzer",
    "GapFinding",
    "GapRule",
    "GapSeverity",
    "Intent",
    "IntentExtractor",
    "IssueLink",
    "IssueRequest",
    "IssueTrackerAdapter",
    "JiraAdapter",
    "JiraConfig",
    "SourceAdapter",
    "SourceDocument",
    "SourceRef",
    "SpecWriter",
    "blocks_approval",
    "load_gap_rules",
]
