"""The four states a grounded draft can be in, and the one rule that makes them worth having.

A drafted change puts a model's prose and the graph's facts on one page. If a reader cannot
tell which produced a given line — or cannot tell "we looked and found nothing" from "we never
looked" — then citations have lent their authority to guesses, and the grounded draft is worse
than the blind one. So each state is asserted to say something *different*, and the ungrounded
default is asserted to be byte-for-byte what the command rendered before grounding existed.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.intake import pkg_evidence
from orchestrator.intake.intents import Intent
from orchestrator.intake.openspec_writer import render_change
from orchestrator.intake.specs import FeatureSpec
from orchestrator.pkg.criteria_binding import Anchor, CriteriaBinding, CriterionBinding
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore

INTENT = Intent(id="intent-cart", title="Cart checkout", description="The cart should charge.")
SPEC = FeatureSpec(
    intent_id="intent-cart",
    title="Cart checkout",
    summary="Charge a basket.",
    acceptance_criteria=["The cart posts to /api/charge."],
)


def _store(*, grounded: int = 0, external: int = 0, language: str = "python") -> FactStore:
    batch = FactBatch()
    for i in range(grounded):
        batch.add_node(
            Node(
                id=f"py:m.f{i}",
                kind=NodeKind.FUNCTION,
                name=f"f{i}",
                language=language,
                provenance=Provenance(file="m.py", line=i + 1),
            )
        )
    for i in range(external):
        batch.add_node(Node(id=f"ext:lib.g{i}", kind=NodeKind.FUNCTION, name=f"g{i}", external=True))
    return FactStore(batch)


# --- classification: the only place a state is decided ------------------------------------


def test_no_repository_is_ungrounded() -> None:
    assert pkg_evidence.ungrounded().state == "ungrounded"


def test_an_empty_graph_is_not_the_same_as_no_repository() -> None:
    """The distinction that costs code: claiming nothing was consulted when something was."""
    g = pkg_evidence.from_store(_store(), where="./web")
    assert g.state == "empty"
    assert g.where == "./web"


def test_external_only_nodes_still_count_as_empty() -> None:
    """Imports resolved to libraries are not this repository's own source."""
    g = pkg_evidence.from_store(_store(external=5), where="./web")
    assert g.state == "empty"
    assert g.nodes == 5 and g.grounded_nodes == 0


def test_a_dirty_tree_is_grounded_but_untrusted() -> None:
    g = pkg_evidence.from_store(_store(grounded=3), where="./web", untrusted=("web",))
    assert g.state == "untrusted"
    assert g.languages == ("python",)


def test_an_empty_graph_reports_emptiness_over_dirtiness() -> None:
    """Both are true; the actionable one wins, and the other would bury it."""
    g = pkg_evidence.from_store(_store(), where="./web", untrusted=("web",))
    assert g.state == "empty"


def test_a_clean_populated_graph_is_grounded() -> None:
    g = pkg_evidence.from_store(_store(grounded=2), where="./web")
    assert g.state == "grounded"
    assert g.cites is True


def test_only_a_state_with_evidence_may_cite() -> None:
    assert pkg_evidence.ungrounded().cites is False
    assert pkg_evidence.from_store(_store(), where="x").cites is False


# --- the page: four states a reader must be able to tell apart ----------------------------

STATES = [
    pkg_evidence.ungrounded(),
    pkg_evidence.from_store(_store(), where="./web"),
    pkg_evidence.from_store(_store(grounded=2), where="./web", untrusted=("web",)),
    pkg_evidence.from_store(_store(grounded=2), where="./web"),
]


def test_every_state_says_something_different_in_the_banner() -> None:
    """One wrong shared constant and all four pages would claim the same standing."""
    assert len({pkg_evidence.banner_sentence(g) for g in STATES}) == 4


def test_every_state_says_something_different_in_the_section() -> None:
    assert len({pkg_evidence.absence_section(g) for g in STATES}) == 4


def test_an_empty_graph_is_never_reported_as_the_ticket_touching_nothing() -> None:
    body = pkg_evidence.absence_section(STATES[1])
    assert "we looked and found nothing" in body
    assert "was** read" in body


