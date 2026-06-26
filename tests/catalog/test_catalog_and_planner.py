"""Catalog loading (code + declarative) and deterministic planning."""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.catalog import (
    CapabilityCatalog,
    CapabilityKind,
    ProjectProfile,
    default_catalog,
    plan_capabilities,
)


def _profile(**kw: object) -> ProjectProfile:
    base: dict[str, object] = {
        "languages": frozenset({"python"}),
        "framework": None,
        "has_db": False,
        "has_migrations": False,
        "test_runner": "pytest",
        "task_type": "feature",
    }
    base.update(kw)
    return ProjectProfile(**base)  # type: ignore[arg-type]


# ---- catalog ----------------------------------------------------------------


def test_default_catalog_has_seed_ids() -> None:
    ids = {c.id for c in default_catalog().all()}
    assert {
        "python-conventions",
        "java-conventions",
        "repo-pkg-grounding",
        "migration-fanout",
        "db-schema-mcp",
    } <= ids


def test_default_catalog_includes_promoted_overlay() -> None:
    # The P3 promotion overlay (_PROMOTED) is empty until a skill clears the A/B
    # bar; whatever is in it is part of the default catalog and every SKILL entry
    # must still resolve to a native Skill (the planner-selectability invariant).
    from orchestrator.catalog.catalog import _PROMOTED
    from orchestrator.catalog.skills import default_skills

    skill_ids = {s.id for s in default_skills()}
    catalog_ids = {c.id for c in default_catalog().all()}
    for cap in _PROMOTED:
        assert cap.id in catalog_ids
        assert cap.id in skill_ids  # a promoted capability must have a Skill artifact


def test_declarative_entries_merge_and_override(tmp_path: Path) -> None:
    doc = {
        "capabilities": [
            {
                "id": "react-conventions",
                "kind": "skill",
                "summary": "Match React conventions",
                "selector": {"languages": ["typescript"], "task_types": ["feature"]},
            },
            {  # same id as a seed entry → overrides it
                "id": "db-schema-mcp",
                "kind": "mcp_server",
                "summary": "Custom DB MCP",
                "selector": {"requires_db": True},
                "payload": {"server": "warehouse"},
            },
        ]
    }
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    catalog = CapabilityCatalog.from_sources(path)
    assert catalog.get("react-conventions") is not None
    overridden = catalog.get("db-schema-mcp")
    assert overridden is not None and overridden.payload["server"] == "warehouse"


# ---- planner ----------------------------------------------------------------


def test_python_feature_plan(tmp_path: Path) -> None:
    plan = plan_capabilities(_profile(languages=frozenset({"python"}), task_type="feature"))
    assert "python-conventions" in plan.skills
    assert "repo-pkg-grounding" in plan.skills
    assert "java-conventions" not in plan.skills
    assert plan.workflow_params == {} and plan.mcp_servers == []


def test_migration_plan_sets_workflow_params() -> None:
    plan = plan_capabilities(_profile(task_type="migration"))
    assert plan.workflow_params == {"max_parallel_features": 4, "max_review_iterations": 3}
    # migration is not a feature, so conventions/grounding don't apply
    assert plan.skills == []


def test_db_present_onboards_db_mcp() -> None:
    plan = plan_capabilities(_profile(has_db=True))
    assert "db" in plan.mcp_servers


def test_empty_plan_for_unmatched_profile() -> None:
    # An unknown language on a bugfix matches nothing in the v1 seed.
    plan = plan_capabilities(_profile(languages=frozenset({"go"}), task_type="bugfix"))
    assert plan.is_empty
    assert plan.summary_lines() == ["base pipeline — no extra capabilities selected"]


def test_plan_is_deterministic() -> None:
    p = _profile(languages=frozenset({"python"}), has_db=True, task_type="feature")
    a, b = plan_capabilities(p), plan_capabilities(p)
    assert a.to_dict() == b.to_dict()


def test_every_plan_item_carries_a_rationale() -> None:
    plan = plan_capabilities(_profile(has_db=True))
    assert all(item.rationale for item in plan.items)
    assert all(item.kind in CapabilityKind for item in plan.items)
