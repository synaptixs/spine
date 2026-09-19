"""The brief, over a merged multi-repo graph — E2 Phase 4.

The whole programme is only worth anything if a surface reads the cross-repo edges. Phases 1–3
put them in the graph; these tests are about whether a person is told.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.join_link import link_joins
from orchestrator.pkg.repos import Join
from orchestrator.pkg.scoping import merge_repos
from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

WEB = "import httpx\n\n\ndef place_order(payload):\n    return httpx.post('/v1/orders', json=payload)\n"
BILLING = (
    "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
    "@app.post('/v1/orders')\ndef create_order():\n    return 1\n"
)


def _merged(tmp_path: Path, *, joined: bool = True) -> FactStore:
    extractors, batches, unresolved = {}, {}, {}
    for name, body in (("web", WEB), ("billing", BILLING)):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / name / "m.py").write_text(body, encoding="utf-8")
        extractors[name] = RepoCodeExtractor()
        batches[name] = extractors[name].extract(tmp_path / name)
        unresolved[name] = list(extractors[name].unresolved_calls)
    merged = merge_repos(batches)
    if joined:
        merged, _ = link_joins(merged, [Join("http", "web", "billing")], unresolved)
    return FactStore(merged)


def _brief(store: FactStore) -> str:
    inv = build_investigation("orders failing", "create_order is returning errors", store=store)
    return render_investigation_md(inv)


# ---- the exit criterion ----------------------------------------------------


def test_a_handler_reports_its_dependent_in_another_repo(tmp_path: Path) -> None:
    """*"A ticket landing in repo A shows the repo-B caller that will break."*

    `create_order` has **0** inbound CALLS — nothing in the source calls an HTTP handler — so
    the caller count alone says nothing depends on it, which is the most dangerous answer the
    graph can give.
    """
    inv = build_investigation("orders failing", "create_order is returning errors", store=_merged(tmp_path))
    handler = next(hit for hit in inv.landing if hit.name == "create_order")
    assert handler.callers == 0, "precondition: nothing *calls* an HTTP handler"
    assert handler.cross_repo == 1, "and something in another repo depends on it entirely"
    assert handler.repo == "billing"


def test_without_the_join_the_same_handler_looks_safe(tmp_path: Path) -> None:
    """The counterfactual, so the number above is attributable to the join and nothing else."""
    inv = build_investigation(
        "orders failing", "create_order is returning errors", store=_merged(tmp_path, joined=False)
    )
    handler = next(hit for hit in inv.landing if hit.name == "create_order")
    assert handler.cross_repo == 0


def test_the_brief_says_which_repository_each_landing_is_in(tmp_path: Path) -> None:
    """Module *names* are not scoped, so two services with `app.models` are indistinguishable
    without this — and `where` does not disambiguate either, since both read `app/models.py:14`."""
    md = _brief(_merged(tmp_path))
    assert "**billing** · `create_order`" in md
    assert "billing:m.py:" in md, "the location is repo-qualified"
    assert "lands in 2 repositories" in md


def test_areas_are_repo_qualified(tmp_path: Path) -> None:
    """Unqualified, two services' `app.models` collapse into one area and the brief claims the
    change is narrower than it is."""
    inv = build_investigation("orders", "create_order place_order", store=_merged(tmp_path))
    assert all(":" in area for area in inv.areas), inv.areas
    assert {a.split(":")[0] for a in inv.areas} == {"web", "billing"}


# ---- single-repo behaviour is untouched ------------------------------------


def test_a_single_repo_brief_carries_no_repo_and_no_cross_repo_count(tmp_path: Path) -> None:
    """The invariant that has held for four phases: nothing moves for a user who opted into
    nothing. An unscoped graph has no "other repo", so the field is 0 rather than misleading."""
    (tmp_path / "m.py").write_text(BILLING, encoding="utf-8")
    inv = build_investigation(
        "orders failing",
        "create_order is returning errors",
        store=FactStore(RepoCodeExtractor().extract(tmp_path)),
    )
    handler = next(hit for hit in inv.landing if hit.name == "create_order")
    assert handler.repo == ""
    assert handler.cross_repo == 0
    assert inv.repos == []
    md = render_investigation_md(inv)
    assert "dependent(s) in other repos" not in md
    assert "lands in 1 repositories" not in md
    assert "repositories:" not in md


# ---- bounded honestly ------------------------------------------------------


def test_a_truncated_list_says_how_many_it_did_not_show(tmp_path: Path) -> None:
    """ "Top N of M", never a clipped view implying completeness (`CLAUDE.md` invariant 7)."""
    body = "\n\n".join(f"def order_handler_{i}():\n    return {i}" for i in range(12))
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))

    inv = build_investigation("order handler", "order handler", store=store, max_symbols=3)
    assert len(inv.landing) == 3
    assert inv.elided > 0
    assert f"{inv.elided} further match(es) not listed" in render_investigation_md(inv)


def test_a_complete_list_does_not_claim_to_be_truncated(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def order_handler():\n    return 1\n", encoding="utf-8")
    inv = build_investigation(
        "order handler", "order handler", store=FactStore(RepoCodeExtractor().extract(tmp_path))
    )
    assert inv.elided == 0
    assert "not listed" not in render_investigation_md(inv)


# ---- D8: a merged brief reads each landed-in repo's own episteme ------------


def _with_bank(root: Path, name: str, body: str) -> None:
    """Write a minimal committed knowledge base for one repo."""
    bank = root / name / "episteme"
    bank.mkdir(parents=True, exist_ok=True)
    (bank / "domain-model.md").write_text(body, encoding="utf-8")


def test_a_merged_brief_carries_each_landed_in_repo_under_its_key(tmp_path: Path) -> None:
    """The mode a cross-cutting ticket needs used to be the only mode with no knowledge.

    `episteme/` belongs to one repository, so a merged brief cannot take the section from an
    arbitrary one — it omitted the section entirely instead, which is honest and useless.
    """
    store = _merged(tmp_path)
    _with_bank(tmp_path, "web", "# Domain model\n\nCart is the web model.\n")
    _with_bank(tmp_path, "billing", "# Domain model\n\nInvoice is the billing model.\n")

    inv = build_investigation(
        "orders failing",
        "create_order is returning errors",
        store=store,
        repo_roots={"web": tmp_path / "web", "billing": tmp_path / "billing"},
    )
    md = render_investigation_md(inv)
    assert "### `web`" in md and "Cart is the web model." in md
    assert "### `billing`" in md and "Invoice is the billing model." in md


def test_a_repo_the_ticket_never_lands_in_is_not_read(tmp_path: Path) -> None:
    """A four-repo declaration must not put a second service's domain model in front of a
    reader whose ticket never touches it. The brief already knows where it landed."""
    store = _merged(tmp_path)
    _with_bank(tmp_path, "web", "# Domain model\n\nCart is the web model.\n")
    (tmp_path / "unrelated" / "episteme").mkdir(parents=True, exist_ok=True)
    (tmp_path / "unrelated" / "episteme" / "domain-model.md").write_text(
        "# Domain model\n\nShipping is unrelated.\n", encoding="utf-8"
    )

    inv = build_investigation(
        "orders failing",
        "create_order is returning errors",
        store=store,
        repo_roots={
            "web": tmp_path / "web",
            "billing": tmp_path / "billing",
            "unrelated": tmp_path / "unrelated",
        },
    )
    assert "Shipping is unrelated." not in render_investigation_md(inv)


def test_a_declared_repo_with_no_bank_is_named_not_skipped(tmp_path: Path) -> None:
    """Silence reads as "that repository has nothing to say". It means nobody ran
    `understand` there, which is a different statement and the reader's to act on."""
    store = _merged(tmp_path)
    _with_bank(tmp_path, "web", "# Domain model\n\nCart is the web model.\n")

    inv = build_investigation(
        "orders failing",
        "create_order is returning errors",
        store=store,
        repo_roots={"web": tmp_path / "web", "billing": tmp_path / "billing"},
    )
    md = render_investigation_md(inv)
    assert "`billing`" in md and "Absent, not empty." in md


def test_the_knowledge_budget_is_split_across_repos_not_multiplied(tmp_path: Path) -> None:
    """A section that grows with the repository count crowds out the landing sites, which are
    what the reader came for."""
    from orchestrator.sdlc.investigate import _MERGED_KNOWLEDGE_BUDGET, _merged_knowledge

    for name in ("web", "billing"):
        _with_bank(tmp_path, name, "# Domain model\n\n" + ("x" * 4000))
    block = _merged_knowledge({"web": tmp_path / "web", "billing": tmp_path / "billing"}, ["web", "billing"])
    # Two repos, one budget — plus the two per-repo headings and section framing.
    assert len(block) < _MERGED_KNOWLEDGE_BUDGET * 1.5
