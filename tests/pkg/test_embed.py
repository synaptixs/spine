"""The ``semantic`` extra's embedders (hybrid KG search, D2/D16/D17): pinned, local, lazy.

What these tests pin: the three shortlisted models are pinned by repository, revision and file sha256, and a
file that does not match is refused, never used; nothing heavy is imported until a model is asked to embed; no
model is used unless one is configured; the static and ONNX adapters pool the way their model cards say (mean
of token rows, CLS, masked mean) and return unit vectors in input order; and the stand-in that keeps
``ci.yml`` offline is deterministic. The real models are exercised only where they are already on disk (never
downloaded here).
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import embed
from orchestrator.pkg.embed import (
    MODELS,
    ModelError,
    ModelSpec,
    OnnxEmbedder,
    StandInEmbedder,
    StaticEmbedder,
    default_embedder,
    embedder_for,
)


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- pins
# -------------------------------------------------------------------------------------------------------


def test_the_three_shortlisted_models_are_pinned_by_revision_and_sha256() -> None:
    assert sorted(MODELS) == ["bge-small-en-v1.5", "jina-embeddings-v2-base-code", "potion-retrieval-32M"]
    bge, jina, potion = (MODELS[k] for k in sorted(MODELS))
    assert (bge.repo, bge.revision, bge.weights, bge.pooling, bge.dim) == (
        "BAAI/bge-small-en-v1.5",
        "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "onnx/model.onnx",
        "cls",
        384,
    )
    assert bge.weights_sha256 == "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"
    assert (jina.repo, jina.revision, jina.weights, jina.pooling, jina.dim) == (
        "jinaai/jina-embeddings-v2-base-code",
        "516f4baf13dec4ddddda8631e019b5737c8bc250",
        "onnx/model_quantized.onnx",
        "mean",
        768,
    )
    assert jina.weights_sha256 == "ed45870251c9f0cf656e78aab0d37a23489066df8a222bb1c8caf8a45f2cb16d"
    assert (potion.repo, potion.revision, potion.weights, potion.kind, potion.dim) == (
        "minishlab/potion-retrieval-32M",
        "6fc8051fab2a1e0ee76689cf08c853792ac285e7",
        "model.safetensors",
        "static",
        512,
    )
    assert potion.weights_sha256 == "07609e5bd33aad37900b3fd62f4ec96f6daec88ca4d46b9d8b928bfababf6ea0"
    for spec in MODELS.values():
        assert len(spec.vocab_sha256) == 64
        # The id a brief prints: the repository and the revision it was pinned at.
        assert spec.model_id == f"{spec.repo}@{spec.revision[:8]}"


def test_a_model_lives_under_its_repository_and_revision(tmp_path: Path) -> None:
    spec = MODELS["bge-small-en-v1.5"]
    assert spec.directory(tmp_path) == tmp_path / "BAAI__bge-small-en-v1.5" / spec.revision


def _fake_model(tmp_path: Path, spec: ModelSpec, weights: bytes = b"w", tokenizer: bytes = b"t") -> Path:
    d = spec.directory(tmp_path)
    (d / spec.weights).parent.mkdir(parents=True, exist_ok=True)
    (d / spec.weights).write_bytes(weights)
    (d / "tokenizer.json").write_bytes(tokenizer)
    return d


def _spec_for(tmp_path: Path, weights_bytes: bytes, tokenizer_bytes: bytes, **kw: Any) -> ModelSpec:
    base = dict(
        alias="fixture",
        repo="test/fixture",
        revision="0" * 40,
        kind="static",
        weights="model.safetensors",
        weights_sha256=hashlib.sha256(weights_bytes).hexdigest(),
        vocab_sha256=hashlib.sha256(tokenizer_bytes).hexdigest(),
        pooling="static",
        max_tokens=8,
        dim=3,
    )
    base.update(kw)
    return ModelSpec(**base)  # type: ignore[arg-type]


def test_a_file_whose_sha256_does_not_match_is_refused(tmp_path: Path) -> None:
    spec = _spec_for(tmp_path, b"good", b"tok")
    _fake_model(tmp_path, spec, weights=b"tampered", tokenizer=b"tok")
    with pytest.raises(ModelError, match="sha256"):
        embed.verify(spec, tmp_path)
    _fake_model(tmp_path, spec, weights=b"good", tokenizer=b"other")
    with pytest.raises(ModelError, match="tokenizer.json"):
        embed.verify(spec, tmp_path)
    _fake_model(tmp_path, spec, weights=b"good", tokenizer=b"tok")
    assert embed.verify(spec, tmp_path) == spec.directory(tmp_path)


def test_a_missing_model_says_where_it_looked_and_never_downloads(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="not found") as err:
        embed.verify(MODELS["bge-small-en-v1.5"], tmp_path)
    assert str(tmp_path) in str(err.value)


# --- configuration
# ------------------------------------------------------------------------------------------------


def test_no_model_is_used_unless_one_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPINE_EMBED_MODEL", raising=False)
    assert default_embedder() is None
    monkeypatch.setenv("SPINE_EMBED_MODEL", "  ")
    assert default_embedder() is None


def test_a_configured_model_is_loaded_lazily_from_the_model_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPINE_EMBED_MODEL", "bge-small-en-v1.5")
    monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
    found = default_embedder()  # nothing on disk yet: constructing it reads no file
    assert isinstance(found, OnnxEmbedder)
    assert found.model_id == "BAAI/bge-small-en-v1.5@5c38ec7c"
    assert found.sha256 == MODELS["bge-small-en-v1.5"].weights_sha256
    with pytest.raises(ModelError, match="not found"):
        found.embed(["x"])


def test_an_unknown_model_name_is_refused_with_the_known_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_EMBED_MODEL", "all-MiniLM-L6-v2")
    with pytest.raises(ModelError, match="potion-retrieval-32M"):
        default_embedder()


def test_a_configured_model_without_the_extra_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embed, "semantic_available", lambda: False)
    with pytest.raises(ModelError, match=r"synaptixs-spine\[semantic\]"):
        embedder_for("potion-retrieval-32M")


def test_the_model_dir_defaults_to_the_spine_models_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPINE_MODEL_DIR", raising=False)
    assert embed.model_dir() == Path.home() / ".cache" / "spine-models"


def test_importing_the_module_loads_no_model_runtime() -> None:
    code = (
        "import sys, orchestrator.pkg.embed, orchestrator.pkg.hybrid\n"
        "heavy = [m for m in ('onnxruntime', 'tokenizers', 'safetensors', 'numpy') if m in sys.modules]\n"
        "print(heavy)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- the stand-in
# -------------------------------------------------------------------------------------------------


def test_the_stand_in_is_deterministic_unit_length_and_word_based() -> None:
    a, b = StandInEmbedder(), StandInEmbedder()
    texts = ["append a row to the ledger", "ledger rows go missing", "render the page"]
    va, vb = a.embed(texts), b.embed(texts)
    assert va == vb
    assert all(abs(_norm(v) - 1.0) < 1e-9 for v in va)
    assert len(va[0]) == a.dim
    assert _dot(va[0], va[1]) > _dot(va[0], va[2])
    assert a.embed([""]) == [[0.0] * a.dim]
    assert a.model_id == "stand-in/hash-64"
    assert a.sha256 == hashlib.sha256(b"spine-stand-in:hash:64:v1").hexdigest()


# --- the static adapter, on a fixture model built here
# ------------------------------------------------------------


def _static_fixture(tmp_path: Path) -> ModelSpec:
    np = pytest.importorskip("numpy")
    tokenizers = pytest.importorskip("tokenizers")
    safetensors_numpy = pytest.importorskip("safetensors.numpy")
    vocab = {"[UNK]": 0, "ledger": 1, "row": 2, "page": 3}
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok_path = tmp_path / "tok.json"
    tok.save(str(tok_path))
    rows = np.array([[9.0, 9.0, 9.0], [3.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    w_path = tmp_path / "w.safetensors"
    safetensors_numpy.save_file({"embeddings": rows}, str(w_path))
    weights, tokenizer = w_path.read_bytes(), tok_path.read_bytes()
    spec = _spec_for(tmp_path, weights, tokenizer)
    _fake_model(tmp_path, spec, weights=weights, tokenizer=tokenizer)
    return spec


def test_the_static_adapter_averages_token_rows_drops_unknowns_and_normalises(tmp_path: Path) -> None:
    spec = _static_fixture(tmp_path)
    model = StaticEmbedder(spec, tmp_path)
    ledger, mixed, unknown_only = model.embed(["ledger", "ledger row zzz", "zzz"])
    assert ledger == pytest.approx([1.0, 0.0, 0.0])
    # mean of (3,0,0) and (0,4,0) = (1.5, 2, 0), unit length; the unknown word's row is never used
    assert mixed == pytest.approx([0.6, 0.8, 0.0], abs=1e-6)
    assert unknown_only == [0.0, 0.0, 0.0]


def test_the_static_adapter_truncates_at_the_models_token_limit(tmp_path: Path) -> None:
    spec = _static_fixture(tmp_path)
    model = StaticEmbedder(spec, tmp_path)
    [v] = model.embed([" ".join(["ledger"] * 8 + ["page"] * 8)])  # max_tokens = 8: only ledgers are read
    assert v == pytest.approx([1.0, 0.0, 0.0])


# --- the ONNX adapter, on a fake session (the pooling is ours; the network is the model's)
# ----------------------


class _FakeSession:
    """Echoes each token id into the hidden state so pooling can be checked by hand."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self.calls: list[dict[str, Any]] = []

    def get_inputs(self) -> list[Any]:
        return [type("I", (), {"name": n})() for n in self.names]

    def run(self, _outputs: Any, feed: dict[str, Any]) -> list[Any]:
        np = pytest.importorskip("numpy")
        self.calls.append(feed)
        ids = feed["input_ids"].astype(np.float32)
        hidden = np.stack([ids, np.ones_like(ids), -ids], axis=-1)  # (batch, seq, 3)
        return [hidden]


