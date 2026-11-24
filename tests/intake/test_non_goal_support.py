"""B174 (SSPN-400), intake half: what the ticket says stays what the plan says.

A field ticket ended "Not blocking — to be picked up later", a scheduling note about the work, and
intake filed the non-goal "Immediate fix": an instruction not to do the work the ticket asked for.
The same ticket asked an open question nobody answered, and the plan dropped it and said PROCEED.

D11 forbids a prompt change (it would invalidate every cached intake and every approval), so the
fix is deterministic and sits where the spec enters ``build_plan``: a non-goal with no support in the
ticket's own text is dropped and listed as dropped, and unanswered questions travel on the spec.

The fixture mirrors that ticket's shape with invented names.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from orchestrator.intake.intents import Intent, Resolution
from orchestrator.intake.support import screen_non_goals

#: A synthetic ticket in the field ticket's shape: a stated fault site, a stated fix, an open
#: question, and a scheduling note at the end.
TICKET = """\
# SHOP-41 Basket shows one seller per item

## Root cause
The seller list is built by a single append in src/basket/summary.py, so only the first seller
of each item is kept.

## Fix
Collect every seller of the item before rendering the summary row.

## Open question
Can the basket screen call the seller listing service directly?

Not blocking — to be picked up later.
Changing the checkout flow is out of scope.
"""


# ---- the support check ------------------------------------------------------------------------


def test_a_scheduling_note_never_becomes_a_non_goal() -> None:
    """The measured case: the note says *when*, and the non-goal it became says *don't*."""
    kept, dropped = screen_non_goals(["Immediate fix"], TICKET)

    assert kept == []
    assert [d.text for d in dropped] == ["Immediate fix"]
    assert "scheduling" in dropped[0].reason


def test_the_scheduling_note_itself_is_not_a_non_goal_either() -> None:
    """Fully supported by the ticket's text, and still not excluded work."""
    kept, dropped = screen_non_goals(["Not blocking — to be picked up later"], TICKET)

    assert kept == []
    assert "scheduling" in dropped[0].reason


def test_timing_words_do_not_rescue_a_non_goal_the_ticket_never_stated() -> None:
    """ "fix" is in the ticket; "immediate" is not. All of a non-goal's words must be."""
    _, dropped = screen_non_goals(["Urgent hotfix of the payment gateway"], TICKET)

    assert [d.text for d in dropped] == ["Urgent hotfix of the payment gateway"]
    assert "payment" in dropped[0].reason  # names the words the ticket does not hold


def test_one_unsupported_word_is_enough_to_drop() -> None:
    """ "changes" and "flow" are in the ticket, "payment" is not: the ticket never excluded that."""
    kept, dropped = screen_non_goals(["Changes to the payment flow"], TICKET)

    assert kept == []
    assert dropped[0].reason.endswith("never says payment")


def test_real_work_marked_low_priority_is_not_a_scheduling_note() -> None:
    """Only a line that says nothing *but* when is a scheduling note."""
    ticket = "Dark mode support is low priority and out of scope here."
    kept, dropped = screen_non_goals(["Dark mode support, low priority"], ticket)

    assert kept == ["Dark mode support, low priority"]
    assert dropped == []


@pytest.mark.parametrize(
    "non_goal",
    ["Out of scope: schedule changes.", "No priority changes", "Wait time", "Sprint planning view"],
)
def test_a_timing_noun_alone_does_not_make_a_scheduling_note(non_goal: str) -> None:
    """B174 review: "schedule", "priority", "wait", "time" and "sprint" are as often what a ticket is
    about as when it is due. Without a clearly temporal word, the line is judged on support alone."""
    ticket = f"Booking page. {non_goal} Not this ticket."
    kept, dropped = screen_non_goals([non_goal], ticket)

    assert kept == [non_goal]
    assert dropped == []


def test_a_non_goal_the_ticket_states_is_kept() -> None:
    """Monotone: the check only ever removes, and a stated exclusion is the thing it keeps."""
    kept, dropped = screen_non_goals(["Changes to the checkout flow"], TICKET)

    assert kept == ["Changes to the checkout flow"]
    assert dropped == []


def test_inflection_does_not_count_as_missing_support() -> None:
    """ "Changes" in the non-goal, "Changing" in the ticket: the same word."""
    kept, _ = screen_non_goals(["Changing checkout"], "Changes to checkout are out of scope.")

    assert kept == ["Changing checkout"]


