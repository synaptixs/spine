"""PKG grounded retrieval: enclosing-symbol resolution + blast-radius briefs."""

from __future__ import annotations

import pytest

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    FactStore,
    GroundedRetriever,
    Node,
    NodeKind,
    Provenance,
    retrieval,
)


def _store() -> FactStore:
    batch = FactBatch()
    # callee `total` spans lines 4-6 in invoice.py
    batch.add_node(
        Node("py:inv.Invoice.total", NodeKind.FUNCTION, "total", "python", Provenance("inv.py", 4, 6))
    )
    # a sibling method in the same file (lines 8-9) — same-file caller
    batch.add_node(
        Node("py:inv.Invoice.summary", NodeKind.FUNCTION, "summary", "python", Provenance("inv.py", 8, 9))
    )
    # a caller in a *different* file
    batch.add_node(
        Node("py:report.build", NodeKind.FUNCTION, "build", "python", Provenance("report.py", 12, 20))
    )
    batch.add_edge(
        Edge("py:report.build", "py:inv.Invoice.total", EdgeKind.CALLS, Provenance("report.py", 15))
    )
    batch.add_edge(
        Edge("py:inv.Invoice.summary", "py:inv.Invoice.total", EdgeKind.CALLS, Provenance("inv.py", 9))
    )
    return FactStore(batch)


def test_enclosing_symbol_uses_span() -> None:
    r = GroundedRetriever(_store())
    assert r.enclosing_symbol("inv.py", 5) is not None
    assert r.enclosing_symbol("inv.py", 5).id == "py:inv.Invoice.total"  # type: ignore[union-attr]
    assert r.enclosing_symbol("inv.py", 99) is None  # outside any span


def test_enclosing_symbol_picks_smallest_span() -> None:
    # line 9 is inside both summary (8-9) and nothing larger here → summary
    r = GroundedRetriever(_store())
    assert r.enclosing_symbol("inv.py", 9).id == "py:inv.Invoice.summary"  # type: ignore[union-attr]


def test_diff_impact_orders_by_caller_count() -> None:
    r = GroundedRetriever(_store())
    impacts = r.diff_impact({"inv.py": {5}})
    assert impacts[0].symbol.id == "py:inv.Invoice.total"
    assert {c.caller.id for c in impacts[0].callers} == {"py:report.build", "py:inv.Invoice.summary"}


def test_cross_file_callers_excludes_same_file() -> None:
    r = GroundedRetriever(_store())
    impact = r.impact_of("py:inv.Invoice.total")
    assert impact is not None
    cross = impact.cross_file_callers()
    assert [c.caller.id for c in cross] == ["py:report.build"]  # summary (same file) dropped


def test_render_brief_highlights_cross_file_breakage() -> None:
    r = GroundedRetriever(_store())
    brief = r.render(r.diff_impact({"inv.py": {5}}), cross_file_only=True)
    assert "Product Knowledge Graph" in brief
    assert "py:report.build" in brief and "report.py:15" in brief
    assert "py:inv.Invoice.summary" not in brief  # same-file caller not a breakage risk


def test_render_empty_when_no_downstream() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m.lonely", NodeKind.FUNCTION, "lonely", "python", Provenance("m.py", 1, 2)))
    r = GroundedRetriever(FactStore(batch))
    assert r.render(r.diff_impact({"m.py": {1}})) == ""


# ---- D7: the suffix stripper (CB-686) ------------------------------------------------------------


def test_prose_inflections_reduce_to_the_bare_name_and_short_words_are_left_alone() -> None:
    from orchestrator.pkg.retrieval import _tokens

    assert _tokens("deletion deleted deleting delete") == {"delet"}
    assert _tokens("licences Licence policies policy caching cache created creation") == {
        "licenc",
        "policy",
        "cach",
        "creat",
    }
    # Never below four characters, and `Reader` stays apart from `read`.
    assert _tokens("string type name Reader read") == {"string", "type", "name", "reader", "read"}
    # The NSS-1231 words are unchanged.
    assert _tokens("EBSOrderApiClient client credentials") == {"ebsorder", "api", "client", "credential"}


