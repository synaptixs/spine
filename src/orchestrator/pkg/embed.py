"""Text embedders for hybrid search's meaning leg (hybrid KG search, D2/D16/D17): local, pinned, lazy.

The ``semantic`` extra (onnxruntime, tokenizers, safetensors, numpy) runs a small open-weights model on the
CPU. No code leaves the machine, nothing is paid for, and no model is ever downloaded here: the files are a
one-time, approved download into :func:`model_dir`, and each one is checked against the sha256 pinned in
:data:`MODELS` before it is used — a file that does not match is refused, never loaded. The three pins are the
pre-registered shortlist (``~/plans/hybrid-kg-search-p0/shortlist.md``); a model outside it is not offered,
because the grid measured only these.

Nothing heavy is imported until a model is asked to embed, so importing this module (or ``pkg.hybrid``) costs
the base install nothing, and ``understand``/``state`` never reach it (invariant 2). No model is used unless
one is configured or installed: :func:`default_embedder` reads ``SPINE_EMBED_MODEL`` (an alias from
:data:`MODELS`, or ``none``); unset, it uses :data:`DEFAULT_MODEL` only when the extra and that model's pinned
files are both present, and otherwise returns ``None``, so the hybrid core runs three legs and says so.

Two adapters, because the shortlist has two shapes:

* :class:`OnnxEmbedder` — a transformer encoder exported to ONNX (``bge-small-en-v1.5``: CLS pooling;
  ``jina-embeddings-v2-base-code``: mean pooling). The ONNX export runs no code from the model's repository,
  which is why jina is loaded this way and never through PyTorch's ``trust_remote_code``.
* :class:`StaticEmbedder` — a model2vec-format static model (``potion-retrieval-32M``): a token → vector
  table, averaged. numpy only, no network graph to run.

Both truncate to the model's own token limit (pre-registration §3), L2-normalise, and return vectors in input
order. :class:`StandInEmbedder` is a deterministic, hash-based stand-in (alias ``stand-in``) so ``ci.yml``
tests the whole leg offline; it has no notion of meaning beyond shared words and is never a grid cell.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The ``semantic`` extra's modules; all four must import for a real model to run.
_EXTRA_MODULES = ("onnxruntime", "tokenizers", "safetensors", "numpy")

#: Texts per ONNX run. Sorted by length first, so a batch pads to its own longest text, not the corpus's.
_BATCH = 32


class ModelError(RuntimeError):
    """A configured model cannot be used: unknown, not installed, not downloaded, or not the pinned file."""


@dataclass(frozen=True)
class ModelSpec:
    """One pinned model: where its files live and how its vectors are pooled.

    ``weights_sha256`` pins the weights file; ``vocab_sha256`` pins ``tokenizer.json`` (vocabulary, rules).
    """

    alias: str
    repo: str
    revision: str
    kind: str  # "onnx" | "static"
    weights: str  # path of the weights file inside the model directory
    weights_sha256: str
    vocab_sha256: str
    pooling: str  # "cls" | "mean" | "static"
    max_tokens: int
    dim: int

    @property
    def model_id(self) -> str:
        """What a brief prints: the repository and the revision it was pinned at."""
        return f"{self.repo}@{self.revision[:8]}"

    def directory(self, base: Path) -> Path:
        return base / self.repo.replace("/", "__") / self.revision


#: The pre-registered shortlist (§4 of the pre-registration), verified at the source in P0.
MODELS: dict[str, ModelSpec] = {
    spec.alias: spec
    for spec in (
        ModelSpec(
            alias="potion-retrieval-32M",
            repo="minishlab/potion-retrieval-32M",
            revision="6fc8051fab2a1e0ee76689cf08c853792ac285e7",
            kind="static",
            weights="model.safetensors",
            weights_sha256="07609e5bd33aad37900b3fd62f4ec96f6daec88ca4d46b9d8b928bfababf6ea0",
            vocab_sha256="7d75cbc54318138807c401b0f0c9721117c628b39de8e8e0edb6cb17e0ee7d18",
            pooling="static",
            max_tokens=512,  # model2vec's own default; the table itself has no position limit
            dim=512,
        ),
        ModelSpec(
            alias="bge-small-en-v1.5",
            repo="BAAI/bge-small-en-v1.5",
            revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
            kind="onnx",
            weights="onnx/model.onnx",
            weights_sha256="828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
            vocab_sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
            pooling="cls",
            max_tokens=512,
            dim=384,
        ),
        ModelSpec(
            alias="jina-embeddings-v2-base-code",
            repo="jinaai/jina-embeddings-v2-base-code",
            revision="516f4baf13dec4ddddda8631e019b5737c8bc250",
            kind="onnx",
            weights="onnx/model_quantized.onnx",
            weights_sha256="ed45870251c9f0cf656e78aab0d37a23489066df8a222bb1c8caf8a45f2cb16d",
            vocab_sha256="b01c78a902aa4facb2f47f95449f48e2f7bbfea5d2472ee2f6ce92323c6f86e5",
            pooling="mean",
            max_tokens=8192,
            dim=768,
        ),
    )
}

STAND_IN = "stand-in"


def model_dir() -> Path:
    """``$SPINE_MODEL_DIR``, else ``~/.cache/spine-models`` — where the approved download put the files."""
    override = os.getenv("SPINE_MODEL_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".cache" / "spine-models"


def semantic_available() -> bool:
    """Whether the ``semantic`` extra is installed. Probed with ``find_spec``: nothing is imported."""
    return all(importlib.util.find_spec(m) is not None for m in _EXTRA_MODULES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(spec: ModelSpec, base: Path) -> Path:
    """The model's directory, once its weights and tokenizer match their pins; else :class:`ModelError`."""
    directory = spec.directory(base)
    for name, pinned in ((spec.weights, spec.weights_sha256), ("tokenizer.json", spec.vocab_sha256)):
        path = directory / name
        if not path.is_file():
            raise ModelError(
                f"{spec.repo} ({spec.alias}) not found: expected {path}. Spine never downloads a model; "
                f"fetch revision {spec.revision} into {base} first."
            )
        actual = sha256_file(path)
        if actual != pinned:
            raise ModelError(f"{path}: sha256 {actual} is not the pinned {pinned}; refusing to load it")
    return directory


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class StandInEmbedder:
    """Feature hashing over :func:`retrieval.word_tokens`: deterministic, offline, and blind to meaning."""

    dim = 64

    @property
    def model_id(self) -> str:
        return f"stand-in/hash-{self.dim}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(f"spine-stand-in:hash:{self.dim}:v1".encode()).hexdigest()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from orchestrator.pkg.retrieval import word_tokens

        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in word_tokens(text):
                h = hashlib.sha256(token.encode()).digest()
                vec[int.from_bytes(h[:4], "big") % self.dim] += 1.0 if h[4] & 1 else -1.0
            out.append(_l2(vec))
        return out


