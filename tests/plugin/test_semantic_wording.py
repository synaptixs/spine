"""D17: "Deterministic" stays true only if it says what it depends on, and the output names the model.

With hybrid search on and an embedding model configured (the ``semantic`` extra), a ranking depends on that
pinned model. Every MCP tool whose ranking can go through hybrid search says so in its description, and the
output it points at names the model; ``understand`` and ``state`` keep the plain claim because they never load
it.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from orchestrator.plugin import server  # noqa: E402

_PHRASE = "ranks depend on the pinned embedding model"

#: Each tool whose ranking can go through ``HybridRetriever``, and where its output names the model.
_HYBRID_TOOLS = {
    "investigate": "the brief names",
    "root_cause": "the report names",
    "search_graph": "``model`` names",
    "design_change": "is bge-small-en-v1.5 unless ``SPINE_EMBED_MODEL`` names another",
    "sdlc_plan": "is bge-small-en-v1.5 unless ``SPINE_EMBED_MODEL`` names another",
}


@pytest.mark.parametrize("tool", sorted(_HYBRID_TOOLS))
def test_each_hybrid_tool_says_what_its_ranking_depends_on(tool: str) -> None:
    doc = " ".join((inspect.getdoc(getattr(server, tool)) or "").split())
    assert _PHRASE in doc, tool
    assert f"{_PHRASE}, which {_HYBRID_TOOLS[tool]}" in doc, tool


def test_the_module_docstring_qualifies_its_deterministic_claims() -> None:
    doc = " ".join((server.__doc__ or "").split())
    assert "with hybrid search on and the semantic extra" in doc.lower()


def _repo(tmp_path: Path) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "billing.py").write_text(
        'def persist(record, path):\n    """Append one row to the accounting ledger."""\n'
        "    audit(record)\n\n\n"
        "def audit(record):\n    return record\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_search_graph_names_the_model_it_ranked_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPINE_EMBED_MODEL", raising=False)
    out = server.search_graph(_repo(tmp_path), "rows lost from the accounting ledger")
    assert out["model"] is None and "meaning" not in out["legs_used"]
    monkeypatch.setenv("SPINE_EMBED_MODEL", "stand-in")
    out = server.search_graph(_repo(tmp_path / "b"), "rows lost from the accounting ledger")
    assert out["model"] == "stand-in/hash-64"
    assert out["legs_used"] == ["name", "content", "meaning", "graph"]


def test_investigate_brief_names_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    monkeypatch.setenv("SPINE_EMBED_MODEL", "stand-in")
    out = server.investigate(
        _repo(tmp_path), "Ledger rows go missing", "Rows in the accounting ledger are lost."
    )
    assert "meaning (stand-in/hash-64)" in out["markdown"]
    assert "no embedding model was loaded" not in out["markdown"]


def test_understand_and_state_never_import_the_hybrid_or_embedding_modules(tmp_path: Path) -> None:
    """Invariant 2: they are no-model paths. Run both on a repo in a fresh interpreter and look."""
    _repo(tmp_path)
    code = (
        "import sys\n"
        "from orchestrator.knowledge.understand import render_memory_bank\n"
        "from orchestrator.knowledge.current_state import build_current_state\n"
        f"render_memory_bank({str(tmp_path)!r}, refresh=True)\n"
        f"build_current_state({str(tmp_path)!r}, refresh=True)\n"
        "print(sorted(m for m in sys.modules if m in ('orchestrator.pkg.hybrid', 'orchestrator.pkg.embed',"
        " 'orchestrator.pkg.vector_cache', 'onnxruntime')))\n"
    )
    env = {
        "SPINE_EMBED_MODEL": "stand-in",
        "SPINE_HYBRID_SEARCH": "1",
        "ORCHESTRATOR_PKG_CACHE_DIR": str(tmp_path / ".pkgcache"),
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"
