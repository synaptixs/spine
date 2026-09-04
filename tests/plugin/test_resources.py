"""MCP resources: the committed bank, the build documents and the state, as documents."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from orchestrator.plugin.resources import (
    _RESOURCES,
    REPO_ROOT_ENV,
    bank_index,
    bank_section,
    default_repo_root,
    plan_document,
    plans_index,
    state_report,
)

LEDGER = '''\
class TokenLedger:
    """Tracks per-stage token usage."""

    def record(self, stage, result):
        return None
'''


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path))
    return tmp_path


def test_the_default_repo_is_the_env_override_else_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_repo_root() == tmp_path.resolve()
    monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "elsewhere"))
    assert default_repo_root() == (tmp_path / "elsewhere").resolve()


def test_a_missing_bank_is_a_note_naming_what_builds_it(repo: Path) -> None:
    text = bank_index()
    assert "No knowledge base" in text and "understand_repo" in text
    assert "No knowledge base" in bank_section("architecture")  # same note, not an error


def test_a_built_bank_is_readable_by_index_and_by_section(repo: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    assert "error" not in understand_repo(str(repo))
    index = bank_index()
    assert "spine://bank/architecture" in index and "spine://bank/conventions" in index
    page = bank_section("architecture")
    assert page.startswith("#") or "rchitecture" in page
    assert "No section" in bank_section("no-such-page")


def test_a_section_cannot_escape_the_bank(repo: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    (repo.parent / "secret.md").write_text("SECRET", encoding="utf-8")
    understand_repo(str(repo))
    assert "SECRET" not in bank_section("../../secret")
    assert "SECRET" not in bank_section("../secret")


def test_plans_index_and_document_with_and_without_an_approval(repo: Path) -> None:
    from orchestrator.sdlc.builddoc import PlanApproval, plan_dir, save_approval

    assert "No build documents" in plans_index() and "sdlc_plan" in plans_index()
    plans = plan_dir(repo)
    plans.mkdir(parents=True)
    (plans / "TCK-1-build.md").write_text("# Build TCK-1\n\nthe document\n", encoding="utf-8")
    (plans / "TCK-2-build.md").write_text("# Build TCK-2\n", encoding="utf-8")
    save_approval(
        PlanApproval(
            intent_id="TCK-2",
            decision="APPROVED",
            decided_by="ana",
            decided_at="2026-09-04",
            digest="d",
            commit="c",
            note="ok",
        ),
        root=repo,
    )
    index = plans_index()
    assert "| `TCK-1` | not decided |" in index and "| `TCK-2` | APPROVED | ana |" in index
    assert "spine://plan/TCK-1" in index
    doc = plan_document("TCK-1")
    assert doc.startswith("> **Not decided.**") and "the document" in doc
    doc = plan_document("TCK-2")
    assert doc.startswith("> **APPROVED** by ana on 2026-09-04 — ok")
    assert "No build document" in plan_document("TCK-9")


def test_a_plan_id_cannot_escape_the_plans_dir(repo: Path) -> None:
    from orchestrator.sdlc.builddoc import plan_dir

    plan_dir(repo).mkdir(parents=True)
    (repo / ".spine" / "leak-build.md").write_text("LEAK", encoding="utf-8")
    assert "LEAK" not in plan_document("../leak")


def test_state_report_renders_the_repo(repo: Path) -> None:
    text = state_report()
    assert "TokenLedger" in text or "ledger" in text.lower()


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_resources_reach_the_host_and_read_back(repo: Path) -> None:
    from orchestrator.plugin.server import build_server, understand_repo

    understand_repo(str(repo))
    server = build_server()
    direct = {str(r.uri): r for r in await server.list_resources()}
    templates = {t.uri_template: t for t in await server.list_resource_templates()}
    assert set(direct) == {"spine://bank", "spine://plans", "spine://state"}
    assert set(templates) == {"spine://bank/{section}", "spine://plan/{intent_id}"}
    assert {s.uri for s in _RESOURCES} == set(direct) | set(templates)
    assert all(r.mime_type == "text/markdown" for r in direct.values())
    contents = list(await server.read_resource("spine://bank/architecture"))
    assert contents and contents[0].mime_type == "text/markdown"
    assert "rchitecture" in str(contents[0].content)
    contents = list(await server.read_resource("spine://plans"))
    assert "No build documents" in str(contents[0].content)
