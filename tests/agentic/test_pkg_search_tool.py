"""Codegen's hybrid search is opt-in (hybrid KG search D11): a ``pkg_search`` tool and ranked grounding.

The sealed read measured where a change lands, not whether a generated patch resolves. So codegen keeps its
lexical tools and context unless ``SPINE_HYBRID_CODEGEN=1``, and ``SPINE_HYBRID_SEARCH=0`` still wins. A
paid resolve run, on the user's go, is what would move this default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.agentic.tools import build_readonly_tools
from orchestrator.pkg.hybrid import hybrid_codegen_enabled
from orchestrator.sdlc.grounding import PKGCodegenGrounder

_BILLING = '''\
def persist(record, path):
    """Append one row to the accounting ledger."""
    with open(path, "a") as fh:
        fh.write(record)


def render(record):
    return str(record)
'''

_QUERY = "rows written to the accounting ledger are lost"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "billing.py").write_text(_BILLING, encoding="utf-8")
    return tmp_path


def _names(root: Path) -> set[str]:
    return {t.spec.name for t in build_readonly_tools(root)}


@pytest.mark.hybrid_default
def test_codegen_keeps_its_lexical_tools_by_default(repo: Path) -> None:
    assert not hybrid_codegen_enabled()
    assert "pkg_search" not in _names(repo)


@pytest.mark.hybrid_default
def test_opted_in_the_agent_can_search_by_source_text_and_is_told_why(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPINE_HYBRID_CODEGEN", "1")
    tools = {t.spec.name: t for t in build_readonly_tools(repo)}
    out = await_run(tools["pkg_search"], {"query": _QUERY})
    line = next(row for row in out.splitlines() if " persist " in row)
    assert "billing.py" in line
    assert "found by: " in line and "content" in line


@pytest.mark.hybrid_default
def test_the_global_off_switch_beats_the_codegen_opt_in(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_CODEGEN", "1")
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "0")
    assert not hybrid_codegen_enabled()
    assert "pkg_search" not in _names(repo)


@pytest.mark.hybrid_default
def test_the_grounding_context_is_ranked_by_hybrid_search_only_when_opted_in(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = {"title": "Ledger rows go missing", "summary": _QUERY}
    lexical = PKGCodegenGrounder.from_repo(repo, use_cache=False).context_for_spec(spec)
    assert "persist" not in lexical  # no symbol is named for the ledger
    monkeypatch.setenv("SPINE_HYBRID_CODEGEN", "1")
    hybrid = PKGCodegenGrounder.from_repo(repo, use_cache=False).context_for_spec(spec)
    assert "persist" in hybrid and "billing.py" in hybrid


def await_run(tool: object, args: dict[str, object]) -> str:
    import asyncio

    return str(asyncio.run(tool.run(args)))  # type: ignore[attr-defined]
