"""The meaning leg of hybrid search (hybrid KG search, D9/D17): an embedder's cosine ranking, fused with the
rest.

What these tests pin: with an embedder the retriever runs four legs and names the model; symbol vectors come
from the content-keyed cache, so a second build embeds nothing; a vector is the same whether it was just
computed or read back (both pass through float32), so a cold and a warm run rank the same; similarities are
rounded to 4 places before ranking and ties go to the node id; the numpy path and the stdlib path rank alike;
and the fixture's ranks are pinned literally, so the Linux and macOS jobs compare the same list (the
cross-platform rank test).
"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg import hybrid as hybrid_mod
from orchestrator.pkg.embed import StandInEmbedder
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.hybrid import LEG_MEANING, HybridRetriever
from orchestrator.pkg.vector_cache import VectorCache

_SHOP = '''\
def add_to_cart(cart, item):
    """Put one item in the shopping cart."""
    cart.append(item)
    recount(cart)


def recount(cart):
    return len(cart)


def remove_from_cart(cart, item):
    """Take an item out of the shopping cart."""
    cart.remove(item)


def send_invoice(order):
    """Email the invoice for a paid order."""
    return mail(order)


def mail(order):
    return str(order)


def parse_date(value):
    """Turn a date string into a datetime."""
    return value
'''


class _Counting(StandInEmbedder):
    """The stand-in, counting how many texts it was asked to embed."""

    def __init__(self) -> None:
        super().__init__()
        self.seen = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.seen += len(texts)
        return super().embed(texts)


@pytest.fixture
def shop(tmp_path: Path) -> tuple[FactStore, Path]:
    (tmp_path / "shop.py").write_text(_SHOP)
    return FactStore(RepoCodeExtractor().extract(tmp_path)), tmp_path


def _short(ids: Sequence[str]) -> list[str]:
    return [i.rsplit(".", 1)[-1] for i in ids]


def test_with_an_embedder_four_legs_run_and_the_model_is_named(
    shop: tuple[FactStore, Path], tmp_path: Path
) -> None:
    store, root = shop
    retriever = HybridRetriever(
        store, root, embedder=StandInEmbedder(), vector_cache=VectorCache(tmp_path / "v")
    )
    assert retriever.legs_used == ("name", "content", "meaning", "graph")
    assert retriever.model == "stand-in/hash-64"
    hits = retriever.search("the shopping cart loses an item")
    assert any(LEG_MEANING in h.legs for h in hits)
    assert "meaning (#" in next(h.found_by for h in hits if LEG_MEANING in h.legs)
    assert HybridRetriever(store, root).model is None


def test_symbol_vectors_come_from_the_cache_on_the_second_build(
    shop: tuple[FactStore, Path], tmp_path: Path
) -> None:
    store, root = shop
    cache = VectorCache(tmp_path / "v")
    cold = _Counting()
    first = [
        h.node.id for h in HybridRetriever(store, root, embedder=cold, vector_cache=cache).search("cart item")
    ]
    symbols = cold.seen - 1  # every symbol text, then the query
    assert symbols >= 6
    # A new process: a graph loaded afresh, so nothing is remembered in memory and the vectors must come from
    # disk.
    again = FactStore(RepoCodeExtractor().extract(root))
    warm = _Counting()
    second = [
        h.node.id for h in HybridRetriever(again, root, embedder=warm, vector_cache=cache).search("cart item")
    ]
    assert warm.seen == 1  # only the query
    assert second == first  # the cached float32 vectors rank exactly as the fresh ones did


def test_a_different_model_never_reads_another_models_vectors(
    shop: tuple[FactStore, Path], tmp_path: Path
) -> None:
    store, root = shop
    cache = VectorCache(tmp_path / "v")
    HybridRetriever(store, root, embedder=_Counting(), vector_cache=cache).search("cart")

    class Other(_Counting):
        @property
        def sha256(self) -> str:
            return "f" * 64

    other = Other()
    HybridRetriever(store, root, embedder=other, vector_cache=cache).search("cart")
    assert other.seen > 1


@pytest.mark.parametrize("numpy_path", [True, False])
def test_meaning_ranks_by_rounded_cosine_then_by_id(numpy_path: bool) -> None:
    """``b`` is 0.600022 from the query unrounded, ``a`` exactly 0.6: apart in float32, equal to 4 places.
    Rounded before ranking they tie and the id decides (``a`` first); unrounded, ``b`` would win. ``c`` is
    orthogonal (score 0) and dropped."""
    if numpy_path:
        pytest.importorskip("numpy")
    hybrid_mod._MeaningIndex.use_numpy = numpy_path
    try:
        ranked = hybrid_mod._MeaningIndex.build(["b", "a", "c"], [[0.60002, 0.79998], [0.6, 0.8], [0.0, 1.0]])
    finally:
        hybrid_mod._MeaningIndex.use_numpy = True
    assert ranked.rank([1.0, 0.0], pool=10) == ["a", "b"]


def test_fresh_and_cached_vectors_are_the_same_float32_numbers(
    shop: tuple[FactStore, Path], tmp_path: Path
) -> None:
    """The stdlib path ranks on the stored rows as they are: a fresh float64 vector must be cut to float32
    like a cached one, or a cold run and a warm run compare different numbers."""
    store, root = shop
    hybrid_mod._MeaningIndex.use_numpy = False
    try:
        retriever = HybridRetriever(
            store, root, embedder=StandInEmbedder(), vector_cache=VectorCache(tmp_path / "v")
        )
        retriever.search("cart item")
    finally:
        hybrid_mod._MeaningIndex.use_numpy = True
    rows = retriever._index._rows  # type: ignore[union-attr]
    assert rows and all(row == array("f", row).tolist() for row in rows)
    assert any(row != [round(x, 3) for x in row] for row in rows)  # not trivially representable numbers


def test_the_numpy_and_stdlib_paths_rank_alike(shop: tuple[FactStore, Path], tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    store, root = shop
    cache = VectorCache(tmp_path / "v")
    with_numpy = [
        h.node.id
        for h in HybridRetriever(store, root, embedder=StandInEmbedder(), vector_cache=cache).search(
            "invoice for the order"
        )
    ]
    hybrid_mod._MeaningIndex.use_numpy = False
    try:
        stdlib = [
            h.node.id
            for h in HybridRetriever(store, root, embedder=StandInEmbedder(), vector_cache=cache).search(
                "invoice for the order"
            )
        ]
    finally:
        hybrid_mod._MeaningIndex.use_numpy = True
    assert stdlib == with_numpy


def test_the_fixture_ranks_are_the_same_on_every_platform(
    shop: tuple[FactStore, Path], tmp_path: Path
) -> None:
    """D17's cross-platform test: this literal list is checked by the Linux job and the macOS job alike."""
    store, root = shop
    retriever = HybridRetriever(
        store, root, embedder=StandInEmbedder(), vector_cache=VectorCache(tmp_path / "v")
    )
    hits = retriever.search("the shopping cart loses an item")
    meaning = sorted((h for h in hits if LEG_MEANING in h.legs), key=lambda h: h.legs[LEG_MEANING])
    assert _short([h.node.id for h in meaning]) == GOLDEN_MEANING
    assert _short([h.node.id for h in hits]) == GOLDEN_FUSED


# Pinned from the first green run (macOS arm64, numpy and stdlib paths alike). A change here is a ranking
# change: say why in the commit, never regenerate it to make a red run green.
GOLDEN_MEANING = ["add_to_cart", "py:shop", "remove_from_cart", "recount"]
GOLDEN_FUSED = ["add_to_cart", "remove_from_cart", "py:shop", "recount", "mail", "send_invoice", "parse_date"]
