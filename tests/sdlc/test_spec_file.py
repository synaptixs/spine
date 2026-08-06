"""Loading a hand-written spec, in place of intake.

The point of the format is that a spec you wrote is the spec that runs. Every test
here is about a way that could quietly not be true.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.sdlc.spec_file import SpecFileError, load_spec_file

_VALID = {
    "title": "Keep invented criteria separable",
    "summary": "Intake appends inferred criteria to the stated ones.",
    "acceptance_criteria": ["A criterion the source did not state is emitted as proposed."],
}


def _write(tmp_path: Path, payload: object, name: str = "spec.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return p


def test_a_valid_spec_round_trips(tmp_path: Path) -> None:
    spec = load_spec_file(_write(tmp_path, _VALID))
    assert spec["title"] == _VALID["title"]
    assert spec["acceptance_criteria"] == _VALID["acceptance_criteria"]


def test_intent_id_defaults_to_the_filename(tmp_path: Path) -> None:
    """So a spec file is addressable without repeating its own name inside it."""
    assert load_spec_file(_write(tmp_path, _VALID, "SSPN-31.json"))["intent_id"] == "SSPN-31"


def test_an_explicit_intent_id_wins(tmp_path: Path) -> None:
    spec = load_spec_file(_write(tmp_path, {**_VALID, "intent_id": "chosen"}, "ignored.json"))
    assert spec["intent_id"] == "chosen"


def test_a_misspelled_field_is_an_error_not_a_silent_drop(tmp_path: Path) -> None:
    """The whole reason for JSON-with-validation over a markdown parser.

    `acceptance-criteria` would otherwise leave the run with zero criteria, which
    the judge passes by default — a spec that proves nothing, reported as success.
    """
    bad = {"title": _VALID["title"], "acceptance-criteria": ["something"]}
    with pytest.raises(SpecFileError) as exc:
        load_spec_file(_write(tmp_path, bad))
    assert "not a valid spec" in str(exc.value)


def test_an_empty_criteria_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SpecFileError, match="nothing for the acceptance judge to verify"):
        load_spec_file(_write(tmp_path, {**_VALID, "acceptance_criteria": []}))


def test_an_empty_title_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SpecFileError, match="'title' is required"):
        load_spec_file(_write(tmp_path, {**_VALID, "title": "   "}))


def test_malformed_json_names_the_line(tmp_path: Path) -> None:
    with pytest.raises(SpecFileError, match="not valid JSON"):
        load_spec_file(_write(tmp_path, '{"title": "x",}'))


def test_a_json_array_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SpecFileError, match="must contain a JSON object"):
        load_spec_file(_write(tmp_path, [_VALID]))


def test_a_missing_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(SpecFileError, match="cannot read spec file"):
        load_spec_file(tmp_path / "nope.json")