def _onnx_fixture(tmp_path: Path, pooling: str, names: tuple[str, ...]) -> tuple[OnnxEmbedder, _FakeSession]:
    tokenizers = pytest.importorskip("tokenizers")
    vocab = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "a": 3, "b": 4, "c": 5}
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.post_processor = tokenizers.processors.TemplateProcessing(
        single="[CLS] $A", special_tokens=[("[CLS]", 2)]
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    tok_path = tmp_path / "tok.json"
    tok.save(str(tok_path))
    tokenizer = tok_path.read_bytes()
    spec = _spec_for(
        tmp_path, b"onnx", tokenizer, kind="onnx", weights="onnx/model.onnx", pooling=pooling, max_tokens=3
    )
    _fake_model(tmp_path, spec, weights=b"onnx", tokenizer=tokenizer)
    session = _FakeSession(names)
    return OnnxEmbedder(spec, tmp_path, session=session), session


def test_cls_pooling_takes_the_first_token_and_feeds_token_types_only_when_asked(tmp_path: Path) -> None:
    model, session = _onnx_fixture(tmp_path, "cls", ("input_ids", "attention_mask", "token_type_ids"))
    [v] = model.embed(["a b"])
    s = math.sqrt(2 * 2 + 1 + 2 * 2)  # the [CLS] row: (2, 1, -2)
    assert v == pytest.approx([2 / s, 1 / s, -2 / s])
    assert set(session.calls[0]) == {"input_ids", "attention_mask", "token_type_ids"}
    model2, session2 = _onnx_fixture(tmp_path / "x", "cls", ("input_ids", "attention_mask"))
    model2.embed(["a"])
    assert set(session2.calls[0]) == {"input_ids", "attention_mask"}


def test_mean_pooling_ignores_padding_truncates_and_keeps_input_order(tmp_path: Path) -> None:
    model, session = _onnx_fixture(tmp_path, "mean", ("input_ids", "attention_mask"))
    long_, short = model.embed(["a b c c c", "a"])  # max_tokens = 3: [CLS] a b
    m = (2 + 3 + 4) / 3
    s = math.sqrt(m * m + 1 + m * m)
    assert long_ == pytest.approx([m / s, 1 / s, -m / s])
    m2 = (2 + 3) / 2  # [CLS] a, the pad row is masked out
    s2 = math.sqrt(m2 * m2 + 1 + m2 * m2)
    assert short == pytest.approx([m2 / s2, 1 / s2, -m2 / s2])
    assert session.calls[0]["input_ids"].shape[1] == 3


# --- the real models, only where they are already on disk
# ---------------------------------------------------------


def _on_disk(alias: str) -> ModelSpec:
    if not embed.semantic_available():
        pytest.skip("the semantic extra is not installed")
    spec = MODELS[alias]
    if not (spec.directory(embed.model_dir()) / spec.weights).is_file():
        pytest.skip(f"{alias} is not downloaded (this test never downloads)")
    return spec


@pytest.mark.parametrize("alias", sorted(MODELS))
def test_a_real_model_embeds_unit_vectors_that_rank_a_related_text_first(alias: str) -> None:
    spec = _on_disk(alias)
    model = embedder_for(alias)
    query, near, far = model.embed(
        [
            "parse the date string into a datetime",
            "def parse_date(value): return datetime.strptime(value)",
            "class Colour: red = 1",
        ]
    )
    assert len(query) == spec.dim
    assert abs(_norm(query) - 1.0) < 1e-4
    assert _dot(query, near) > _dot(query, far)
    assert json.dumps(model.embed(["same text"])) == json.dumps(model.embed(["same text"]))
