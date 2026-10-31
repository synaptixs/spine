"""SSPN-279: `sdlc_plan` from a source, for the editor's Plan from requirement (D102, D104–D108).

The MCP tool and `sdlc plan --source` share one path (`sdlc/plan_source.py`), so a ticket planned in
an editor and in a terminal renders the same document and leaves the same spec and source text
beside it. Every test here runs on a seeded intake cache with the model forbidden, except the one
that meters a fresh extraction, which stands a fake model in for the real one.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.core.llm.client import CompletionResult
from orchestrator.intake.cache import save_plan, source_fingerprint
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec

CRITERION = "Cart.total skips skus without a price instead of raising KeyError"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(
        "class Cart:\n    def __init__(self):\n        self.items = []\n\n"
        "    def total(self, prices):\n        return sum(prices[s] * q for s, q in self.items)\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture(autouse=True)
def no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test its own intake cache, and any real model call fails the test."""
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "intake-cache"))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    async def _forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a model was called")

    monkeypatch.setattr("orchestrator.core.llm.litellm_client.LiteLLMClient.complete", _forbidden)


def _ticket(tmp_path: Path, name: str = "PROJ-42.md") -> str:
    path = tmp_path / name
    path.write_text(
        f"# PROJ-42\n\nCart.total crashes on an unknown sku.\n\n## Acceptance criteria\n- {CRITERION}\n",
        encoding="utf-8",
    )
    return f"file://{path}"


def _seed(source: str, *ids: str) -> None:
    """A cached extraction of ``source``, as `analyze_cached` would have written it."""
    ids = ids or ("PROJ-42",)
    plan = BacklogPlan(
        documents=[SourceDocument(id="PROJ-42", title="PROJ-42", body=f"- {CRITERION}")],
        intents=[
            Intent(id=i, title=f"{i} title", description="d", acceptance_criteria=[CRITERION]) for i in ids
        ],
        gaps=[],
        specs=[
            FeatureSpec(
                intent_id=i,
                title=f"{i}: Cart.total raises KeyError",
                summary="Cart.total in shop/cart.py crashes.",
                acceptance_criteria=[CRITERION],
            )
            for i in ids
        ],
        blocked=False,
        truncated=False,
    )
    save_plan(source, plan, fingerprint=source_fingerprint(source))


async def test_plans_a_cached_source_with_no_model_and_saves_its_spec_and_source_text(
    checkout: Path, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_plan
    from orchestrator.sdlc.builddoc import load_source_text

    source = _ticket(tmp_path)
    _seed(source)
    out = await sdlc_plan(str(checkout), source=source)
    assert "error" not in out, out
    assert out["intent_id"] == "PROJ-42"
    assert out["cached"] is True
    assert out["spent_usd"] == 0
    assert "## 12. Confidence" in out["document"]
    assert Path(out["path"]).is_file()
    assert CRITERION in load_source_text("PROJ-42", root=checkout)
    assert (checkout / ".spine" / "plans" / "PROJ-42-spec.json").is_file() or any(
        (checkout / ".spine").rglob("PROJ-42*spec*")
    )


def test_the_editor_and_the_terminal_render_the_same_document_from_one_source(
    checkout: Path, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)
    _seed(source)
    result = CliRunner().invoke(app, ["sdlc", "plan", "--source", source, "--path", str(checkout), "--quiet"])
    assert result.exit_code == 0, result.output
    terminal = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    editor = asyncio.run(sdlc_plan(str(checkout), source=source, persist_plan=False))
    assert editor["document"] == terminal


async def test_extract_false_answers_from_the_cache_and_writes_nothing(
    checkout: Path, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)
    out = await sdlc_plan(str(checkout), source=source, extract=False)
    assert out == {"source": source, "cached": False, "intents": [], "spent_usd": 0}
    _seed(source, "PROJ-42", "PROJ-43")
    out = await sdlc_plan(str(checkout), source=source, extract=False)
    assert out["cached"] is True
    assert out["intents"] == [
        {"id": "PROJ-42", "title": "PROJ-42: Cart.total raises KeyError"},
        {"id": "PROJ-43", "title": "PROJ-43: Cart.total raises KeyError"},
    ]
    assert not (checkout / ".spine").exists()


async def test_an_edited_file_is_not_cached_even_though_an_entry_exists(
    checkout: Path, tmp_path: Path
) -> None:
    """The same rule `analyze_cached` applies: a local file that changed is extracted again (spends)."""
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)
    _seed(source)
    Path(source.removeprefix("file://")).write_text(
        "# PROJ-42\n\nsomething else entirely\n", encoding="utf-8"
    )
    assert (await sdlc_plan(str(checkout), source=source, extract=False))["cached"] is False


async def test_several_intents_and_none_named_lists_them_and_plans_nothing(
    checkout: Path, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)
    _seed(source, "PROJ-42", "PROJ-43")
    out = await sdlc_plan(str(checkout), source=source)
    assert [i["id"] for i in out["intents"]] == ["PROJ-42", "PROJ-43"]
    assert "document" not in out
    assert not (checkout / ".spine" / "plans").exists()
    named = await sdlc_plan(str(checkout), source=source, intent_id="PROJ-43")
    assert named["intent_id"] == "PROJ-43"
    assert named["spent_usd"] == 0


async def test_an_intent_the_source_does_not_hold_is_refused_naming_those_it_does(
    checkout: Path, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)
    _seed(source)
    out = await sdlc_plan(str(checkout), source=source, intent_id="NOPE-1")
    assert "NOPE-1" in out["error"] and "PROJ-42" in out["error"]


async def test_exactly_one_of_spec_and_source(checkout: Path, tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_plan

    assert "error" in await sdlc_plan(str(checkout))
    both = await sdlc_plan(
        str(checkout),
        {"intent_id": "X-1", "title": "t", "acceptance_criteria": ["a"]},
        source=_ticket(tmp_path),
    )
    assert "one of" in both["error"]


async def test_a_fresh_extraction_reports_what_it_spent(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D105: the intake model is metered through the same `BudgetedLLMClient` runs use, uncapped."""
    from orchestrator.plugin.server import sdlc_plan

    source = _ticket(tmp_path)

    class _Priced:
        async def complete(self, *_a: Any, **_k: Any) -> CompletionResult:
            return CompletionResult(
                text="{}", model="m", prompt_tokens=10, completion_tokens=5, cost_usd=0.0123, latency_ms=1.0
            )

    async def _extract(service: Any, uri: str, **_k: Any) -> BacklogPlan:
        await service.llm.complete([], model="m")
        await service.llm.complete([], model="m")
        _seed(uri)
        from orchestrator.intake.cache import load_cached_plan

        plan = load_cached_plan(uri)
        assert plan is not None
        return plan

    from orchestrator.intake.factory import build_service_for as real_service

    class _Service:
        """The real file service, with the metered client the extraction is handed."""

        def __init__(self, uri: str, llm: Any) -> None:
            self.llm = llm
            self._real = real_service(uri, dry_run=True)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr("orchestrator.core.llm.LiteLLMClient", _Priced)
    monkeypatch.setattr(
        "orchestrator.intake.factory.build_service_for", lambda s, *, dry_run, llm: _Service(s, llm)
    )
    monkeypatch.setattr("orchestrator.sdlc.spec_context.attach_repo_context", lambda *_a: None)
    monkeypatch.setattr("orchestrator.intake.cache.analyze_cached", _extract)
    out = await sdlc_plan(str(checkout), source=source, persist_plan=False)
    assert out["cached"] is False
    assert out["spent_usd"] == pytest.approx(0.0246)
    assert out["spent_estimated"] is False