def test_an_untrusted_draft_records_it_in_the_file_not_only_on_stderr() -> None:
    body = pkg_evidence.absence_section(STATES[2])
    assert "NOT REPRODUCIBLE" in body
    assert "`web`" in body


# --- render: the default must be the old behaviour ----------------------------------------


def test_grounding_none_is_the_library_default_the_cli_never_takes() -> None:
    """`grounding=None` renders no Grounding section — but the CLI always passes a state.

    Pinned as a *library* default, and named as one. An earlier version of this test read as
    proof that the ungrounded **command** was unchanged, which it is not: `_grounding_for`
    returns `ungrounded()`, never `None`, so every draft gains the banner and the section.
    Three user-facing documents repeated that wrong reading.
    """
    files = render_change(SPEC, INTENT)
    assert "## Grounding" not in files["proposal.md"]
    assert "Auto-drafted by Spine" in files["proposal.md"]


def test_the_delta_spec_is_the_one_file_no_mode_changes() -> None:
    """`specs/<cap>/spec.md` is the contract codegen hits — it must not move."""
    baseline = render_change(SPEC, INTENT)
    key = next(k for k in baseline if k.endswith("spec.md"))
    for g in (None, *STATES):
        assert render_change(SPEC, INTENT, g)[key] == baseline[key]


@pytest.mark.parametrize("grounding", STATES)
def test_a_grounded_render_always_carries_both_the_banner_line_and_the_section(
    grounding: pkg_evidence.DraftGrounding,
) -> None:
    proposal = render_change(SPEC, INTENT, grounding)["proposal.md"]
    assert pkg_evidence.banner_sentence(grounding) in proposal
    assert "## Grounding" in proposal


@pytest.mark.parametrize("grounding", STATES)
def test_the_fact_region_is_fenced_below_the_prose_never_interleaved(
    grounding: pkg_evidence.DraftGrounding,
) -> None:
    """A citation beside a model's sentence lends it authority it has not earned."""
    proposal = render_change(SPEC, INTENT, grounding)["proposal.md"]
    assert proposal.index("## Why") < proposal.index("## Grounding")
    assert proposal.index("## What Changes") < proposal.index("## Grounding")


@pytest.mark.parametrize("grounding", STATES)
def test_grounding_never_reaches_the_delta_spec(grounding: pkg_evidence.DraftGrounding) -> None:
    """The delta spec is the contract codegen hits; grounding is commentary on it."""
    files = render_change(SPEC, INTENT, grounding)
    spec_key = next(k for k in files if k.endswith("spec.md"))
    assert files[spec_key] == render_change(SPEC, INTENT)[spec_key]


# --- the fact block: P3(d) ----------------------------------------------------------------


def _bound(text: str, symbol: str, where: str, *, in_evidence: bool = False) -> CriterionBinding:
    return CriterionBinding(
        text=text,
        status="bound",
        anchors=(
            Anchor(text=symbol, symbol=symbol, node_id=f"py:{symbol}", where=where, in_evidence=in_evidence),
        ),
    )


GROUNDED = pkg_evidence.from_store(_store(grounded=2), where="./web")


def test_a_state_that_may_not_cite_renders_no_fact_block() -> None:
    """The mechanical half of the rule: absence and citation never appear on one page."""
    assert pkg_evidence.fact_section(pkg_evidence.ungrounded()) == ""
    assert pkg_evidence.fact_section(pkg_evidence.from_store(_store(), where="./web")) == ""


def test_landings_are_grouped_by_repository_and_an_empty_one_is_named() -> None:
    g = pkg_evidence.with_facts(
        GROUNDED,
        landings=(
            pkg_evidence.LandingGroup(repo="web", bullets=("- `Cart` (Type, 2 caller(s)) — `c.py:1`",)),
            pkg_evidence.LandingGroup(repo="billing", bullets=(), absent=True),
        ),
    )
    body = pkg_evidence.fact_section(g)
    assert "#### `web`" in body and "#### `billing`" in body
    assert "lands in this repository, but no symbol matched" in body


