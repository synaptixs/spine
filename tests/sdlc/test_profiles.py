"""The shipped workflow profiles, and the ones a repo can carry (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orchestrator.ir.graph import NodeType
from orchestrator.ir.validator import IRValidator
from orchestrator.sdlc.profiles import (
    REPO_PROFILE_DIR,
    ProfileNotFoundError,
    load_profile,
    profile_names,
    profile_path,
    repo_profile_names,
)

SHIPPED = ("default", "bug", "enhancement")


@pytest.mark.parametrize("name", SHIPPED)
async def test_every_shipped_profile_validates(name: str) -> None:
    """A profile that cannot be validated is a packaging bug, and `autorun` falls back to the
    imperative stages rather than executing a graph nobody checked — so an invalid profile would
    degrade a run silently rather than failing it."""
    report = await IRValidator().validate(load_profile(name))
    assert report.ok, report.failures


def test_the_enhancement_profile_has_no_rca_node() -> None:
    """The whole reason the file exists. RCA localizes a symptom; a feature request has none, so
    it resolves nothing and prints "not localized" — an empty section that reads as a finding."""
    nodes = {n.id for n in load_profile("enhancement").spec.nodes}
    assert "n_rca" not in nodes
    assert "n_investigate" in nodes and "n_blast_radius" in nodes


def test_the_bug_profile_keeps_it() -> None:
    assert "n_rca" in {n.id for n in load_profile("bug").spec.nodes}


@pytest.mark.parametrize("name", SHIPPED)
def test_every_tool_node_names_a_registered_tool(name: str) -> None:
    """The validator checks this too, but only when it is asked. A profile that ships with a
    typo in a `template_id` is a packaging bug worth catching in the suite."""
    from orchestrator.runtime.tool_registry import default_registry

    registry = default_registry()
    for node in load_profile(name).spec.nodes:
        if node.type is NodeType.TOOL:
            assert node.template_id and registry.has(node.template_id), node.id


def test_a_repo_profile_shadows_the_shipped_one(tmp_path: Path) -> None:
    """Same-name-wins is the rule: a team that wants a different `bug` profile writes `bug.yaml`
    and does not have to learn a precedence order."""
    shipped = yaml.safe_load(profile_path("bug").read_text(encoding="utf-8"))
    shipped["metadata"]["description"] = "the repo's own bug profile"
    carried = tmp_path / REPO_PROFILE_DIR
    carried.mkdir(parents=True)
    (carried / "bug.yaml").write_text(yaml.safe_dump(shipped), encoding="utf-8")

    assert profile_path("bug", tmp_path) == carried / "bug.yaml"
    assert load_profile("bug", tmp_path).metadata.description == "the repo's own bug profile"
    # …and the shipped one is untouched for any repo that carries nothing.
    assert load_profile("bug").metadata.description != "the repo's own bug profile"


def test_a_repo_profile_with_a_new_name_is_listed_and_loadable(tmp_path: Path) -> None:
    shipped = yaml.safe_load(profile_path("default").read_text(encoding="utf-8"))
    shipped["metadata"]["id"] = "sdlc.house_style"
    carried = tmp_path / REPO_PROFILE_DIR
    carried.mkdir(parents=True)
    (carried / "house_style.yaml").write_text(yaml.safe_dump(shipped), encoding="utf-8")

    assert "house_style" in profile_names(tmp_path)
    assert repo_profile_names(tmp_path) == ("house_style",)
    assert load_profile("house_style", tmp_path).metadata.id == "sdlc.house_style"


def test_a_repo_carrying_nothing_changes_nothing(tmp_path: Path) -> None:
    assert repo_profile_names(tmp_path) == ()
    assert set(profile_names(tmp_path)) == set(SHIPPED)


def test_an_unknown_profile_names_the_ones_that_exist(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError, match="enhancement"):
        profile_path("nope", tmp_path)


def test_loading_is_not_cached_across_roots(tmp_path: Path) -> None:
    """It was cached, until profiles could come from a target repo — at which point a stale
    entry would silently run the shipped profile for a repo that carries its own."""
    shipped = yaml.safe_load(profile_path("default").read_text(encoding="utf-8"))
    shipped["metadata"]["description"] = "carried"
    carried = tmp_path / REPO_PROFILE_DIR
    carried.mkdir(parents=True)
    (carried / "default.yaml").write_text(yaml.safe_dump(shipped), encoding="utf-8")

    first = load_profile("default")  # shipped
    second = load_profile("default", tmp_path)  # repo's
    assert first.metadata.description != second.metadata.description
    assert load_profile("default").metadata.description == first.metadata.description