def _cb_686_store() -> FactStore:
    """The CB-686 shape after the vendored trees are gone: the screen the ticket is about, and
    an unrelated chat type whose two fields happen to be called `reason`."""
    b = FactBatch()
    screen = "src/screens/deleteAccount/index.tsx"
    chat = "src/types/chat.ts"
    b.add_node(
        Node(
            "ts:src/screens/deleteAccount",
            NodeKind.MODULE,
            "src/screens/deleteAccount",
            "typescript",
            Provenance(screen, 1),
        )
    )
    b.add_node(
        Node(
            "ts:src/screens/deleteAccount.DeleteAccountScreen",
            NodeKind.FUNCTION,
            "DeleteAccountScreen",
            "typescript",
            Provenance(screen, 3),
        )
    )
    b.add_node(
        Node(
            "ts:src/screens/deleteAccount.onDeleteAccount",
            NodeKind.FUNCTION,
            "onDeleteAccount",
            "typescript",
            Provenance(screen, 9),
        )
    )
    b.add_node(
        Node("ts:src/types/chat", NodeKind.MODULE, "src/types/chat", "typescript", Provenance(chat, 1))
    )
    b.add_node(
        Node("ts:src/types/chat.BaseMessage", NodeKind.TYPE, "BaseMessage", "typescript", Provenance(chat, 2))
    )
    b.add_node(
        Node(
            "ts:src/types/chat.BaseMessage.reason",
            NodeKind.FIELD,
            "reason",
            "typescript",
            Provenance(chat, 4),
        )
    )
    b.add_node(Node("ts:src/types/chat.Product", NodeKind.TYPE, "Product", "typescript", Provenance(chat, 8)))
    b.add_node(
        Node("ts:src/types/chat.Product.reason", NodeKind.FIELD, "reason", "typescript", Provenance(chat, 10))
    )
    return FactStore(b)


def test_cb_686_account_deletion_lands_on_the_delete_account_screen() -> None:
    """Before the stripper `deletion` matched nothing, so two `reason` fields of an unrelated
    type outranked the screen. Now the screen's file leads, the screen itself is a strong hit
    above every `reason`, and the handler in the same file is the whole-name match on top."""
    hits = GroundedRetriever(_cb_686_store()).scored_symbols("Add reason for account deletion")
    names = [h.node.name for h in hits]
    assert hits[0].node.provenance is not None
    assert hits[0].node.provenance.file == "src/screens/deleteAccount/index.tsx"
    screen = next(h for h in hits if h.node.name == "DeleteAccountScreen")
    assert screen.matched == ("account", "delet") and not screen.weak
    assert names.index("DeleteAccountScreen") < names.index("reason")


# ---- step B: a weak hit does not outrank a specific one (SSPN-168 / SSPN-176) --------------------
#
# `weak` has been computed since 81029ecc — "the words this hit shares with the ticket also name
# symbols in other files" — but only for the hits that survived the cut, and nothing used it to rank.
# So `handl` (from `handling`) matched every `handle` in libuv and the lot outscored the one symbol
# named `recvmmsg` (5161). The flag is now applied before the cut.


def _functions(*names: str) -> FactStore:
    """One function per name, each in its own file, ids sorted the way the file names sort."""
    b = FactBatch()
    for i, name in enumerate(names):
        file = f"src/f{i:02d}.py"
        b.add_node(Node(f"py:f{i:02d}.{name}", NodeKind.FUNCTION, name, "python", Provenance(file, 1)))
    return FactStore(b)


def _weak_vs_specific() -> FactStore:
    """Six `handle`s in six files (a generic word, so weak) and one `recvmmsg_batch` in a seventh."""
    return _functions(
        "handle", "on_handle", "fs_handle", "tcp_handle", "udp_handle", "pipe_handle", "recvmmsg_batch"
    )


def test_a_specific_hit_leads_the_weak_ones_that_outscore_it() -> None:
    hits = GroundedRetriever(_weak_vs_specific()).scored_symbols("recvmmsg handling")

    assert hits[0].node.name == "recvmmsg_batch"
    assert not hits[0].weak
    assert all(h.weak for h in hits[1:])


def test_the_demotion_happens_before_the_cut() -> None:
    """Demoting after the cut is a no-op: the specific hit would already be gone."""
    hits = GroundedRetriever(_weak_vs_specific()).scored_symbols("recvmmsg handling", limit=2)

    assert "recvmmsg_batch" in [h.node.name for h in hits]


