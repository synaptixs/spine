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
        "notion",
        "file",
        "openspec",
        "mcp-confluence",
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
        build_service_for("jira://PROJ", dry_run=True)
    msg = str(exc.value)
    assert "jira" in msg
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
