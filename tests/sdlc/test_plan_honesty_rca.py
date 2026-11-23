"""G2 PR-B, phase B1: §3 keeps what the ticket states about the cause, and says where each fact came from.

B174 (§3 half, D12), B162 (D10), B163, B164, B168. Fixtures are synthetic; they mirror the shapes the
field report showed (a ticket that states its own root cause, a model-enriched RCA with no traced frame)
without any field-repo symbol.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.rca import build_rca, render_rca_md


def _store(*files: str, symbols: tuple[tuple[str, str], ...] = ()) -> FactStore:
    """Module nodes for ``files``; ``symbols`` adds (name, file) function nodes."""
    b = FactBatch()
    for i, f in enumerate(files):
        lang = "typescript" if f.endswith((".ts", ".tsx")) else "python"
        b.add_node(Node(f"m:{i}", NodeKind.MODULE, f, lang, Provenance(f, 1, 90)))
    for j, (name, f) in enumerate(symbols):
        b.add_node(Node(f"f:{j}", NodeKind.FUNCTION, name, "typescript", Provenance(f, 10, 20)))
    return FactStore(b)


# A ticket shaped like the one the field report showed: the cause is stated under its own heading, with
# the file and lines, and the priority note sits in a section of its own.
_TICKET = """CAT-12: Catalog shows one vendor per product

Issue type: Bug

## Summary
The catalog screen lists only the first vendor for each product.

## Root cause
The vendor list in src/screens/catalog/index.tsx is built by a single `.push()`
(lines ~40-41), so every vendor after the first is dropped.

## Priority
Not blocking — to be picked up later.
"""

_FILES = ("src/screens/catalog/index.tsx", "src/services/api.ts")


# ---- B174 §3 half (D12): a ticket-stated root cause is carried verbatim, tagged, ranked first --------------


async def test_d12_a_root_cause_section_is_carried_verbatim_tagged_stated_and_ranked_first() -> None:
    report = await build_rca(
        "Catalog shows one vendor per product\nOnly the first vendor is listed.",
        store=_store(*_FILES),
        source_text=_TICKET,
    )

    top = report.hypotheses[0]
    assert top.confidence == "stated"
    assert "Root cause" in top.claim
    # Verbatim: the reporter's lines, not a paraphrase of them.
    assert (
        "built by a single `.push()`\n(lines ~40-41), so every vendor after the first is dropped."
        in top.quote
    )
    assert "Not blocking" not in top.quote  # the next section is not part of the cause


async def test_d12_the_stated_sections_paths_become_the_fault_site() -> None:
    report = await build_rca(
        "Catalog shows one vendor per product\nOnly the first vendor is listed.",
        store=_store(*_FILES),
        source_text=_TICKET,
    )

    assert report.fault_module == "src/screens/catalog/index.tsx"
    assert report.fault_origin == "stated"
    assert report.fault_site == ""  # stated, not traced: no line is claimed as established


async def test_d12_an_identifier_alone_names_the_fault_file_when_the_graph_holds_it_once() -> None:
    ticket = "## Cause\nThe guard in `applyDiscount` runs before the total is known.\n"
    report = await build_rca(
        "Discount wrong",
        store=_store("src/cart.ts", "src/web.ts", symbols=(("applyDiscount", "src/cart.ts"),)),
        source_text=ticket,
    )

    assert (report.fault_module, report.fault_origin) == ("src/cart.ts", "stated")


async def test_d12_an_identifier_two_files_hold_names_no_file() -> None:
    ticket = "## Cause\nThe guard in `applyDiscount` runs before the total is known.\n"
    store = _store(
        "src/cart.ts",
        "src/web.ts",
        symbols=(("applyDiscount", "src/cart.ts"), ("applyDiscount", "src/web.ts")),
    )
    report = await build_rca("Discount wrong", store=store, source_text=ticket)

    assert report.fault_origin != "stated"
    assert report.hypotheses[0].confidence == "stated"  # the statement is still carried


@pytest.mark.parametrize(
    "heading",
    [
        "## Root cause",
        "### Root Cause:",
        "# Cause",
        "## Analysis",
        "## Fix",
        "## Proposed fix",
        "h2. Root cause",
        "**Root cause:**",
        "*Root cause.*",
    ],
)
async def test_d12_every_heading_form_is_read(heading: str) -> None:
    ticket = (
        f"Intro line.\n\n{heading}\nThe cache key in src/services/api.ts omits the locale.\n\n## Other\nx\n"
    )
    report = await build_rca("Prices wrong", store=_store(*_FILES), source_text=ticket)

    assert report.hypotheses[0].confidence == "stated"
    assert "omits the locale" in report.hypotheses[0].quote
    assert report.fault_module == "src/services/api.ts"


async def test_d12_a_bold_lead_in_carries_its_own_line() -> None:
    ticket = "**Root cause:** the cache key in src/services/api.ts omits the locale.\n\nOther text.\n"
    report = await build_rca("Prices wrong", store=_store(*_FILES), source_text=ticket)

    assert report.hypotheses[0].quote == "the cache key in src/services/api.ts omits the locale."


@pytest.mark.parametrize(
    "heading", ["## Root causes of churn", "## Fix direction (not decided)", "Fix: later"]
)
async def test_d12_a_heading_that_only_contains_a_word_is_not_a_cause_section(heading: str) -> None:
    ticket = f"{heading}\nThe cache key in src/services/api.ts omits the locale.\n"
    report = await build_rca("Prices wrong", store=_store(*_FILES), source_text=ticket)

    assert all(h.confidence != "stated" for h in report.hypotheses)


async def test_d12_a_long_section_is_cut_and_says_so() -> None:
    ticket = "## Root cause\n" + ("word " * 800) + "\n"
    report = await build_rca("x", store=_store(*_FILES), source_text=ticket)

    quote = report.hypotheses[0].quote
    assert len(quote) < 2000
    assert "the rest is in the ticket" in quote


async def test_d12_a_root_cause_on_a_linked_page_is_not_the_tickets() -> None:
    """Only the ticket's own text is read for a stated cause: a linked design page is context."""
    from orchestrator.intake.follow_links import linked_page_marker

    text = (
        "The catalog lists one vendor.\n\n"
        + linked_page_marker("Design notes")
        + "\n## Root cause\nIn src/services/api.ts.\n"
    )
    report = await build_rca("x", store=_store(*_FILES), source_text=text)

    assert all(h.confidence != "stated" for h in report.hypotheses)


