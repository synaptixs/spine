"""Shared construction of a source → Jira ``BacklogService``.

The ``orchestrator ingest`` CLI, the SDLC pipeline, and the Block-B web
preview all need the same wiring: a requirements source, the two LLM stages
(intent extractor + spec writer) honoring ``ORCHESTRATOR_INTAKE_MODEL``, the
gap analyzer, and a Jira tracker. Only the *source* differs by stack, so the
LLM/tracker wiring is shared and the source is swapped per kind.

``build_service_for`` dispatches on the source URI's kind (``confluence`` /
``notion``); the per-kind builders raise ``IntakeNotConfiguredError`` (not a
CLI/HTTP error) so each caller maps it onto its own surface — ``typer.Exit``
for the CLI, a 400 for the web app.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from orchestrator.core.llm import LiteLLMClient
from orchestrator.intake.confluence import ConfluenceAdapter, ConfluenceConfig
from orchestrator.intake.file_source import FileSourceAdapter, FileSourceConfig
from orchestrator.intake.gaps import GapAnalyzer, load_gap_rules
from orchestrator.intake.intents import IntentExtractor
from orchestrator.intake.jira import JiraAdapter, JiraConfig
from orchestrator.intake.jira_source import JiraSourceAdapter
from orchestrator.intake.notion import NotionAdapter, NotionConfig
from orchestrator.intake.service import BacklogService, parse_source_uri
from orchestrator.intake.source import SourceAdapter
from orchestrator.intake.specs import SpecWriter

if TYPE_CHECKING:
    from orchestrator.intake.mcp_source import MCPSourceConfig


class IntakeNotConfiguredError(RuntimeError):
    """Raised when required source credentials are missing from the env."""


def _build_service(source: SourceAdapter, *, dry_run: bool, rules_path: str | None) -> BacklogService:
    """Wire the shared LLM stages + Jira tracker around a given source."""
    llm = LiteLLMClient()
    # The intake agents default to claude-sonnet-4-6; ORCHESTRATOR_INTAKE_MODEL
    # swaps them onto another provider (needs that provider's key in the env).
    intake_model = os.getenv("ORCHESTRATOR_INTAKE_MODEL")
    model_kwargs = {"model": intake_model} if intake_model else {}
    rules = load_gap_rules(rules_path) if rules_path else None
    return BacklogService(
        source=source,
        extractor=IntentExtractor(llm, **model_kwargs),
        analyzer=GapAnalyzer(rules),
        spec_writer=SpecWriter(llm, **model_kwargs),
        tracker=JiraAdapter(JiraConfig(dry_run=dry_run)),
    )


def build_confluence_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Wire a Confluence-backed ``BacklogService`` from environment config.

    ``dry_run`` controls the Jira tracker only; the web preview never writes,
    so it passes ``dry_run=True`` and simply never calls ``create_issues``.
    Raises ``IntakeNotConfiguredError`` if Confluence credentials are absent.
    """
    conf = ConfluenceConfig()
    if not conf.configured:
        raise IntakeNotConfiguredError(
            "Confluence not configured (set CONFLUENCE_BASE_URL / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN)."
        )
    return _build_service(ConfluenceAdapter(conf), dry_run=dry_run, rules_path=rules_path)


