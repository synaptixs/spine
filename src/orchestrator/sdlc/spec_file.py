"""Load a hand-written feature spec from disk, in place of intake.

Intake derives a spec from a source document. Sometimes you already know what the
spec is and want the pipeline to build *that* — a remediation, a spec agreed in
review, or a ticket whose repair is a defect in intake itself, where letting the
broken stage write the specification for its own fix is circular.

The injection path already exists (``run_feature(spec=...)``, ``autorun(spec=...)``);
this is the file format that reaches it.

**JSON, validated strictly.** A markdown spec would be friendlier to write and worse
to trust: a criterion the parser silently misses is indistinguishable from one you
never wrote, which is the failure this pipeline keeps having. ``FeatureSpec`` forbids
extra keys, so ``acceptance-criteria`` or ``acceptanceCriteria`` is an error naming
the valid fields rather than a run that quietly proceeds with none.

``proposed_criteria`` is optional and defaults to empty: a file may carry criteria
nobody stated (clearly marked as such), and a file that only supplies
``acceptance_criteria`` keeps validating exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.intake.specs import FeatureSpec


class SpecFileError(ValueError):
    """A spec file could not be read, parsed, or validated."""


def load_spec_file(path: Path | str) -> dict[str, Any]:
    """Read and validate a spec file, returning the dict the pipeline expects.

    Raises :class:`SpecFileError` with a message naming the file and the specific
    problem — this runs before any work starts, so it is the cheapest place to be
    told the spec is wrong.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecFileError(f"cannot read spec file {p}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecFileError(f"{p} is not valid JSON: line {exc.lineno}: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise SpecFileError(f"{p} must contain a JSON object, got {type(payload).__name__}")

    payload.setdefault("intent_id", p.stem)
    try:
        spec = FeatureSpec.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError — reported, not re-raised raw
        raise SpecFileError(f"{p} is not a valid spec:\n{exc}") from exc

    if not spec.title.strip():
        raise SpecFileError(f"{p}: 'title' is required and cannot be empty")
    if not spec.acceptance_criteria:
        # Not fatal upstream, but a spec with nothing to satisfy gives the judge
        # nothing to judge — the run would report success having proved nothing.
        # 'proposed_criteria' deliberately doesn't count: unsourced criteria are
        # suggestions, not a contract to pass a run against.
        raise SpecFileError(
            f"{p}: 'acceptance_criteria' is empty — there would be nothing for the "
            "acceptance judge to verify, and the run would pass by default."
        )
    return spec.model_dump()


__all__ = ["SpecFileError", "load_spec_file"]
