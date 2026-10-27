"""An intent id names a plan's files; it must never choose their directory.

``plan_dir(root) / f"{intent_id}-build.md"`` with an absolute ``intent_id`` is that absolute
path — pathlib drops the left side — and ``a/../../x`` walks out. On a shared server the id
arrives from a caller (``sdlc_plan``'s spec, ``sdlc_approve``'s argument), so a plan or an
approval could be written anywhere the server can write, past the repository list. Found by
the P5 security review (SSPN-258).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.builddoc import (
    approval_path,
    journey_path,
    persist,
    plan_dir,
    plan_file,
    source_text_path,
)

ESCAPES = ["/tmp/pwn", "../../pwn", "a/b", "a\\b", "", "x\x00y"]


@pytest.mark.parametrize("intent_id", ["intent-a", "TCK-1", "add-csv-export", "jira:PROJ-12", "v1.2_x", ".."])
def test_a_plan_file_is_always_inside_the_plans_directory(tmp_path: Path, intent_id: str) -> None:
    path = plan_file(intent_id, "build.md", root=tmp_path)
    assert path.parent == plan_dir(tmp_path) and path.name == f"{intent_id}-build.md"


@pytest.mark.parametrize("intent_id", ESCAPES)
def test_an_intent_id_that_could_leave_the_directory_is_refused(tmp_path: Path, intent_id: str) -> None:
    with pytest.raises(ValueError, match="intent id"):
        plan_file(intent_id, "build.md", root=tmp_path)


@pytest.mark.parametrize("intent_id", ESCAPES)
def test_every_plan_path_refuses_it(tmp_path: Path, intent_id: str) -> None:
    for build in (approval_path, source_text_path, journey_path):
        with pytest.raises(ValueError, match="intent id"):
            build(intent_id, root=tmp_path)
    with pytest.raises(ValueError, match="intent id"):
        persist("# plan", intent_id=intent_id, root=tmp_path)
    assert not (tmp_path / ".spine").exists()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    return repo


async def test_sdlc_plan_refuses_an_escaping_intent_id_and_writes_nothing(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from orchestrator.plugin.server import sdlc_plan

    repo, target = _repo(tmp_path), tmp_path / "elsewhere" / "pwn"
    spec = {"intent_id": str(target), "title": "x", "description": "d", "acceptance_criteria": ["y"]}
    out = await sdlc_plan(repo_path=str(repo), spec=spec)
    assert "intent id" in out["error"]
    assert not (tmp_path / "elsewhere").exists() and not list(tmp_path.rglob("*-build.md"))


def test_sdlc_approve_refuses_an_escaping_intent_id_and_writes_nothing(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from orchestrator.plugin.server import sdlc_approve

    repo = _repo(tmp_path)
    out = sdlc_approve(repo_path=str(repo), intent_id=str(tmp_path / "elsewhere" / "pwn"), decided_by="a")
    assert "intent id" in out["error"]
    assert not (tmp_path / "elsewhere").exists() and not list(tmp_path.rglob("*-approval.json"))