async def test_d12_section_3_renders_the_stated_cause_first_with_its_tag(tmp_path: Any) -> None:
    from orchestrator.sdlc.builddoc import _root_cause_block

    report = await build_rca(
        "Catalog shows one vendor per product", store=_store(*_FILES), source_text=_TICKET
    )
    block = _root_cause_block(report)

    assert block.index("**[stated]**") < block.index("**[medium]**") if "**[medium]**" in block else True
    assert "1. **[stated]**" in block
    assert "    > The vendor list in src/screens/catalog/index.tsx is built by a single `.push()`" in block
    assert "named in the ticket's own root-cause section" in block
    assert "nothing inside `src/screens/catalog/index.tsx` is ruled out yet" in block


async def test_d12_a_stated_cause_naming_no_graph_file_still_renders_section_3() -> None:
    from orchestrator.sdlc.builddoc import _root_cause_block

    ticket = "## Root cause\nThe upstream feed sends one vendor per product.\n"
    report = await build_rca("Catalog wrong", store=_store(*_FILES), source_text=ticket)
    block = _root_cause_block(report)

    assert "**[stated]**" in block
    assert "The upstream feed sends one vendor per product." in block
    assert "``" not in block  # no empty module name rendered


async def test_d12_the_rca_document_quotes_the_stated_cause() -> None:
    md = render_rca_md(await build_rca(_TICKET, store=_store(*_FILES)))

    assert "1. **[stated]**" in md
    assert "   > The vendor list in src/screens/catalog/index.tsx" in md


# ---- B163: model enrichment knows the fault is untraced ---------------------------------------------------


