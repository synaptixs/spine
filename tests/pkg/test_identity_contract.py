"""The parse contract on `Provenance.__str__` and node ids, pinned.

Both formats are **parsed back apart** at twenty call sites, none of which raises when the shape
changes — they just return the wrong string. This file exists so the next person who adds a
segment finds out from a failing test rather than from a blast radius computed against a path
that does not exist.

Every site listed here was found by grep on 2026-08-25 and is named, so a reader can check the
list is still complete rather than trusting that it was once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.scoping import (
    ScopeError,
    merge_repos,
    scope_batch,
    scope_id,
    unscope_id,
    validate_repo_key,
)

# ---- the provenance contract ----------------------------------------------
#
# `str(Provenance)` is `file:line`, exactly one separator, file first. These six sites recover
# the file with `split(":", 1)[0]`:
#
#   sdlc/design.py:130          sdlc/builddoc.py:953, 1163
#   sdlc/autorun.py:788         sdlc/criteria_binding.py:224
#                               sdlc/evidence.py:73
#
# The last two are the ones that would hurt. `evidence.py` is the Evidence artifact's own file
# accessor, and `criteria_binding.py` decides whether an acceptance criterion is bound to a
# landing site — a criterion binding against a repo name binds against nothing and passes.


def test_str_is_file_then_line_with_one_separator() -> None:
    assert str(Provenance("src/pkg/mod.py", 42)) == "src/pkg/mod.py:42"


def test_the_six_call_sites_recover_the_file_path() -> None:
    """`where.split(":", 1)[0]` must be the file, for every provenance we can build."""
    for prov in (
        Provenance("a.py", 1),
        Provenance("src/deep/path/mod.py", 999, end_line=1200),
        Provenance("a.py", 1, repo="svc-a"),  # repo set — must NOT reach the string
    ):
        assert str(prov).split(":", 1)[0] == prov.file


def test_the_repo_never_enters_the_string_form() -> None:
    """The whole point of `qualified()` existing separately."""
    prov = Provenance("src/mod.py", 7, repo="billing")
    assert str(prov) == "src/mod.py:7"
    assert "billing" not in str(prov)
    assert prov.qualified() == "billing:src/mod.py:7"


def test_qualified_degrades_to_the_plain_form_without_a_repo() -> None:
    assert Provenance("a.py", 3).qualified() == "a.py:3"


# ---- the id contract -------------------------------------------------------
#
# Fourteen sites recover a name or body with `id.partition(":")` / `split(":", 1)[-1]`:
# knowledge/{areas,insights,current_state}, pkg/{docs,verify,invention,cpp_extractor},
# and five in pkg/import_link.py. So the scope goes AFTER the language prefix, never before.


@pytest.mark.parametrize(
    ("raw", "scoped"),
    [
        ("py:shop.cart.Cart", "py:svc-a@shop.cart.Cart"),
        ("cpp:Namespace::func", "cpp:svc-a@Namespace::func"),
        ("ts:app/handler.Handler", "ts:svc-a@app/handler.Handler"),
        ("go:shop.Cart.Add", "go:svc-a@shop.Cart.Add"),
    ],
)
def test_the_scope_goes_after_the_language_prefix(raw: str, scoped: str) -> None:
    assert scope_id(raw, "svc-a") == scoped
    assert scoped.partition(":")[0] == raw.partition(":")[0], "language prefix must survive"


def test_scoping_round_trips(raw: str = "py:shop.cart.Cart") -> None:
    assert unscope_id(scope_id(raw, "svc-a")) == ("svc-a", raw)


def test_scoping_is_idempotent() -> None:
    once = scope_id("py:shop.cart.Cart", "svc-a")
    assert scope_id(once, "svc-a") == once


def test_an_unscoped_id_reports_no_repo() -> None:
    assert unscope_id("py:shop.cart.Cart") == ("", "py:shop.cart.Cart")


def test_an_npm_scoped_typescript_id_is_not_read_as_scoped() -> None:
    """`ts:@vue/runtime-core:h` starts with the separator and carries no repo.

    Without the emptiness check this parses as scoped-to-nothing, and the id silently loses
    its package — which is how a real external node would become a different node.
    """
    raw = "ts:@vue/runtime-core:h"
    assert unscope_id(raw) == ("", raw)
    assert unscope_id(scope_id(raw, "web")) == ("web", raw)


def test_an_id_with_no_language_prefix_is_left_alone() -> None:
    assert scope_id("bare-identifier", "svc-a") == "bare-identifier"


# ---- repo keys -------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "has space", "has@sep", "/abs/path", "../rel", "-leading"])
def test_an_unusable_repo_key_is_refused_loudly(bad: str) -> None:
    """Rejected, never sanitised: a key silently rewritten differs between machines."""
    with pytest.raises(ScopeError):
        validate_repo_key(bad)


@pytest.mark.parametrize("good", ["svc-a", "billing.core", "Repo_1", "a"])
def test_a_usable_repo_key_is_accepted(good: str) -> None:
    assert validate_repo_key(good) == good


# ---- what scoping does to a batch -----------------------------------------


def _batch(external: bool = False) -> FactBatch:
    b = FactBatch()
    b.add_node(Node("py:shop.cart", NodeKind.MODULE, "shop.cart", "python", Provenance("cart.py", 1)))
    b.add_node(Node("py:shop.cart.Cart", NodeKind.TYPE, "Cart", "python", Provenance("cart.py", 4)))
    if external:
        b.add_node(Node("py:ValueError", NodeKind.TYPE, "ValueError", "python", external=True))
        b.add_edge(Edge("py:shop.cart.Cart", "py:ValueError", EdgeKind.CALLS, Provenance("cart.py", 9)))
    b.add_edge(Edge("py:shop.cart", "py:shop.cart.Cart", EdgeKind.CONTAINS, Provenance("cart.py", 4)))
    return b


def test_scoping_a_batch_moves_nodes_edges_and_provenance() -> None:
    out = scope_batch(_batch(), "svc-a")
    assert {n.id for n in out.nodes} == {"py:svc-a@shop.cart", "py:svc-a@shop.cart.Cart"}
    edge = next(iter(out.edges))
    assert (edge.src, edge.dst) == ("py:svc-a@shop.cart", "py:svc-a@shop.cart.Cart")
    assert all(n.provenance is None or n.provenance.repo == "svc-a" for n in out.nodes)


def test_an_external_node_keeps_its_id() -> None:
    """`py:ValueError` is the same thing in every repo. Scoping it makes two copies of one fact."""
    out = scope_batch(_batch(external=True), "svc-a")
    assert "py:ValueError" in {n.id for n in out.nodes}
    call = next(e for e in out.edges if e.kind is EdgeKind.CALLS)
    assert call.dst == "py:ValueError"
    assert call.src == "py:svc-a@shop.cart.Cart"


def test_the_collision_this_whole_module_exists_to_prevent() -> None:
    """Two repos defining the same class must be two nodes, not one.

    Merging unscoped is `add_node` in a loop, so it collapses them silently — no dangling edge,
    nothing for `pkg verify` to report. Internally consistent and externally false.
    """
    naive = FactBatch()
    naive.merge(_batch())
    naive.merge(_batch())
    assert len(list(naive.nodes)) == 2, "the bug: two repos collapsed to one set of nodes"

    merged = merge_repos({"svc-a": _batch(), "svc-b": _batch()})
    assert len(list(merged.nodes)) == 4
    assert {n.id for n in merged.nodes} == {
        "py:svc-a@shop.cart",
        "py:svc-a@shop.cart.Cart",
        "py:svc-b@shop.cart",
        "py:svc-b@shop.cart.Cart",
    }


def test_merging_is_deterministic_regardless_of_input_order() -> None:
    a = merge_repos({"svc-a": _batch(), "svc-b": _batch()})
    b = merge_repos({"svc-b": _batch(), "svc-a": _batch()})
    assert [n.id for n in a.nodes] == [n.id for n in b.nodes]
    assert [(e.src, e.dst) for e in a.edges] == [(e.src, e.dst) for e in b.edges]


# ---- the invariant that protects everything already shipped ----------------


def test_single_repo_extraction_is_untouched(tmp_path: Path) -> None:
    """Nothing may move because a feature nobody enabled was added.

    The commit-keyed cache, the committed `scoreboard.json`, every corpus fixture and
    `understand --check` all rest on single-repo extraction being byte-stable.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor

    (tmp_path / "m.py").write_text("class Cart:\n    def add(self):\n        return 1\n")
    batch = RepoCodeExtractor().extract(tmp_path)

    assert all(unscope_id(n.id) == ("", n.id) for n in batch.nodes), "ids gained a scope"
    assert all(n.provenance is None or n.provenance.repo == "" for n in batch.nodes), (
        "provenance gained a repo"
    )
