"""The why-fields and ``Resolution`` are absent-when-empty everywhere (D26).

``Intent`` and ``FeatureSpec`` are ``extra="forbid"`` and cached, so a new key written for an
input that has no "why" would make an older Spine re-extract and re-approve. These tests pin the
serialised shape of a no-why input to the keys it had before the fields existed.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.intake.cache import load_cached_plan, save_plan
from orchestrator.intake.intents import Intent, Resolution
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec

# The keys each model serialised before this track — captured from develop @ 3a54316f.
_INTENT_KEYS = {
    "id",
    "title",
    "description",
    "scope",
    "acceptance_criteria",
    "dependencies",
    "nfrs",
    "open_questions",
    "source_doc_ids",
}
_SPEC_KEYS = {
    "intent_id",
    "title",
    "summary",
    "description",
    "scope",
    "user_story",
    "acceptance_criteria",
    "proposed_criteria",
    "met_criteria",
    "technical_notes",
    "nfrs",
    "dependencies",
    "estimate",
}
_SOURCE = "confluence://1234567890"


def _plan(intent: Intent, spec: FeatureSpec) -> BacklogPlan:
    return BacklogPlan(
        documents=[SourceDocument(id="1234567890", title="Reqs", body="text", labels=(), issue_type="Bug")],
        intents=[intent],
        gaps=[],
        specs=[spec],
        blocked=False,
        truncated=False,
    )


def test_an_intent_with_no_why_serialises_to_the_keys_it_always_had() -> None:
    assert set(Intent(id="a", title="A").model_dump()) == _INTENT_KEYS
    assert set(json.loads(Intent(id="a", title="A").model_dump_json())) == _INTENT_KEYS


def test_a_spec_with_no_why_serialises_to_the_keys_it_always_had() -> None:
    assert set(FeatureSpec(intent_id="a", title="A").model_dump()) == _SPEC_KEYS


def test_a_set_why_field_is_serialised_and_only_that_one() -> None:
    dumped = Intent(id="a", title="A", problem="Finance cannot reconcile.").model_dump()
    assert dumped["problem"] == "Finance cannot reconcile."
    assert set(dumped) == _INTENT_KEYS | {"problem"}


def test_resolutions_and_idea_id_appear_only_when_set() -> None:
    res = Resolution(status="answered", answer="EUR, USD", origin="user", channel="cli")
    dumped = Intent(
        id="a",
        title="A",
        open_questions=["Which currencies?"],
        resolutions={"Which currencies?": res},
        idea_id="idea-1",
    ).model_dump()
    assert dumped["idea_id"] == "idea-1"
    assert dumped["resolutions"]["Which currencies?"]["answer"] == "EUR, USD"


def test_the_cache_of_a_no_why_plan_has_the_old_keys_only(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(Intent(id="a", title="A"), FeatureSpec(intent_id="a", title="A")), tmp_path)
    (cache_file,) = tmp_path.rglob("*.json")
    entry = json.loads(cache_file.read_text())
    plan = entry.get("plan", entry)
    assert set(plan["intents"][0]) == _INTENT_KEYS
    assert set(plan["specs"][0]) == _SPEC_KEYS


def test_a_cache_written_before_the_fields_existed_still_loads(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(Intent(id="a", title="A"), FeatureSpec(intent_id="a", title="A")), tmp_path)
    loaded = load_cached_plan(_SOURCE, tmp_path)
    assert loaded is not None
    assert loaded.intents[0].problem == "" and loaded.intents[0].resolutions == {}


def test_the_why_round_trips_through_the_cache(tmp_path: Path) -> None:
    res = Resolution(status="deferred", owner="finance-lead")
    intent = Intent(
        id="a",
        title="A",
        problem="P",
        users=["finance"],
        outcome="O",
        non_goals=["no PDF"],
        idea_id="i1",
        open_questions=["Q?"],
        resolutions={"Q?": res},
    )
    spec = FeatureSpec(
        intent_id="a", title="A", problem="P", users=["finance"], outcome="O", non_goals=["no PDF"]
    )
    save_plan(_SOURCE, _plan(intent, spec), tmp_path)
    loaded = load_cached_plan(_SOURCE, tmp_path)
    assert loaded is not None
    assert loaded.intents[0] == intent
    assert loaded.specs[0] == spec


def test_the_spec_writer_carries_the_why_verbatim_on_both_paths() -> None:
    from orchestrator.intake.specs import SpecWriter

    intent = Intent(id="a", title="A", problem="P", users=["u1"], outcome="O", non_goals=["n1"])
    writer = SpecWriter(None, model="fake-model")  # type: ignore[arg-type]
    for text in ("not json", json.dumps({"summary": "s", "user_story": "As a u I want x."})):
        spec = writer._parse(text, intent)
        assert (spec.problem, spec.users, spec.outcome, spec.non_goals) == ("P", ["u1"], "O", ["n1"])