def test_a_single_repo_block_carries_no_repository_heading() -> None:
    g = pkg_evidence.with_facts(
        GROUNDED, landings=(pkg_evidence.LandingGroup(repo="", bullets=("- `Cart`",)),)
    )
    assert "####" not in pkg_evidence.fact_section(g)


def test_a_clipped_landing_list_says_it_clipped_without_inventing_a_count() -> None:
    """`elided` saturates at 1 — printing it as a number understates by any margin.

    `build_investigation` retrieves `max_symbols + 1` so the field can distinguish "these are
    all of them" from "this is the top N". Rendered as a count, a ticket matching three
    hundred symbols reads "1 further match", which in a committed file says the list is
    essentially complete.
    """
    g = pkg_evidence.with_facts(
        GROUNDED, landings=(pkg_evidence.LandingGroup(repo="", bullets=("- a", "- b")),), elided=1
    )
    body = pkg_evidence.fact_section(g)
    assert "_Showing the top 2 — **further matches were cut** and are not listed._" in body
    assert "further match(es)" not in body


def test_the_shown_figure_counts_bullets_not_rendered_lines() -> None:
    """`render_landings` appends an excerpt as its own entry; `len(bullets)` would inflate."""
    g = pkg_evidence.with_facts(
        GROUNDED,
        landings=(pkg_evidence.LandingGroup(repo="", bullets=("- a", "\n  ```py\n  x\n  ```\n", "- b")),),
        elided=1,
    )
    assert "_Showing the top 2 —" in pkg_evidence.fact_section(g)


def test_no_landing_is_reported_as_lexical_retrieval_not_as_absence_of_work() -> None:
    body = pkg_evidence.fact_section(pkg_evidence.with_facts(GROUNDED))
    assert "lexical" in body


def test_bound_criteria_are_evidence_for_a_human_never_a_verdict() -> None:
    binding = CriteriaBinding(rows=(_bound("The cart charges.", "charge", "svc/charge.py:4"),))
    body = pkg_evidence.fact_section(pkg_evidence.with_facts(GROUNDED, binding=binding))
    assert "name code that already exists" in body
    assert "confirm whether the behaviour is already satisfied" in body
    assert "`svc/charge.py:4`" in body


def test_there_is_exactly_one_criteria_heading_not_a_duplicate_already_met_section() -> None:
    """The candidate set *is* the bound set; two headings would read as two findings."""
    binding = CriteriaBinding(rows=(_bound("The cart charges.", "charge", "svc/charge.py:4"),))
    body = pkg_evidence.fact_section(pkg_evidence.with_facts(GROUNDED, binding=binding))
    assert body.count("### ") == 2  # "Where it lands" + "Criteria against the code"
    assert "already met" not in body.lower()


def test_an_unbound_criterion_does_not_claim_the_work_is_new() -> None:
    binding = CriteriaBinding(
        rows=(CriterionBinding(text="The cart refunds.", status="unbound", claims=("refund",)),)
    )
    body = pkg_evidence.fact_section(pkg_evidence.with_facts(GROUNDED, binding=binding))
    assert "the two look identical from here" in body
    assert "`refund`" in body


def test_criteria_making_no_claim_are_counted_not_listed_as_failures() -> None:
    binding = CriteriaBinding(rows=(CriterionBinding(text="It should be fast.", status="no-claim"),))
    body = pkg_evidence.fact_section(pkg_evidence.with_facts(GROUNDED, binding=binding))
    assert "make no claim about existing code" in body
    assert "not a failure to bind" in body


# --- §5.1: a citation that does not open is worse than no citation -------------------------