def test_without_ticket_text_nothing_is_dropped_for_lack_of_support() -> None:
    """A hand-written `--spec` has no ticket to check against: its author's non-goals stand.
    A scheduling note is dropped all the same, since it is never excluded work."""
    kept, dropped = screen_non_goals(["Changes to the checkout flow", "Immediate fix"], "")

    assert kept == ["Changes to the checkout flow"]
    assert [d.text for d in dropped] == ["Immediate fix"]


def test_the_check_is_deterministic_and_keeps_order() -> None:
    goals = ["Changes to the checkout flow", "Immediate fix", "Checkout"]
    assert screen_non_goals(goals, TICKET) == screen_non_goals(list(goals), TICKET)
    assert screen_non_goals(goals, TICKET)[0] == ["Changes to the checkout flow", "Checkout"]


# ---- open questions travel on the spec ----------------------------------------------------------


QUESTION = "Can the basket screen call the seller listing service directly?"


def test_the_spec_carries_the_unanswered_questions_on_both_parse_paths() -> None:
    """Intake asked the model for them; until now nothing downstream read them."""
    from orchestrator.intake.specs import SpecWriter

    intent = Intent(id="a", title="A", description="d", open_questions=[QUESTION])
    writer = SpecWriter(None, model="fake-model")  # type: ignore[arg-type]
    for text in ("not json", json.dumps({"summary": "s", "user_story": "As a u I want x."})):
        assert writer._parse(text, intent).open_questions == [QUESTION]


def test_an_answered_question_is_a_fact_not_an_open_question() -> None:
    from orchestrator.intake.specs import SpecWriter

    intent = Intent(
        id="a",
        title="A",
        description="d",
        open_questions=[QUESTION, "Which sellers count?"],
        resolutions={QUESTION: Resolution(status="answered", answer="yes", origin="user")},
    )
    spec = SpecWriter(None, model="fake-model")._parse("not json", intent)  # type: ignore[arg-type]

    assert spec.open_questions == ["Which sellers count?"]


def test_a_spec_without_questions_serialises_as_it_always_did() -> None:
    """Omitted when empty, like the why-fields, so no stored spec and no approval digest moves."""
    from orchestrator.intake.specs import FeatureSpec

    assert "open_questions" not in FeatureSpec(intent_id="a", title="A").model_dump()
    assert FeatureSpec(intent_id="a", title="A", open_questions=[QUESTION]).model_dump()[
        "open_questions"
    ] == [QUESTION]


# ---- D11: the intake prompt and the cache key do not move ------------------------------------------


def _sha(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_intake_prompt_and_cache_key_are_unchanged() -> None:
    """Pinned on develop f04ad2bc. A prompt change invalidates every cached intake and every
    approval built on one (D11); this fix must not need one. If this fails, the change is not
    the one G2 decided — re-grill D11 rather than re-pin."""
    from orchestrator.intake import cache, intents, specs

    assert _sha(intents._SYSTEM_PROMPT) == "e17ada7925a6fd50b2da0f26fb75ca383f1b88d590d6722e865a4979d5d98833"
    assert _sha(specs._SYSTEM_PROMPT) == "cd45611e41ceb712330b726c9c0e8d3e0903dd30a821e78e6a78da5340a33181"
    assert (
        _sha(vars(intents._SUBMIT_TOOL)) == "27253850b6f4a000452230c6e873f156c8c407c8d790faca80bd9aa0776ad584"
    )
    assert (
        _sha(vars(specs._SUBMIT_TOOL)) == "cdd602d9ddb90d99f5553a794124ce73ea964f23a058951aaae9779bd42aba88"
    )
    assert cache._CACHE_VERSION == 2
    assert cache.cache_path("file:///tmp/t.md").name == "d59bf821d5238e27.json"
    # What the spec writer is told about an intent with an open question: unchanged too.
    intent = Intent(
        id="a",
        title="A",
        description="d",
        open_questions=["Can the screen call the listing service?"],
        non_goals=["n1"],
    )
    message = specs.SpecWriter(None, model="m")._build_user_message(intent)  # type: ignore[arg-type]
    assert _sha(message) == "18ab7616caff81dd7c38c6efe5fa1d79fb27c39716274f28b25597374ecd5566"
