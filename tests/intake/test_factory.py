"""Tests for the source → ``BacklogService`` factory dispatcher.

``build_service_for`` is the single seam the CLI (``ingest`` / ``sdlc``) and
the SDLC worker share to wire a backlog pipeline from a ``<kind>://<root>``
URI. These cover the dispatch table, the unsupported-kind and
not-configured error surfaces, and that ``SUPPORTED_SOURCE_KINDS`` stays in
lockstep with the builder registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from orchestrator.intake import factory
from orchestrator.intake.confluence import ConfluenceAdapter, ConfluenceConfig
from orchestrator.intake.factory import (
    SUPPORTED_SOURCE_KINDS,
    IntakeNotConfiguredError,
    build_service_for,
)
from orchestrator.intake.file_source import FileSourceAdapter
from orchestrator.intake.notion import NotionAdapter, NotionConfig
from orchestrator.intake.service import SourceUriError


def test_supported_kinds_match_registry() -> None:
    # The validate-only callers (e.g. `sdlc run`) trust this set; keep it
    # exactly the keys the dispatcher can actually build.
    assert set(SUPPORTED_SOURCE_KINDS) == {
        "confluence",
        "jira",
        "notion",
        "file",
        "openspec",
        "mcp-confluence",
        "mcp-jira",
        "mcp",
    }


def test_dispatch_routes_to_kind_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, dict[str, object]] = {}

    def spy(kind: str) -> Callable[..., str]:
        def _build(*, dry_run: bool, rules_path: str | None = None) -> str:
            calls[kind] = {"dry_run": dry_run, "rules_path": rules_path}
            return f"{kind}-service"

        return _build

    monkeypatch.setitem(factory._BUILDERS, "confluence", spy("confluence"))
    monkeypatch.setitem(factory._BUILDERS, "notion", spy("notion"))

    notion = cast(object, build_service_for("notion://abc", dry_run=True, rules_path="r.yaml"))
    assert notion == "notion-service"
    assert calls["notion"] == {"dry_run": True, "rules_path": "r.yaml"}

    confluence = cast(object, build_service_for("confluence://xyz", dry_run=False))
    assert confluence == "confluence-service"
    assert calls["confluence"] == {"dry_run": False, "rules_path": None}


def test_unsupported_kind_raises_with_supported_list() -> None:
    with pytest.raises(IntakeNotConfiguredError) as exc:
        build_service_for("linear://TEAM", dry_run=True)
    msg = str(exc.value)
    assert "linear" in msg
    assert "confluence" in msg and "notion" in msg


def test_malformed_uri_propagates_source_uri_error() -> None:
    # parse_source_uri's own validation surfaces before any builder lookup.
    with pytest.raises(SourceUriError):
        build_service_for("not-a-uri", dry_run=True)


def test_notion_builder_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "NotionConfig", lambda: NotionConfig(api_token=""))
    with pytest.raises(IntakeNotConfiguredError, match="NOTION_API_TOKEN"):
        build_service_for("notion://root", dry_run=True)


def test_confluence_builder_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory, "ConfluenceConfig", lambda: ConfluenceConfig(base_url="", email="", api_token="")
    )
    with pytest.raises(IntakeNotConfiguredError, match="Confluence not configured"):
        build_service_for("confluence://root", dry_run=True)


def test_notion_builder_configured_wires_notion_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "NotionConfig", lambda: NotionConfig(api_token="tok"))
    service = build_service_for("notion://root", dry_run=True)
    assert isinstance(service._source, NotionAdapter)


def test_file_builder_needs_no_credentials() -> None:
    # file:// is the zero-config source — no env, never IntakeNotConfiguredError.
    service = build_service_for("file://./spec.md", dry_run=True)
    assert isinstance(service._source, FileSourceAdapter)


def test_confluence_builder_configured_wires_confluence_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory,
        "ConfluenceConfig",
        lambda: ConfluenceConfig(base_url="https://x.atlassian.net/wiki", email="e@x.io", api_token="t"),
    )
    service = build_service_for("confluence://root", dry_run=True)
    assert isinstance(service._source, ConfluenceAdapter)


# --- the intake agents can be given the run's client (B32, SSPN-56) ----------------------------------
#
# Intake builds its own `LiteLLMClient()`. A run that has a budget and a ledger therefore spent on
# intake outside both. `llm=` hands the factory the run's client instead; with nothing given, the
# agents get the client they always got.


@pytest.mark.parametrize("kind", sorted(SUPPORTED_SOURCE_KINDS))
def test_every_builder_accepts_the_run_client(kind: str) -> None:
    import inspect

    assert "llm" in inspect.signature(factory._BUILDERS[kind]).parameters


@pytest.mark.parametrize("uri", ["openspec://", "file://./spec.md"])
def test_the_run_client_reaches_both_intake_agents(uri: str) -> None:
    from orchestrator.core.llm import MockLLMClient

    client = MockLLMClient(script=[])  # building a service must not call the model, so it is empty
    service = build_service_for(uri, dry_run=True, llm=client)
    assert service._extractor._llm is client
    assert service._spec_writer._llm is client


def test_without_a_client_the_agents_get_the_one_they_always_got() -> None:
    from orchestrator.core.llm import LiteLLMClient

    service = build_service_for("openspec://", dry_run=True)
    assert isinstance(service._extractor._llm, LiteLLMClient)
    assert isinstance(service._spec_writer._llm, LiteLLMClient)
