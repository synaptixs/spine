"""The generation side of a benchmark run must not be able to see the answer.

SWE-bench rows carry the gold patch, the tests that grade it and the issue's comment thread (which
can describe the fix). The harness keeps those out of generation *structurally*: the file it reads
is refused if it carries them, and the type handed to generation has no field for them. A test
that only checked one run's output would pass until the day someone added a field.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.evals.swebench.instances import (
    FORBIDDEN_FIELDS,
    GenerationInput,
    Instance,
    load_instances,
)

ROW: dict[str, Any] = {
    "instance_id": "psf__requests-1142",
    "repo": "psf/requests",
    "version": "1.1",
    "base_commit": "22623bd8c265b78b161542663ee980738441c307",
    "problem_statement": "requests.get is ALWAYS sending content-length\n\nIt should not.",
    "image": "swebench/sweb.eval.x86_64.psf_1776_requests-1142:latest",
    "image_digest": "sha256:b2733e57feb4cfaeac24ab5ec496d4cb89a37db53bd0ca72eaededdb2ce19d1c",
    "test_patch_files": ["test_requests.py"],
}


def _write(path: Path, *rows: dict[str, Any]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_a_valid_file_loads_into_the_two_sides(tmp_path: Path) -> None:
    [inst] = load_instances(_write(tmp_path / "i.jsonl", ROW))

    assert isinstance(inst, Instance)
    assert inst.generation.instance_id == "psf__requests-1142"
    assert inst.generation.problem_statement.startswith("requests.get")
    assert inst.test_patch_files == ("test_requests.py",)


def test_the_generation_type_has_no_field_that_could_hold_an_answer() -> None:
    names = {f.name for f in dataclasses.fields(GenerationInput)}
    assert names.isdisjoint(FORBIDDEN_FIELDS)
    assert "test_patch_files" not in names  # grading side only: it names where the tests are


#: Written out, not derived from FORBIDDEN_FIELDS: a test that iterates the set it checks cannot notice
#: that a name was dropped from it.
GOLD_FIELDS = [
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "hints_text",
    "environment_setup_commit",
    "created_at",
]


@pytest.mark.parametrize("field", GOLD_FIELDS)
def test_a_file_carrying_gold_data_is_refused(tmp_path: Path, field: str) -> None:
    row = {**ROW, field: "SENTINEL-GOLD"}
    with pytest.raises(ValueError, match=field):
        load_instances(_write(tmp_path / "i.jsonl", row))


def test_the_forbidden_set_covers_what_swebench_rows_carry() -> None:
    assert set(GOLD_FIELDS) <= set(FORBIDDEN_FIELDS)


@pytest.mark.parametrize("missing", sorted(set(ROW)))
def test_a_missing_field_is_named(tmp_path: Path, missing: str) -> None:
    row = {k: v for k, v in ROW.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        load_instances(_write(tmp_path / "i.jsonl", row))


def test_a_blank_problem_statement_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="problem_statement"):
        load_instances(_write(tmp_path / "i.jsonl", {**ROW, "problem_statement": "  \n"}))


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_instances(_write(tmp_path / "i.jsonl", ROW, ROW))


def test_an_unpinned_image_is_refused(tmp_path: Path) -> None:
    """A moving tag is how a stale published image masks a broken build."""
    with pytest.raises(ValueError, match="image_digest"):
        load_instances(_write(tmp_path / "i.jsonl", {**ROW, "image_digest": "latest"}))


def test_the_image_reference_is_by_digest() -> None:
    gen = load_one()
    assert gen.image_ref == (
        "swebench/sweb.eval.x86_64.psf_1776_requests-1142@sha256:"
        "b2733e57feb4cfaeac24ab5ec496d4cb89a37db53bd0ca72eaededdb2ce19d1c"
    )


def load_one() -> GenerationInput:
    return GenerationInput(
        **{
            k: ROW[k]
            for k in (
                "instance_id",
                "repo",
                "version",
                "base_commit",
                "problem_statement",
                "image",
                "image_digest",
            )
        }
    )


def test_blank_lines_are_ignored_and_the_order_is_kept(tmp_path: Path) -> None:
    other = {**ROW, "instance_id": "psf__requests-1724"}
    path = tmp_path / "i.jsonl"
    path.write_text(json.dumps(ROW) + "\n\n" + json.dumps(other) + "\n\n", encoding="utf-8")
    assert [i.generation.instance_id for i in load_instances(path)] == [
        "psf__requests-1142",
        "psf__requests-1724",
    ]


# --- turning a raw dataset row into the record the harness is allowed to read -------------

RAW: dict[str, Any] = {
    "instance_id": "django__django-10097",
    "repo": "django/django",
    "version": "2.2",
    "base_commit": "b9cf764be62e77b4777b3a75ec256f6209a57671",
    "problem_statement": "Make URLValidator reject invalid characters in the username and password",
    "patch": "diff --git a/django/core/validators.py SENTINEL-GOLD-PATCH",
    "test_patch": (
        "diff --git a/tests/validators/invalid_urls.txt b/tests/validators/invalid_urls.txt\n"
        "SENTINEL-GOLD-TESTS\n"
        "diff --git a/tests/validators/tests.py b/tests/validators/tests.py\n"
    ),
    "FAIL_TO_PASS": '["test_ok (validators.tests.X) SENTINEL-F2P"]',
    "PASS_TO_PASS": '["test_defaults SENTINEL-P2P"]',
    "hints_text": "the fix is to change the regex SENTINEL-HINT",
    "created_at": "2018-06-22T14:42:51Z",
    "environment_setup_commit": "SENTINEL-ENV",
    "difficulty": "15 min - 1 hour",
}


def test_a_raw_row_becomes_a_record_with_no_gold_in_it() -> None:
    from orchestrator.evals.swebench.instances import record_from_row

    record = record_from_row(RAW, image_digest="sha256:" + "c" * 64)

    text = json.dumps(record)
    assert "SENTINEL" not in text
    assert set(record).isdisjoint(FORBIDDEN_FIELDS)
    assert record["image"] == "swebench/sweb.eval.x86_64.django_1776_django-10097:latest"
    assert record["test_patch_files"] == ["tests/validators/invalid_urls.txt", "tests/validators/tests.py"]


def test_a_record_built_from_a_row_loads_back(tmp_path: Path) -> None:
    from orchestrator.evals.swebench.instances import record_from_row

    path = _write(tmp_path / "i.jsonl", record_from_row(RAW, image_digest="sha256:" + "c" * 64))

    [inst] = load_instances(path)
    assert inst.generation.instance_id == "django__django-10097"
    assert inst.test_patch_files == ("tests/validators/invalid_urls.txt", "tests/validators/tests.py")


def test_the_image_name_follows_the_published_convention() -> None:
    from orchestrator.evals.swebench.instances import image_name

    assert image_name("psf__requests-1142") == "swebench/sweb.eval.x86_64.psf_1776_requests-1142:latest"
    assert image_name("scikit-learn__scikit-learn-10297") == (
        "swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-10297:latest"
    )