def test_every_file_line_in_a_fact_block_resolves_in_the_graph() -> None:
    """The check the whole track rests on.

    A `file:line` a reader cannot open does not merely fail to help — it makes an unverified
    claim *look* verified, which is the one outcome that would leave the grounded draft worse
    than the blind one. So the block is rendered from a real store and every citation in it is
    resolved back against that store's provenance.
    """
    from orchestrator.cli.build import _facts_for_spec

    batch = FactBatch()
    provenances = {
        "charge": Provenance(file="svc/charge.py", line=4),
        "refund": Provenance(file="svc/charge.py", line=9),
        "Cart": Provenance(file="app/cart.py", line=5),
    }
    for name, prov in provenances.items():
        kind = NodeKind.TYPE if name == "Cart" else NodeKind.FUNCTION
        batch.add_node(Node(id=f"py:svc.{name}", kind=kind, name=name, language="python", provenance=prov))
    store = FactStore(batch)

    spec = FeatureSpec(
        intent_id="intent-cart",
        title="Cart charge",
        summary="The Cart should call charge and support refund.",
        description="The Cart should call charge and support refund.",
        # Backticked on purpose: the binder is precision-first and treats a bare prose word as
        # no claim at all. Plain text here would make this test pass while exercising none of
        # the bound path — the very path whose citations it exists to check.
        acceptance_criteria=["`Cart.checkout` calls `charge`.", "`refund` reverses a charge."],
    )
    base = pkg_evidence.from_store(store, where="./repo")
    body = pkg_evidence.fact_section(_facts_for_spec(base, store, None, None, spec))

    known = {f"{p.file}:{p.line}" for p in provenances.values()} | {p.file for p in provenances.values()}
    cited = set(re.findall(r"`([^`\s]+\.py(?::\d+)?)`", body)) | set(re.findall(r"— ([^\s`]+\.py:\d+)", body))
    assert cited, "the block cited nothing, so this test proved nothing"
    assert "name code that already exists" in body, "the bound path was not exercised"
    assert cited <= known, f"unresolvable citation(s): {sorted(cited - known)}"


# --- tasks.md: P3(e), D20 ------------------------------------------------------------------


TASK_SPEC = FeatureSpec(
    intent_id="intent-cart",
    title="Cart checkout",
    acceptance_criteria=["GIVEN a cart\nWHEN checkout\nTHEN it charges", "`refund` reverses a charge"],
    proposed_criteria=["Retry on 5xx"],
)


def test_tasks_are_one_per_criterion_not_two_constants() -> None:
    """The defect the premise names: `_tasks_md` took `spec` and read nothing from it."""
    tasks = render_change(TASK_SPEC, INTENT)["tasks.md"]
    assert "- [ ] 1.1 GIVEN a cart WHEN checkout THEN it charges" in tasks
    assert "- [ ] 1.2 `refund` reverses a charge" in tasks
    assert "Implement the requirement" not in tasks


def test_a_multiline_criterion_becomes_one_checkbox() -> None:
    """A Given/When/Then criterion spans lines; a checkbox that does would break the list."""
    tasks = render_change(TASK_SPEC, INTENT)["tasks.md"]
    assert "\n- [ ] 1.1 GIVEN a cart WHEN checkout THEN it charges\n" in tasks


def test_proposed_criteria_are_kept_apart_and_labelled() -> None:
    """A suggestion the model inferred is not a contract the source signed."""
    tasks = render_change(TASK_SPEC, INTENT)["tasks.md"]
    assert "## 2. Proposed — inferred by Spine, not stated by the source" in tasks
    assert "- [ ] 2.1 Retry on 5xx" in tasks
    # …and it must not be mistaken for a stated one by sitting in the same group.
    assert tasks.index("Retry on 5xx") > tasks.index("## 2.")


def test_verify_first_survives_the_whitespace_an_llm_emits() -> None:
    """The note is keyed on criterion text, and the binder strips before it binds.

    Built through `bind_criteria` against a real store rather than by hand, because a
    hand-built `CriteriaBinding` uses the spec's own string and so cannot exercise the
    coupling at all — which is how the raw-vs-stripped mismatch shipped.
    """
    from orchestrator.pkg.criteria_binding import bind_criteria

    batch = FactBatch()
    batch.add_node(
        Node(
            id="py:svc.refund",
            kind=NodeKind.FUNCTION,
            name="refund",
            language="python",
            provenance=Provenance(file="svc/charge.py", line=9),
        )
    )
    store = FactStore(batch)
    criterion = "`refund` reverses a charge"
    for raw in (criterion, f"  {criterion}\n", f"{criterion}\n\n"):
        spec = FeatureSpec(intent_id="i", title="Cart", acceptance_criteria=[raw])
        binding = bind_criteria(spec.model_dump(), store=store)
        assert binding.bound, f"precondition: {raw!r} must bind"
        g = pkg_evidence.with_facts(GROUNDED, binding=binding)
        tasks = render_change(spec, INTENT, g)["tasks.md"]
        assert "**verify first:**" in tasks, f"note lost for {raw!r}"