class _StubLLM:
    """Returns what a model might: a [high] regression claim, whatever the prompt said."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, messages: Any, **kw: Any) -> Any:
        from orchestrator.core.llm.client import CompletionResult

        self.prompts.append(messages[-1].content)
        payload = {
            "hypotheses": [
                {
                    "claim": "A recent commit regressed the price lookup.",
                    "evidence": ["churn"],
                    "confidence": "high",
                },
                {
                    "claim": "The locale is dropped from the key.",
                    "evidence": ["read"],
                    "confidence": "medium",
                },
            ],
            "fix_approach": "Add the locale to the key.",
        }
        return CompletionResult(
            text=json.dumps(payload),
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            latency_ms=1.0,
        )


async def test_b163_an_untraced_fault_gets_no_high_recency_hypothesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda: "test-model")
    monkeypatch.setattr(
        "orchestrator.sdlc.churn.recently_changed_files", lambda root, **k: {"src/services/api.ts"}
    )
    llm = _StubLLM()
    report = await build_rca(
        "Prices wrong; see src/services/api.ts.", store=_store(*_FILES), root="/repo", llm=llm
    )

    assert report.llm is True and report.fault_origin == "ticket"
    recency = [h for h in report.hypotheses if "recent" in h.claim.lower() or "regress" in h.claim.lower()]
    assert recency and all(h.confidence != "high" for h in recency)
    # The model is told the origin, and is not handed "changed recently" as if it were traced.
    evidence = json.loads(llm.prompts[0].split("EVIDENCE:\n", 1)[1])
    assert evidence["fault_origin"] == "ticket"
    assert "recently_changed" not in evidence


async def test_b163_a_traced_fault_keeps_the_models_high_recency_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda: "test-model")
    from tests.sdlc.test_rca import _TRACEBACK, _graph

    report = await build_rca(_TRACEBACK, store=FactStore(_graph()), llm=_StubLLM())

    assert report.fault_origin == "traced"
    assert report.hypotheses[0].confidence == "high"


async def test_b163_enrichment_keeps_the_tickets_stated_cause_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda: "test-model")
    report = await build_rca(_TICKET, store=_store(*_FILES), llm=_StubLLM())

    assert report.llm is True
    assert report.hypotheses[0].confidence == "stated"
    assert "The locale is dropped from the key." in [h.claim for h in report.hypotheses]


# ---- B164: a path named only on a linked page says so -----------------------------------------------------


async def test_b164_a_path_only_a_linked_page_names_is_labelled_with_the_page() -> None:
    from orchestrator.intake.follow_links import linked_page_marker

    source = (
        "Prices are wrong for some locales.\n\n"
        + linked_page_marker("Pricing design")
        + "\nThe key is built in src/services/api.ts.\n"
    )
    report = await build_rca(
        "Prices wrong. The key is built in src/services/api.ts.", store=_store(*_FILES), source_text=source
    )

    assert report.fault_origin == "linked:Pricing design"
    named = next(h for h in report.hypotheses if "src/services/api.ts" in h.claim)
    assert "named on a linked page (Pricing design)" in named.claim
    assert "named by the ticket" not in named.claim


async def test_b164_the_tickets_own_mention_wins_over_a_linked_page() -> None:
    from orchestrator.intake.follow_links import linked_page_marker

    source = (
        "See src/services/api.ts.\n\n"
        + linked_page_marker("Pricing design")
        + "\nsrc/services/api.ts again.\n"
    )
    report = await build_rca(
        "Prices wrong in src/services/api.ts.", store=_store(*_FILES), source_text=source
    )

    assert report.fault_origin == "ticket"


def test_b164_source_text_marks_each_linked_page_and_splits_back() -> None:
    from orchestrator.intake.follow_links import LINKED_TITLE_PREFIX, source_text_of, split_linked
    from orchestrator.intake.source import SourceDocument

    docs = [
        SourceDocument(id="PROJ-1", title="PROJ-1", body="The ticket."),
        SourceDocument(id="PROJ-2", title="PROJ-2", body="A subtask."),
        SourceDocument(id="confluence:9", title=f"{LINKED_TITLE_PREFIX}Pricing design", body="The page."),
    ]
    text = source_text_of(docs)
    own, pages = split_linked(text)

    assert own == "The ticket.\n\nA subtask."
    assert pages == [("Pricing design", "The page.")]


def test_b164_a_source_with_no_linked_page_is_joined_exactly_as_before() -> None:
    """Byte-identical: `source.txt` of a ticket that follows no link must not move, or its approval would."""
    from orchestrator.intake.follow_links import source_text_of, split_linked
    from orchestrator.intake.source import SourceDocument

    docs = [SourceDocument(id="A", title="A", body="one"), SourceDocument(id="B", title="B", body="two")]
    assert source_text_of(docs) == "one\n\ntwo"
    assert split_linked("one\n\ntwo") == ("one\n\ntwo", [])


def test_b164_section_3_labels_the_linked_page() -> None:
    from orchestrator.sdlc.builddoc import _root_cause_block
    from tests.sdlc.test_builddoc import _RCA

    rca = _RCA(exception="")
    setattr(rca, "fault_origin", "linked:Pricing design")  # noqa: B010 — the fake has no such attribute
    block = _root_cause_block(rca)

    assert "`src/a.py` — named on a linked page (Pricing design), not localized to a line." in block
    assert "a linked page names the file (the ticket does not), not the line" in block


# ---- B168: no traced frame is not "outside this repo's graph" ---------------------------------------------


async def test_b168_a_ranked_ticket_points_at_the_ranked_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orchestrator.sdlc.rca._ranked_candidates",
        lambda *a, **k: (["src/services/api.ts", "src/cart.ts"], ["content (#1)", "content (#2)"], ""),
    )
    report = await build_rca("Prices are wrong for some locales.", store=_store(*_FILES))

    assert "outside this repo's graph" not in report.fix_approach
    assert "`src/services/api.ts`" in report.fix_approach
    assert "ranked" in report.fix_approach


async def test_b168_a_named_file_is_where_the_fix_approach_starts() -> None:
    report = await build_rca("Prices wrong; see src/services/api.ts.", store=_store(*_FILES))

    assert "outside this repo's graph" not in report.fix_approach
    assert "`src/services/api.ts`" in report.fix_approach


async def test_b168_outside_the_graph_only_when_nothing_in_the_repo_matched() -> None:
    report = await build_rca("Something is slow.", store=_store(*_FILES))

    assert "outside this repo's graph" in report.fix_approach


# ---- B162 (D10): §3-only skip list -----------------------------------------------------------------------


@pytest.mark.parametrize("issue_type", ["Epic", "Spike", "User Story", "story", "Task", "New Feature"])
def test_b162_feature_like_types_skip_root_cause(issue_type: str) -> None:
    from orchestrator.sdlc.profile_select import skips_root_cause

    assert skips_root_cause(issue_type) is True


@pytest.mark.parametrize("issue_type", ["Bug", "defect", "Incident", "", "Escalation", "Sub-task", "Subtask"])
def test_b162_bugs_untyped_unknown_and_sub_tasks_keep_root_cause(issue_type: str) -> None:
    from orchestrator.sdlc.profile_select import skips_root_cause

    assert skips_root_cause(issue_type) is False


def test_b162_a_feature_label_decides_only_when_the_type_says_nothing() -> None:
    from orchestrator.sdlc.profile_select import skips_root_cause

    assert skips_root_cause("", labels=("feature",)) is True
    assert skips_root_cause("Escalation", labels=("enhancement",)) is True
    assert skips_root_cause("Bug", labels=("feature",)) is False  # a known type wins outright
    assert skips_root_cause("", labels=("bug", "feature")) is False  # sorted: `bug` answers first
    assert skips_root_cause("Sub-task", labels=("feature",)) is False  # Sub-task keeps RCA: no parent type


def test_b162_the_shared_profile_table_is_untouched() -> None:
    from orchestrator.sdlc.profile_select import is_enhancement, select_profile

    for issue_type in ("Epic", "Spike", "User Story", "Sub-task"):
        assert is_enhancement(issue_type) is False
        assert select_profile(issue_type).profile == "default"


@pytest.mark.parametrize("issue_type", ["Epic", "Spike", "User Story"])
async def test_b162_section_3_is_skipped_for_these_types_in_a_real_plan(
    tmp_path: Any, issue_type: str
) -> None:
    from orchestrator.sdlc.builddoc import build_plan

    (tmp_path / "auth.py").write_text("def login():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Login",
        "summary": "Rework login in auth.py.",
        "acceptance_criteria": [],
    }
    md = await build_plan(spec, root=tmp_path, issue_type=issue_type)
    body = md.split("## 3. Root cause", 1)[1].split("\n## ", 1)[0]

    assert "not a bug" in body
    assert "Hypotheses" not in body


async def test_b162_a_label_decision_survives_the_approval_re_derivation(tmp_path: Any) -> None:
    """The gate re-derives the plan from the approval; a label it could not see would make every
    label-decided plan stale the moment it was approved."""
    from orchestrator.sdlc.builddoc import (
        PlanApproval,
        build_plan,
        plan_digest,
        planned_labels,
        require_approved_plan,
        save_approval,
    )

    (tmp_path / "auth.py").write_text("def login():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Login",
        "summary": "Rework login in auth.py.",
        "acceptance_criteria": [],
    }
    md = await build_plan(spec, root=tmp_path, labels=("feature",))
    assert "not a bug" in md.split("## 3. Root cause", 1)[1].split("\n## ", 1)[0]
    assert planned_labels(md) == ("feature",)

    save_approval(
        PlanApproval(
            intent_id="T-1",
            digest=plan_digest(md),
            decision="APPROVED",
            decided_by="t",
            decided_at="2026-11-23T00:00:00Z",
            commit="x",
            labels=planned_labels(md),
        ),
        root=tmp_path,
    )
    assert (await require_approved_plan(spec, root=tmp_path)).decided_by == "t"


async def test_b162_labels_are_not_in_the_header_when_the_type_decides(tmp_path: Any) -> None:
    from orchestrator.sdlc.builddoc import build_plan

    (tmp_path / "auth.py").write_text("def login():\n    return 1\n", encoding="utf-8")
    spec = {
        "intent_id": "T-1",
        "title": "Login",
        "summary": "Rework login in auth.py.",
        "acceptance_criteria": [],
    }
    md = await build_plan(spec, root=tmp_path, issue_type="Bug", labels=("feature",))

    assert "**Labels:**" not in md
