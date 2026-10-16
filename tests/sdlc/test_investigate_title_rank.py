"""`investigate` ranks on the ticket title (SSPN-192) unless INVESTIGATE_TITLE_RANK=0.

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


def _order(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> list[str]:
    if flag is None:
        monkeypatch.delenv("INVESTIGATE_TITLE_RANK", raising=False)
    else:
        monkeypatch.setenv("INVESTIGATE_TITLE_RANK", flag)
    inv = build_investigation(TITLE, BODY, store=_store(), max_symbols=10)
    return [land.name for land in inv.landing]


def test_default_ranks_on_the_title(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _order(monkeypatch, None) == [
        "frobnicate_widgets",
        "frobnicate",
        "frobnicate_gizmo",
        "gizmo_gears",
    ]


def test_off_switch_restores_the_flat_query(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _order(monkeypatch, "0") == ["frobnicate_widgets", "gizmo_gears", "frobnicate_gizmo", "frobnicate"]


def test_a_ticket_without_a_body_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVESTIGATE_TITLE_RANK", raising=False)
    on = build_investigation(TITLE, "", store=_store(), max_symbols=10)
    monkeypatch.setenv("INVESTIGATE_TITLE_RANK", "0")
    off = build_investigation(TITLE, "", store=_store(), max_symbols=10)
    assert [x.name for x in on.landing] == [x.name for x in off.landing]