def test_a_bound_criterion_says_verify_never_done() -> None:
    """Evidence, not a verdict — ticking it off here would be the SSPN-49 failure."""
    binding = CriteriaBinding(rows=(_bound("`refund` reverses a charge", "refund", "svc/charge.py:9"),))
    tasks = render_change(TASK_SPEC, INTENT, pkg_evidence.with_facts(GROUNDED, binding=binding))["tasks.md"]
    assert "- [ ] 1.2 `refund` reverses a charge — **verify first:**" in tasks
    assert "- [ ] 1.1 GIVEN a cart WHEN checkout THEN it charges\n" in tasks  # unbound: no note


@pytest.mark.parametrize("grounding", [None, *STATES])
def test_no_task_ever_cites_a_file(grounding: pkg_evidence.DraftGrounding | None) -> None:
    """D20(c) refused, and enforced rather than remembered.

    A task is an instruction, and "change `foo.py:41`" is a derived claim wearing a citation.
    Landing sites belong in the proposal's fact block.
    """
    binding = CriteriaBinding(rows=(_bound("`refund` reverses a charge", "refund", "svc/charge.py:9"),))
    g = pkg_evidence.with_facts(grounding, binding=binding) if grounding is not None else None
    tasks = render_change(TASK_SPEC, INTENT, g)["tasks.md"]
    assert not re.search(r"[\w/]+\.(py|ts|java|cs|go|php|pl|kt|sql):\d+", tasks), tasks


def test_a_spec_with_no_criteria_still_renders_a_usable_task_list() -> None:
    bare = FeatureSpec(intent_id="intent-cart", title="Cart")
    tasks = render_change(bare, INTENT)["tasks.md"]
    assert "- [ ] 1.1 Implement the requirement" in tasks
    assert "## 2. Verification" in tasks


def test_tasks_keep_the_shape_openspec_source_documents() -> None:
    """`## N. Group` + `- [ ] N.M task` — the layout the reader half describes."""
    tasks = render_change(TASK_SPEC, INTENT)["tasks.md"]
    groups = re.findall(r"^## (\d+)\. ", tasks, re.M)
    assert groups == ["1", "2", "3"]
    for line in [ln for ln in tasks.splitlines() if ln.startswith("- [ ]")]:
        assert re.match(r"^- \[ \] \d+\.\d+ \S", line), line


def test_a_declared_repo_with_no_landing_is_named_by_the_production_path() -> None:
    """D14's honesty branch, exercised end to end rather than constructed.

    `absent=True` was unreachable for one commit: the grouping seeded itself from the repos
    the change *landed in*, so every key already had a hit. The old test built the flag by
    hand and passed on code that could never set it — which is exactly why this one goes
    through `_facts_for_spec` with two declared repos and hits in only one.
    """
    from pathlib import Path

    from orchestrator.cli.build import _facts_for_spec
    from orchestrator.pkg.scoping import scope_id

    batch = FactBatch()
    batch.add_node(
        Node(
            id=scope_id("py:app.Cart", "web"),
            kind=NodeKind.TYPE,
            name="Cart",
            language="python",
            provenance=Provenance(file="app/cart.py", line=5),
        )
    )
    store = FactStore(batch)
    spec = FeatureSpec(intent_id="i", title="Cart", summary="The Cart holds items.")
    g = _facts_for_spec(
        pkg_evidence.from_store(store, where="repos.yaml"),
        store,
        None,
        {"web": Path("."), "billing": Path(".")},
        spec,
    )
    by_repo = {grp.repo: grp for grp in g.landings}
    assert set(by_repo) == {"web", "billing"}, "a declared repo was dropped from the page"
    assert by_repo["billing"].absent is True
    assert by_repo["web"].absent is False
    assert "lands in this repository, but no symbol matched" in pkg_evidence.fact_section(g)


