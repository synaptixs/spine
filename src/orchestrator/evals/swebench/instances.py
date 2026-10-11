"""Instances, split so that the generation side cannot see the answer.

A SWE-bench row carries the gold ``patch``, the ``test_patch`` and the tests that grade it, and a
``hints_text`` comment thread that can describe the fix. None of that may reach the pipeline that
is being measured — and "none of that did, this time" is not a control, because the next field
anyone adds would pass it silently.

So the control is structural. The file this module reads is produced by ``scripts/swebench_prep.py``
and is **refused if it carries a forbidden field**; the type generation receives
(:class:`GenerationInput`) has no field that could hold one; and the one fact the grading side needs
from the test patch — the *names* of the files it overwrites, so the submitted patch can leave them
out — lives on :class:`Instance`, not on the generation input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Fields a SWE-bench row carries that must never be read here. A file containing one is refused.
FORBIDDEN_FIELDS = frozenset(
    {
        "patch",
        "test_patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "hints_text",
        "environment_setup_commit",
        "created_at",
    }
)

_REQUIRED = (
    "instance_id",
    "repo",
    "version",
    "base_commit",
    "problem_statement",
    "image",
    "image_digest",
    "test_patch_files",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class GenerationInput:
    """Everything the pipeline is allowed to know about an instance."""

    instance_id: str
    repo: str
    version: str
    base_commit: str
    problem_statement: str
    image: str
    image_digest: str

    @property
    def image_ref(self) -> str:
        """The image by digest — never by tag, which can move under a run."""
        name = self.image.rsplit(":", 1)[0] if ":" in self.image.rsplit("/", 1)[-1] else self.image
        return f"{name}@{self.image_digest}"


@dataclass(frozen=True)
class Instance:
    generation: GenerationInput
    #: Paths the instance's test patch overwrites. Grading side only: it is used afterwards, to
    #: drop those files from the submitted patch, and never reaches generation.
    test_patch_files: tuple[str, ...]


def load_instances(path: Path | str) -> list[Instance]:
    """Read ``instances.jsonl``; raises ``ValueError`` naming the row and field on any defect."""
    found: dict[str, Instance] = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        where = f"{path}:{number}"
        leaked = sorted(FORBIDDEN_FIELDS & set(row))
        if leaked:
            raise ValueError(
                f"{where}: row carries gold data that must not reach generation: {', '.join(leaked)}"
            )
        for name in _REQUIRED:
            if name not in row:
                raise ValueError(f"{where}: missing field {name!r}")
        if not str(row["problem_statement"]).strip():
            raise ValueError(f"{where}: problem_statement is blank")
        if not _DIGEST.match(str(row["image_digest"])):
            raise ValueError(
                f"{where}: image_digest must be pinned as sha256:<64 hex>, got {row['image_digest']!r}"
            )
        if row["instance_id"] in found:
            raise ValueError(f"{where}: duplicate instance_id {row['instance_id']!r}")
        found[row["instance_id"]] = Instance(
            generation=GenerationInput(**{k: str(row[k]) for k in _REQUIRED if k != "test_patch_files"}),
            test_patch_files=tuple(str(f) for f in row["test_patch_files"]),
        )
    return list(found.values())


def image_name(instance_id: str) -> str:
    """The published image for an instance (``org__repo-N`` → ``…sweb.eval.x86_64.org_1776_repo-N``)."""
    return f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"


def record_from_row(row: dict[str, Any], *, image_digest: str) -> dict[str, Any]:
    """The one place a raw dataset row (gold patch and all) is read, and what comes out of it.

    Called by ``scripts/swebench_prep.py``. The output carries only the allowed fields; of the test
    patch it keeps the *names* of the files it touches and nothing else. Everything else in the row
    is dropped on the floor here, so no later stage has a gold field to forward by mistake.
    """
    test_patch = str(row.get("test_patch") or "")
    files = re.findall(r"^diff --git a/(\S+) b/", test_patch, flags=re.MULTILINE)
    return {
        "instance_id": row["instance_id"],
        "repo": row["repo"],
        "version": str(row["version"]),
        "base_commit": row["base_commit"],
        "problem_statement": row["problem_statement"],
        "image": image_name(row["instance_id"]),
        "image_digest": image_digest,
        "test_patch_files": files,
    }