def test_a_weak_hit_is_still_returned_and_still_flagged_when_it_is_all_there_is() -> None:
    """Demotion is an order, not a filter: an all-weak ticket must still say so, not go silent."""
    hits = GroundedRetriever(_weak_vs_specific()).scored_symbols("handling")

    assert len(hits) == 6
    assert all(h.weak for h in hits)


def test_a_weak_hits_reported_score_is_the_demoted_one() -> None:
    weak = GroundedRetriever(_weak_vs_specific()).scored_symbols("recvmmsg handling")[1]

    assert weak.weak and weak.score == (3.0 * 1 + 1.0 + 0.5) * retrieval._WEAK_FACTOR


def test_equally_weak_hits_keep_their_original_relative_order() -> None:
    """All six score alike, so the id decides — as it did before the demotion existed."""
    hits = GroundedRetriever(_weak_vs_specific()).scored_symbols("handling")

    assert [h.node.id for h in hits] == sorted(h.node.id for h in hits)


# ---- step P: the file a symbol lives in is evidence too (SSPN-176) -------------------------------
#
# A ticket that says "invoice" and "render" is better served by the `render` in `billing/invoice.py`
# than by the one in `shipping/label.py`, and the name alone cannot tell them apart. The path's words
# add a small, bounded amount to a hit that already matches by name; a path never creates a hit.


def _renders_in_two_places() -> FactStore:
    """Two `render`s that tie on the name; ids sort the shipping one first so a tie shows."""
    b = FactBatch()
    b.add_node(
        Node(
            "py:a_shipping.render",
            NodeKind.FUNCTION,
            "render",
            "python",
            Provenance("src/shipping/label.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:b_billing.render",
            NodeKind.FUNCTION,
            "render",
            "python",
            Provenance("src/billing/invoice.py", 1),
        )
    )
    return FactStore(b)


def test_a_path_word_in_the_ticket_lifts_the_symbol_in_that_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval, "_PATH_TOKEN_BONUS", 0.5)

    hits = GroundedRetriever(_renders_in_two_places()).scored_symbols("invoice render")

    assert hits[0].node.id == "py:b_billing.render"
    assert hits[0].score > hits[1].score


def test_the_shipped_bonus_is_on_so_the_path_word_decides_without_any_patching() -> None:
    """Every other path test patches the constant; this one pins what actually ships."""
    hits = GroundedRetriever(_renders_in_two_places()).scored_symbols("invoice render")

    assert hits[0].node.id == "py:b_billing.render"


def test_without_the_bonus_the_two_renders_tie_and_the_id_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval, "_PATH_TOKEN_BONUS", 0.0)

    hits = GroundedRetriever(_renders_in_two_places()).scored_symbols("invoice render")

    assert [h.node.id for h in hits] == ["py:a_shipping.render", "py:b_billing.render"]
    assert hits[0].score == hits[1].score


def test_a_word_already_matched_by_the_name_is_not_counted_again_for_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    b = FactBatch()
    b.add_node(
        Node(
            "py:a.invoice_total",
            NodeKind.FUNCTION,
            "invoice_total",
            "python",
            Provenance("src/invoice.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:b.invoice_total",
            NodeKind.FUNCTION,
            "invoice_total",
            "python",
            Provenance("src/billing.py", 1),
        )
    )
    monkeypatch.setattr(retrieval, "_PATH_TOKEN_BONUS", 0.5)

    hits = GroundedRetriever(FactStore(b)).scored_symbols("invoice total")

    assert hits[0].score == hits[1].score


def test_at_most_two_path_words_count(monkeypatch: pytest.MonkeyPatch) -> None:
    b = FactBatch()
    b.add_node(
        Node(
            "py:x.render", NodeKind.FUNCTION, "render", "python", Provenance("src/alpha/bravo/charlie.py", 1)
        )
    )
    monkeypatch.setattr(retrieval, "_PATH_TOKEN_BONUS", 0.5)

    hit = GroundedRetriever(FactStore(b)).scored_symbols("alpha bravo charlie render")[0]

    assert hit.score == 3.0 * 1 + 1.0 + 0.5 + 2 * 0.5  # name term, Function bonus, two path words


def test_a_matching_path_alone_does_not_make_a_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrieval, "_PATH_TOKEN_BONUS", 0.5)

    assert GroundedRetriever(_renders_in_two_places()).scored_symbols("invoice") == []