def test_one_declared_repo_still_checks_the_tree_several_do_not() -> None:
    """`bind_criteria` takes one root and a merged graph has several.

    With one declared repository there is no ambiguity, so the tree is searched as usual.
    With several there is no single tree, and the page must say so — otherwise a criterion
    naming a config file reads as "the graph cannot find this" when nothing looked for it.
    """
    from pathlib import Path

    from orchestrator.cli.build import _facts_for_spec

    batch = FactBatch()
    batch.add_node(
        Node(
            id="py:app.Cart",
            kind=NodeKind.TYPE,
            name="Cart",
            language="python",
            provenance=Provenance(file="app/cart.py", line=5),
        )
    )
    store = FactStore(batch)
    spec = FeatureSpec(
        intent_id="i",
        title="Cart",
        summary="The Cart reads limits.",
        acceptance_criteria=["`config/limits.yaml` caps the basket."],
    )
    base = pkg_evidence.from_store(store, where="repos.yaml")

    one = _facts_for_spec(base, store, None, {"web": Path(".")}, spec)
    assert one.tree_checked is True
    assert "File mentions were checked against the graph only" not in pkg_evidence.fact_section(one)

    several = _facts_for_spec(base, store, None, {"web": Path("."), "billing": Path(".")}, spec)
    assert several.tree_checked is False
    assert "no single working tree to search" in pkg_evidence.fact_section(several)


def test_the_tree_notice_stays_off_when_nothing_failed_to_bind() -> None:
    """A caveat printed under a fully-bound section is noise, not honesty."""
    g = pkg_evidence.with_facts(
        GROUNDED,
        binding=CriteriaBinding(rows=(_bound("x", "charge", "svc/charge.py:4"),)),
        tree_checked=False,
    )
    assert "no single working tree" not in pkg_evidence.fact_section(g)


def test_the_landing_badge_is_withheld_when_a_merged_graph_cannot_place_it() -> None:
    """`in_evidence` compares repo-stripped paths on both sides.

    Two services that both have `app/models.py` is the normal case, so in a merged graph the
    strongest badge in the section — "this binds where the ticket actually is" — can land on
    the wrong checkout. Withheld rather than guessed.
    """
    from pathlib import Path

    from orchestrator.cli.build import _facts_for_spec
    from orchestrator.pkg.scoping import scope_id

    batch = FactBatch()
    for repo in ("web", "billing"):
        batch.add_node(
            Node(
                id=scope_id("py:app.models.Cart", repo),
                kind=NodeKind.TYPE,
                name="Cart",
                language="python",
                provenance=Provenance(file="app/models.py", line=14),
            )
        )
    store = FactStore(batch)
    spec = FeatureSpec(
        intent_id="i",
        title="Cart",
        summary="The `Cart` is recalculated.",
        acceptance_criteria=["`Cart` is recalculated on discount."],
    )
    base = pkg_evidence.from_store(store, where="repos.yaml")
    merged = _facts_for_spec(base, store, None, {"web": Path("."), "billing": Path(".")}, spec)
    assert "in the landing files" not in pkg_evidence.fact_section(merged)


def test_the_cli_module_imports_no_orchestrator_package_at_import_time() -> None:
    """CLI startup is paid by every `orchestrator --help`, not just by `openspec draft`.

    `cli/__init__` imports `build` eagerly to register commands, so a module-level
    `orchestrator.pkg` import here loads the extractor for anyone running any command. Every
    other CLI module defers; this one did not, and it measured ~58% of startup.
    """
    import ast
    import pathlib

    source = pathlib.Path("src/orchestrator/cli/build.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module]
    offenders = [n.module for n in top_level if n.module and n.module.startswith("orchestrator.")]
    assert offenders == [], f"module-level orchestrator imports in cli/build.py: {offenders}"
