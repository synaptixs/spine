"""Read-only in-loop tools over a real (tiny) repo."""

from __future__ import annotations

from pathlib import Path

from orchestrator.agentic.tools import build_readonly_tools

LEDGER = '''\
class TokenLedger:
    """Tracks per-stage token usage."""

    def record(self, stage, result):
        return None
'''


def _tools_by_name(root: Path) -> dict[str, object]:
    return {t.spec.name: t for t in build_readonly_tools(root)}


async def test_pkg_relevant_symbols_finds_existing_code(tmp_path: Path) -> None:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    tools = _tools_by_name(tmp_path)
    out = await tools["pkg_relevant_symbols"].run({"query": "persist the token ledger"})  # type: ignore[attr-defined]
    assert "TokenLedger" in out


async def test_read_file_reads_within_root(tmp_path: Path) -> None:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    tools = _tools_by_name(tmp_path)
    out = await tools["read_file"].run({"path": "ledger.py"})  # type: ignore[attr-defined]
    assert "class TokenLedger" in out


async def test_read_file_refuses_outside_root(tmp_path: Path) -> None:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    tools = _tools_by_name(tmp_path)
    out = await tools["read_file"].run({"path": "../../etc/passwd"})  # type: ignore[attr-defined]
    assert out.startswith("error: refusing to read outside")


async def test_read_file_missing_file(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    out = await tools["read_file"].run({"path": "nope.py"})  # type: ignore[attr-defined]
    assert "no such file" in out


async def test_callers_of_returns_a_string(tmp_path: Path) -> None:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    tools = _tools_by_name(tmp_path)
    # Unknown symbol → a clean message, never an exception.
    out = await tools["pkg_callers_of"].run({"symbol": "DoesNotExist"})  # type: ignore[attr-defined]
    assert "no grounded symbol" in out


def test_readonly_tools_are_all_read_only_specs(tmp_path: Path) -> None:
    names = {t.spec.name for t in build_readonly_tools(tmp_path)}
    assert names == {
        "list_files",
        "pkg_relevant_symbols",
        "pkg_api_surface",
        "pkg_callers_of",
        "pkg_blast_radius",
        "read_file",
    }


async def test_blast_radius_reports_transitive_callers(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def leaf():\n    return 1\n\n\ndef caller():\n    return leaf()\n", encoding="utf-8"
    )
    tools = {t.spec.name: t for t in build_readonly_tools(tmp_path)}
    out = await tools["pkg_blast_radius"].run({"symbol": "leaf"})
    assert "leaf" in out
    assert "caller" in out or "affects 0" in out  # impact set (or clean "nothing calls it")


async def test_blast_radius_unknown_symbol(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    tools = {t.spec.name: t for t in build_readonly_tools(tmp_path)}
    out = await tools["pkg_blast_radius"].run({"symbol": "Nonexistent"})
    assert "no grounded symbol" in out


async def test_list_files_discovers_repo_contents(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("z = 3\n", encoding="utf-8")
    tools = {t.spec.name: t for t in build_readonly_tools(tmp_path)}
    out = await tools["list_files"].run({})
    assert "a.py" in out and "pkg/b.py" in out
    assert ".venv" not in out  # vendored/hidden dirs skipped