def build_jira_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Wire a Jira-backed (read) ``BacklogService`` from environment config.

    Reads existing issues as the requirements source (``jira://PROJ-123`` /
    ``jira://PROJ`` / ``jira://jql/…``). Uses the same ``JIRA_`` creds as the
    tracker, minus ``project_key`` (only writes need a target project). Raises
    ``IntakeNotConfiguredError`` when the read creds are absent.
    """
    conf = JiraConfig()
    if not (conf.base_url and conf.email and conf.api_token):
        raise IntakeNotConfiguredError(
            "Jira source not configured (set JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN)."
        )
    return _build_service(JiraSourceAdapter(conf), dry_run=dry_run, rules_path=rules_path)


def build_notion_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Wire a Notion-backed ``BacklogService`` from environment config.

    Raises ``IntakeNotConfiguredError`` if ``NOTION_API_TOKEN`` is absent.
    """
    conf = NotionConfig()
    if not conf.configured:
        raise IntakeNotConfiguredError("Notion not configured (set NOTION_API_TOKEN).")
    return _build_service(NotionAdapter(conf), dry_run=dry_run, rules_path=rules_path)


def build_file_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Wire a local-filesystem-backed ``BacklogService``.

    The lowest-friction source: no credentials, so this never raises
    ``IntakeNotConfiguredError``. The actual path arrives later via the source
    URI's root at ``BacklogService.analyze`` time, not from the env.
    """
    return _build_service(FileSourceAdapter(FileSourceConfig()), dry_run=dry_run, rules_path=rules_path)


def build_openspec_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Wire an OpenSpec-backed ``BacklogService`` (spec-driven intake).

    Like ``file``, it needs no credentials — the change lives on disk under
    ``openspec/`` (``$ORCHESTRATOR_OPENSPEC_ROOT``). The adapter is a
    ``StructuredIntentSource``, so ``analyze`` parses changes → intents
    deterministically and skips the LLM extractor.
    """
    from orchestrator.intake.openspec_source import OpenSpecSourceAdapter

    return _build_service(OpenSpecSourceAdapter(), dry_run=dry_run, rules_path=rules_path)


def _build_mcp_service(
    config: MCPSourceConfig, *, label: str, dry_run: bool, rules_path: str | None
) -> BacklogService:
    """Wire a source backed by an onboarded MCP server. Shared by every MCP kind.

    Reads through the operator's MCP server instead of direct REST creds. Raises
    ``IntakeNotConfiguredError`` when the named server isn't in the mcpServers
    config. Lazy-imports the ``mcp`` extra.
    """
    from orchestrator.intake.mcp_source import MCPSourceAdapter
    from orchestrator.mcp.registry import MCPRegistry

    if not config.configured:
        raise IntakeNotConfiguredError(
            f"{label} needs a server + tools (set MCP_SOURCE_SERVER / MCP_SOURCE_DOC_TOOL, "
            "or use the mcp-confluence / mcp-jira presets)."
        )
    registry = MCPRegistry.from_config()
    if config.server not in registry.server_names():
        raise IntakeNotConfiguredError(
            f"{label} needs an onboarded MCP server named {config.server!r} "
            "(add it to your mcpServers config, or set the matching *_SERVER env)."
        )
    return _build_service(MCPSourceAdapter(registry, config), dry_run=dry_run, rules_path=rules_path)


def build_mcp_confluence_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Confluence pages via an onboarded MCP server (e.g. ``mcp-atlassian``)."""
    from orchestrator.intake.mcp_source import MCPSourceConfig

    return _build_mcp_service(
        MCPSourceConfig.for_confluence(),
        label="MCP Confluence source",
        dry_run=dry_run,
        rules_path=rules_path,
    )


def build_mcp_jira_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Jira issues via an onboarded MCP server. ``mcp-jira://<issue-key>`` walks the
    issue + its ``parent = <key>`` children; the same ``mcp-atlassian`` server that
    serves Confluence usually serves Jira too (point both at it via the ``*_SERVER`` env)."""
    from orchestrator.intake.mcp_source import MCPSourceConfig

    return _build_mcp_service(
        MCPSourceConfig.for_jira(), label="MCP Jira source", dry_run=dry_run, rules_path=rules_path
    )


def build_mcp_service(*, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Generic escape hatch — any onboarded MCP server, via ``MCP_SOURCE_*`` env
    (server + doc/children tool + arg names). For sources without a preset."""
    from orchestrator.intake.mcp_source import MCPSourceConfig

    return _build_mcp_service(
        MCPSourceConfig.from_env(), label="MCP source", dry_run=dry_run, rules_path=rules_path
    )


_BUILDERS = {
    "confluence": build_confluence_service,
    "jira": build_jira_service,
    "notion": build_notion_service,
    "file": build_file_service,
    "openspec": build_openspec_service,
    "mcp-confluence": build_mcp_confluence_service,
    "mcp-jira": build_mcp_jira_service,
    "mcp": build_mcp_service,
}

#: Source kinds the factory can wire — callers that only validate a URI (e.g.
#: the SDLC CLI, which defers the actual build to the worker) check against
#: this instead of hardcoding ``confluence``.
SUPPORTED_SOURCE_KINDS = frozenset(_BUILDERS)


def build_service_for(source_uri: str, *, dry_run: bool, rules_path: str | None = None) -> BacklogService:
    """Build the right ``BacklogService`` for ``<kind>://<root>`` by source kind."""
    kind, _ = parse_source_uri(source_uri)
    builder = _BUILDERS.get(kind)
    if builder is None:
        supported = ", ".join(sorted(_BUILDERS))
        raise IntakeNotConfiguredError(f"unsupported source kind {kind!r} (supported: {supported}).")
    return builder(dry_run=dry_run, rules_path=rules_path)