class _Pinned:
    """What both real adapters share: the spec, the directory, and a load that happens once, on first use."""

    def __init__(self, spec: ModelSpec, base: Path | None = None) -> None:
        self.spec = spec
        self._base = base if base is not None else model_dir()
        self._loaded = False

    @property
    def model_id(self) -> str:
        return self.spec.model_id

    @property
    def sha256(self) -> str:
        return self.spec.weights_sha256

    def _tokenizer(self, directory: Path) -> Any:
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(directory / "tokenizer.json"))
        tok.no_padding()
        tok.enable_truncation(max_length=self.spec.max_tokens)
        return tok


class StaticEmbedder(_Pinned):
    """A model2vec static model: the mean of the text's token rows (unknown tokens dropped), unit length."""

    def _load(self) -> None:
        if self._loaded:
            return
        directory = verify(self.spec, self._base)
        from safetensors.numpy import load_file

        self._tok = self._tokenizer(directory)
        unk = json.loads(self._tok.to_str()).get("model", {}).get("unk_token")
        self._unk = self._tok.token_to_id(unk) if unk else None
        self._table = load_file(str(directory / self.spec.weights))["embeddings"]
        self._loaded = True

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import numpy as np

        self._load()
        out: list[list[float]] = []
        for enc in self._tok.encode_batch(list(texts), add_special_tokens=False):
            ids = [i for i in enc.ids if i != self._unk]
            if not ids:
                out.append([0.0] * self._table.shape[1])
                continue
            vec = self._table[np.asarray(ids)].mean(axis=0)
            norm = float(np.linalg.norm(vec))
            out.append((vec / norm if norm else vec).astype(np.float32).tolist())
        return out


