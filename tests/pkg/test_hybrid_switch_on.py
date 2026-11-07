"""Hybrid search switched on by default (hybrid KG search P6: D14, D16, D33, D34, D35).

Every stage passed its pre-registered bar on the one sealed read (investigate, RCA's prose path, design;
plan and autorun inherit), so hybrid search is the default and each stage keeps an off-switch. With the
``semantic`` extra installed and the pinned bge-small-en-v1.5 files present, that model is the default
meaning leg — the cell the sealed read measured. The first, cold build of a large repository's vectors
takes minutes, so it is announced on stderr before it starts; the brief itself stays byte-identical run to
run.

The rest of the suite runs with hybrid search held off (``tests/conftest.py``), because it pins lexical
behaviour that the off-switch still reaches; the tests here are the ones marked ``hybrid_default``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.evals import localization
from orchestrator.evals.labels import FixSite, Label
from orchestrator.pkg import FactStore, embed
from orchestrator.pkg import hybrid as hybrid_mod
from orchestrator.pkg.embed import MODELS, OnnxEmbedder, StandInEmbedder, default_embedder
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.hybrid import STAGES, HybridRetriever, hybrid_enabled
from orchestrator.pkg.vector_cache import VectorCache
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

_BILLING = '''\
def persist(record, path):
    """Append one row to the accounting ledger."""
    with open(path, "a") as fh:
        fh.write(record)


def render(record):
    return str(record)
'''

TITLE, BODY = "Ledger rows go missing", "Rows written to the accounting ledger are lost after a restart."


@pytest.fixture
def repo(tmp_path: Path) -> tuple[FactStore, Path]:
    (tmp_path / "billing.py").write_text(_BILLING)
    return FactStore(RepoCodeExtractor().extract(tmp_path)), tmp_path


def _place_model_files(base: Path, alias: str = "bge-small-en-v1.5") -> None:
    spec = MODELS[alias]
    directory = spec.directory(base)
    (directory / spec.weights).parent.mkdir(parents=True, exist_ok=True)
    (directory / spec.weights).write_bytes(b"weights")
    (directory / "tokenizer.json").write_text("{}")


# --- the switches -----------------------------------------------------------------------------------------


def test_the_existing_suite_runs_with_hybrid_search_held_off() -> None:
    """Unmarked, so ``tests/conftest.py`` holds it off: the lexical expectations elsewhere stay meaningful."""
    assert not any(hybrid_enabled(stage) for stage in STAGES)


@pytest.mark.hybrid_default
def test_every_stage_that_passed_its_bar_is_on_by_default() -> None:
    assert STAGES == ("investigate", "rca", "design", "plan", "autorun")
    assert all(hybrid_enabled(stage) for stage in STAGES)


@pytest.mark.hybrid_default
def test_one_switch_turns_every_stage_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "0")
    assert not any(hybrid_enabled(stage) for stage in STAGES)


@pytest.mark.hybrid_default
@pytest.mark.parametrize("stage", STAGES)
def test_each_stage_has_its_own_off_switch(stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"SPINE_HYBRID_{stage.upper()}", "0")
    assert not hybrid_enabled(stage)
    assert all(hybrid_enabled(other) for other in STAGES if other != stage)


@pytest.mark.hybrid_default
def test_a_caller_that_names_no_stage_still_never_gets_it() -> None:
    assert not hybrid_enabled(None)
    assert not hybrid_enabled("no-such-stage")


@pytest.mark.hybrid_default
def test_by_default_the_brief_says_how_it_ranked_and_is_identical_run_to_run(
    repo: tuple[FactStore, Path],
) -> None:
    store, root = repo

    def brief() -> str:
        inv = build_investigation(
            TITLE, BODY, store=store, root=root, rank_on_title=True, stage="investigate"
        )
        return render_investigation_md(inv)

    first = brief()
    assert "hybrid search (name, content, graph)" in first
    assert "Retrieval is **lexical**" not in first
    assert brief() == first


# --- the default model (D33) ------------------------------------------------------------------------------


@pytest.mark.hybrid_default
def test_with_the_extra_and_the_pinned_files_the_measured_model_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: True)
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    _place_model_files(tmp_path)
    found = default_embedder()  # constructing it reads no file and verifies nothing yet
    assert isinstance(found, OnnxEmbedder)
    assert found.model_id == "BAAI/bge-small-en-v1.5@5c38ec7c"


@pytest.mark.hybrid_default
def test_without_the_pinned_files_there_is_no_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: True)
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    assert default_embedder() is None
    spec = MODELS["bge-small-en-v1.5"]
    (spec.directory(tmp_path)).mkdir(parents=True)
    (spec.directory(tmp_path) / "tokenizer.json").write_text("{}")  # the weights are still missing
    assert default_embedder() is None


@pytest.mark.hybrid_default
def test_without_the_extra_the_files_alone_are_not_a_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: False)
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    _place_model_files(tmp_path)
    assert default_embedder() is None


@pytest.mark.hybrid_default
@pytest.mark.parametrize("off", ["none", "NONE", " none "])
def test_the_meaning_leg_can_be_turned_off_by_name(
    off: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: True)
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    _place_model_files(tmp_path)
    monkeypatch.setenv("SPINE_EMBED_MODEL", off)
    assert default_embedder() is None


@pytest.mark.hybrid_default
def test_a_named_model_still_wins_over_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: True)
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    _place_model_files(tmp_path)
    monkeypatch.setenv("SPINE_EMBED_MODEL", "stand-in")
    assert isinstance(default_embedder(), StandInEmbedder)


# --- the cold build is announced, never silent (D34) ------------------------------------------------------


def _retriever(store: FactStore, root: Path, cache: VectorCache) -> HybridRetriever:
    return HybridRetriever(store, root, embedder=StandInEmbedder(), vector_cache=cache)


@pytest.mark.hybrid_default
def test_a_cold_build_says_on_stderr_what_it_is_about_to_embed(
    repo: tuple[FactStore, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, root = repo
    monkeypatch.setattr(hybrid_mod, "COLD_NOTICE_MIN", 1)
    cache = VectorCache(tmp_path / "vc")
    hits = _retriever(store, root, cache).search(f"{TITLE}\n{BODY}", title=TITLE)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "embedding 3 symbols with stand-in/hash-64"  # two functions and their module in captured.err
    assert "first run on this code only" in captured.err
    assert "embedded 3 symbols in" in captured.err
    assert hits  # the notice changes nothing about the answer


@pytest.mark.hybrid_default
def test_a_warm_build_and_a_small_one_say_nothing(
    repo: tuple[FactStore, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store, root = repo
    cache = VectorCache(tmp_path / "vc")
    _retriever(store, root, cache).search(TITLE)  # three symbols: below the default threshold
    assert capsys.readouterr().err == ""
    monkeypatch.setattr(hybrid_mod, "COLD_NOTICE_MIN", 1)
    fresh_store = FactStore(RepoCodeExtractor().extract(root))  # a new graph object: the vectors are cached
    _retriever(fresh_store, root, cache).search(TITLE)
    assert capsys.readouterr().err == ""


# --- the localization gate measures the default path (D16) -----------------------------------------------


@pytest.mark.hybrid_default
def test_the_localization_score_runs_the_investigate_stage(
    repo: tuple[FactStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root = repo
    batch = RepoCodeExtractor().extract(root)
    seen: dict[str, object] = {}
    real = build_investigation

    def spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("orchestrator.sdlc.investigate.build_investigation", spy)
    monkeypatch.setattr("orchestrator.pkg.persistence.load_or_extract", lambda _root: batch)
    label = Label(
        repo="x", issue="x#1", title=TITLE, fix_commit="0" * 40, fix_sites=(FixSite("billing.py"),), body=BODY
    )
    result = localization.score_label(label, root)
    assert seen["stage"] == "investigate"
    assert result.rank == 1
