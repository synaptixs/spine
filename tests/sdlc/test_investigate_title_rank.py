"""`investigate` ranks on the ticket title (SSPN-192) when asked, unless INVESTIGATE_TITLE_RANK=0.

Only the investigate surface asks (`rank_on_title=True`). `design`, `build`, autorun and the other
callers keep today's flat query: two NSS-1231 design tests pin that a file named only in the body
must still be proposed.

Measured on 500 SWE-bench Verified issues: giving the retriever the whole issue as one flat set of
words ranked worse at top-3 than giving it the first line alone. The title is passed separately and
the body breaks ties.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg import FactBatch, FactStore, Node, NodeKind, Provenance
from orchestrator.sdlc.investigate import build_investigation

TITLE = "frobnicate widgets"
BODY = "also touches gizmo gears"


def _store() -> FactStore:
    batch = FactBatch()
    for file, name in (
        ("a.py", "frobnicate_widgets"),
        ("b.py", "frobnicate"),
        ("c.py", "gizmo_gears"),
        ("d.py", "frobnicate_gizmo"),
    ):
        batch.add_node(
            Node(f"py:{file[:-3]}.{name}", NodeKind.FUNCTION, name, "python", Provenance(file, 1, 3))
        )
    return FactStore(batch)


def _order(monkeypatch: pytest.MonkeyPatch, flag: str | None, *, ask: bool) -> list[str]:
    if flag is None:
        monkeypatch.delenv("INVESTIGATE_TITLE_RANK", raising=False)
    else:
        monkeypatch.setenv("INVESTIGATE_TITLE_RANK", flag)
    inv = build_investigation(TITLE, BODY, store=_store(), max_symbols=10, rank_on_title=ask)
    return [land.name for land in inv.landing]


FLAT = ["frobnicate_widgets", "gizmo_gears", "frobnicate_gizmo", "frobnicate"]
TITLED = ["frobnicate_widgets", "frobnicate", "frobnicate_gizmo", "gizmo_gears"]


def test_a_caller_that_asks_gets_the_title_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _order(monkeypatch, None, ask=True) == TITLED


def test_the_off_switch_restores_the_flat_query(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _order(monkeypatch, "0", ask=True) == FLAT


def test_a_caller_that_does_not_ask_keeps_the_flat_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """design, build, autorun and the rest: unchanged, whatever the switch says."""
    assert _order(monkeypatch, None, ask=False) == FLAT
    assert _order(monkeypatch, "1", ask=False) == FLAT


def test_a_ticket_without_a_body_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVESTIGATE_TITLE_RANK", raising=False)
    asked = build_investigation(TITLE, "", store=_store(), max_symbols=10, rank_on_title=True)
    plain = build_investigation(TITLE, "", store=_store(), max_symbols=10)
    assert [x.name for x in asked.landing] == [x.name for x in plain.landing]