class OnnxEmbedder(_Pinned):
    """A transformer encoder in ONNX: CLS or attention-masked mean of the last hidden state, unit length.

    ``session`` and ``tokenizer`` can be injected (tests); otherwise the pinned files are verified and loaded
    on the first :meth:`embed`, on the CPU execution provider only — no CUDA, by D10.
    """

    def __init__(
        self, spec: ModelSpec, base: Path | None = None, *, session: Any = None, tokenizer: Any = None
    ) -> None:
        super().__init__(spec, base)
        self._session = session
        self._tok = tokenizer

    def _load(self) -> None:
        if self._loaded:
            return
        directory = verify(self.spec, self._base)
        if self._tok is None:
            self._tok = self._tokenizer(directory)
        if self._session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            self._session = ort.InferenceSession(
                str(directory / self.spec.weights), sess_options=options, providers=["CPUExecutionProvider"]
            )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._loaded = True

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import numpy as np

        self._load()
        encodings = self._tok.encode_batch(list(texts))
        order = sorted(range(len(encodings)), key=lambda i: (len(encodings[i].ids), i))
        out: list[list[float]] = [[] for _ in encodings]
        for start in range(0, len(order), _BATCH):
            chunk = order[start : start + _BATCH]
            width = max(len(encodings[i].ids) for i in chunk)
            ids = np.zeros((len(chunk), width), dtype=np.int64)
            mask = np.zeros((len(chunk), width), dtype=np.int64)
            for row, i in enumerate(chunk):
                n = len(encodings[i].ids)
                ids[row, :n] = encodings[i].ids
                mask[row, :n] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = np.asarray(self._session.run(None, feed)[0], dtype=np.float32)
            if self.spec.pooling == "cls":
                pooled = hidden[:, 0, :]
            else:
                weights = mask[:, :, None].astype(np.float32)
                pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1.0)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = np.where(norms > 0, pooled / np.where(norms > 0, norms, 1.0), pooled)
            for row, i in enumerate(chunk):
                out[i] = pooled[row].astype(np.float32).tolist()
        return out


def embedder_for(alias: str, *, base: Path | None = None) -> StandInEmbedder | StaticEmbedder | OnnxEmbedder:
    """The embedder for one pinned alias (or ``stand-in``). Constructing it reads no file."""
    if alias == STAND_IN:
        return StandInEmbedder()
    spec = MODELS.get(alias)
    if spec is None:
        known = ", ".join([*sorted(MODELS), STAND_IN])
        raise ModelError(f"unknown embedding model {alias!r}; pinned models: {known}")
    if not semantic_available():
        raise ModelError(
            f"{alias} needs the semantic extra: pip install 'synaptixs-spine[semantic]' (onnxruntime, "
            "tokenizers, safetensors, numpy)"
        )
    if spec.kind == "static":
        return StaticEmbedder(spec, base)
    return OnnxEmbedder(spec, base)


#: The model the sealed read measured (hybrid KG search P4 winner, P5), used when none is named (D33).
DEFAULT_MODEL = "bge-small-en-v1.5"

#: ``SPINE_EMBED_MODEL=none`` turns the meaning leg off even where the default model is installed.
NO_MODEL = "none"


def _installed(spec: ModelSpec, base: Path) -> bool:
    """Whether the pinned files are where :func:`verify` will look.

    Presence only: verifying hashes ~130 MB, so it waits for the first :meth:`embed`, which refuses a file
    that does not match.
    """
    directory = spec.directory(base)
    return (directory / spec.weights).is_file() and (directory / "tokenizer.json").is_file()


def default_embedder() -> StandInEmbedder | StaticEmbedder | OnnxEmbedder | None:
    """The model hybrid search's meaning leg uses, or ``None`` (then three legs run and the brief says so).

    ``SPINE_EMBED_MODEL`` names one (or ``none``); unset, it is :data:`DEFAULT_MODEL` when the ``semantic``
    extra is installed and that model's pinned files are in :func:`model_dir` — the cell the sealed read
    measured — and no model otherwise. Nothing is downloaded and nothing is read here.
    """
    alias = os.getenv("SPINE_EMBED_MODEL", "").strip()
    if alias.lower() == NO_MODEL:
        return None
    if alias:
        return embedder_for(alias)
    if semantic_available() and _installed(MODELS[DEFAULT_MODEL], model_dir()):
        return embedder_for(DEFAULT_MODEL)
    return None


__all__ = [
    "DEFAULT_MODEL",
    "MODELS",
    "NO_MODEL",
    "STAND_IN",
    "ModelError",
    "ModelSpec",
    "OnnxEmbedder",
    "StandInEmbedder",
    "StaticEmbedder",
    "default_embedder",
    "embedder_for",
    "model_dir",
    "semantic_available",
    "sha256_file",
    "verify",
]
