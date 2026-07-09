"""OpenSpec write-back — render derived specs as reviewable OpenSpec changes."""

from __future__ import annotations

from pathlib import Path

from orchestrator.intake.intents import Intent
from orchestrator.intake.openspec_source import change_to_intent
from orchestrator.intake.openspec_writer import change_id_for, render_change, write_change
from orchestrator.intake.specs import FeatureSpec


def _intent() -> Intent:
    return Intent(
        id="intent-create-page-object",
        title="Create Page object",
        description="A typed record of a fetched page.",
        scope="Add src/aeo/page.py with url, status_code, is_ok.",
        acceptance_criteria=[
            "The system SHALL provide a Page with url, status_code, html fields.",
            "GIVEN a Page with status_code 200 THEN page.is_ok is True",
        ],
        open_questions=["Should redirects be followed?"],
    )


def _spec(intent: Intent) -> FeatureSpec:
    return FeatureSpec(
        intent_id=intent.id,
        title=intent.title,
        summary=intent.description,
        acceptance_criteria=intent.acceptance_criteria,
    )


def test_change_id_strips_intent_prefix() -> None:
    assert change_id_for(_intent()) == "create-page-object"
    assert change_id_for(Intent(id="x", title="My Feature")) == "my-feature"  # no prefix → slug


def test_render_change_produces_openspec_structure() -> None:
    intent = _intent()
    files = render_change(_spec(intent), intent)
    assert set(files) == {"proposal.md", "tasks.md", "specs/create-page-object/spec.md"}
    proposal = files["proposal.md"]
    assert proposal.startswith("# Proposal: Create Page object")
    assert "Auto-drafted by Spine" in proposal  # the review banner
    assert "## Why" in proposal and "## What Changes" in proposal
    assert "## Open Questions" in proposal and "redirects" in proposal
    spec_md = files["specs/create-page-object/spec.md"]
    assert "## ADDED Requirements" in spec_md
    assert "### Requirement: Create Page object" in spec_md
    assert spec_md.count("#### Scenario:") == 2  # one per criterion
    # a Given/When/Then criterion is split onto BDD bullet lines
    assert "- GIVEN a Page with status_code 200" in spec_md and "- THEN page.is_ok is True" in spec_md


def test_render_roundtrips_through_the_reader() -> None:
    # render → read back with change_to_intent recovers the criteria (scenarios survive)
    intent = _intent()
    files = render_change(_spec(intent), intent)
    back = change_to_intent(
        "create-page-object",
        proposal_md=files["proposal.md"],
        spec_texts=(files["specs/create-page-object/spec.md"],),
    )
    assert back.title == "Create Page object"
    joined = " ".join(back.acceptance_criteria)
    assert "url, status_code, html" in joined  # requirement statement survived
    assert "status_code 200" in joined and "is_ok is True" in joined  # scenario survived


def test_write_change_never_clobbers_by_default(tmp_path: Path) -> None:
    intent = _intent()
    files = render_change(_spec(intent), intent)
    first = write_change(tmp_path / "openspec", intent, files)
    assert first  # wrote the 3 files
    assert (tmp_path / "openspec" / "changes" / "create-page-object" / "proposal.md").is_file()
    # a human edits the draft; re-running must NOT clobber it
    edited = tmp_path / "openspec" / "changes" / "create-page-object" / "proposal.md"
    edited.write_text("# polished by a human\n", encoding="utf-8")
    second = write_change(tmp_path / "openspec", intent, files)
    assert second == []  # nothing overwritten
    assert edited.read_text() == "# polished by a human\n"
    # explicit overwrite restores the draft
    write_change(tmp_path / "openspec", intent, files, overwrite=True)
    assert "Auto-drafted by Spine" in edited.read_text()
