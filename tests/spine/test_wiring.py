"""Env-gated grounder composition — Spine Seam 1 live wiring (Phase 1 → ✅)."""

from __future__ import annotations

import pytest

from orchestrator.spine import (
    CompositeGrounder,
    compose_factory_with_ontomesh,
    compose_with_ontomesh,
    ontomesh_grounder_from_env,
)


class _Base:
    def context_for_spec(self, spec: dict[str, object]) -> str:  # noqa: ARG002
        return "PKG CONTEXT"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("SPINE_ONTOMESH_URL", "SPINE_ONTOMESH_FLAVOR", "SPINE_ONTOMESH_MIN_CONFIDENCE"):
        monkeypatch.delenv(k, raising=False)


def test_disabled_by_default_returns_base_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert ontomesh_grounder_from_env() is None
    base = _Base()
    assert compose_with_ontomesh(base) is base  # unchanged, no wrapping


def test_requires_both_url_and_flavor(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SPINE_ONTOMESH_URL", "http://ontomesh:5051")  # flavor missing
    assert ontomesh_grounder_from_env() is None


def test_enabled_composes_with_ontomesh(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SPINE_ONTOMESH_URL", "http://ontomesh:5051")
    monkeypatch.setenv("SPINE_ONTOMESH_FLAVOR", "telco")
    assert ontomesh_grounder_from_env() is not None
    composed = compose_with_ontomesh(_Base())
    assert isinstance(composed, CompositeGrounder)


def test_factory_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("SPINE_ONTOMESH_URL", "http://ontomesh:5051")
    monkeypatch.setenv("SPINE_ONTOMESH_FLAVOR", "telco")
    factory = compose_factory_with_ontomesh(lambda _root: _Base())
    grounder = factory("/some/root")
    assert isinstance(grounder, CompositeGrounder)
    # disabled → factory returned unchanged
    _clear(monkeypatch)
    same = compose_factory_with_ontomesh(lambda _root: _Base())
    assert same("/r").context_for_spec({}) == "PKG CONTEXT"
