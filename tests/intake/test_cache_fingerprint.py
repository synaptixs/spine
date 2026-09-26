"""An edited ticket file is re-extracted, not planned from its first version (P9).

A cache hit never re-read the source. A report tool that rewrites `NSS-1243.md` from the tracker
before every step was therefore planning from whatever the file said the first time, until
someone thought to pass `--refresh`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.intake.cache import (
    analyze_cached,
    cache_path,
    cached_fingerprint,
    save_plan,
    source_fingerprint,
)
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan


class _CountingService:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, root_id: str, **_: Any) -> BacklogPlan:
        self.calls += 1
        return BacklogPlan(intents=[Intent(id=f"intent-v{self.calls}", title=f"v{self.calls}")])


def _ticket(tmp_path: Path, body: str) -> str:
    path = tmp_path / "NSS-1243.md"
    path.write_text(body, encoding="utf-8")
    return f"file://{path}"


async def test_an_unchanged_file_is_a_hit(tmp_path: Path) -> None:
    source = _ticket(tmp_path, "# NSS-1243\nShow Yes/No.\n")
    service, said = _CountingService(), list[str]()

    await analyze_cached(service, source, cache_dir=tmp_path / "c")  # type: ignore[arg-type]
    again = await analyze_cached(service, source, cache_dir=tmp_path / "c", log=said.append)  # type: ignore[arg-type]

    assert service.calls == 1
    assert [i.id for i in again.intents] == ["intent-v1"]
    assert said[0].startswith("[intake] reusing cached backlog")


async def test_an_edited_file_is_re_extracted_and_says_why(tmp_path: Path) -> None:
    source = _ticket(tmp_path, "# NSS-1243\nShow Yes/No.\n")
    service, said = _CountingService(), list[str]()
    await analyze_cached(service, source, cache_dir=tmp_path / "c")  # type: ignore[arg-type]

    _ticket(tmp_path, "# NSS-1243\nShow Yes/No from PsiLocalId.\n")
    fresh = await analyze_cached(service, source, cache_dir=tmp_path / "c", log=said.append)  # type: ignore[arg-type]

    assert service.calls == 2
    assert [i.id for i in fresh.intents] == ["intent-v2"]
    assert "changed since it was extracted — re-extracting" in said[0]
    # And the new extraction is what the next hit compares against.
    assert cached_fingerprint(source, tmp_path / "c") == source_fingerprint(source)


async def test_an_entry_from_before_fingerprints_is_used_with_a_note_and_not_stamped(tmp_path: Path) -> None:
    source = _ticket(tmp_path, "# old\n")
    save_plan(source, BacklogPlan(intents=[Intent(id="intent-old", title="old")]), tmp_path / "c")
    said: list[str] = []

    plan = await analyze_cached(_CountingService(), source, cache_dir=tmp_path / "c", log=said.append)  # type: ignore[arg-type]

    assert [i.id for i in plan.intents] == ["intent-old"]
    assert "predates change detection" in said[1]
    assert cached_fingerprint(source, tmp_path / "c") == ""  # never vouches for a file it did not read


async def test_a_remote_source_is_not_re_read_on_a_hit(tmp_path: Path) -> None:
    service = _CountingService()
    await analyze_cached(service, "jira://NSS-1243", cache_dir=tmp_path)  # type: ignore[arg-type]
    await analyze_cached(service, "jira://NSS-1243", cache_dir=tmp_path)  # type: ignore[arg-type]

    assert service.calls == 1
    assert source_fingerprint("jira://NSS-1243") == ""


def test_a_directory_source_changes_when_any_file_in_it_does(tmp_path: Path) -> None:
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "a.md").write_text("a")
    before = source_fingerprint(f"file://{tmp_path / 't'}")
    (tmp_path / "t" / "b.md").write_text("b")

    assert before.startswith("sha256:") and source_fingerprint(f"file://{tmp_path / 't'}") != before


def test_the_fingerprint_is_an_extra_key_older_readers_ignore(tmp_path: Path) -> None:
    source = _ticket(tmp_path, "x")
    save_plan(source, BacklogPlan(), tmp_path / "c", fingerprint="sha256:abc")

    raw = json.loads(cache_path(source, tmp_path / "c").read_text(encoding="utf-8"))

    assert raw["source_fingerprint"] == "sha256:abc"
    assert raw["version"] == 2  # unchanged: a version bump would re-extract every ticket


def test_a_missing_file_has_no_fingerprint(tmp_path: Path) -> None:
    assert source_fingerprint(f"file://{tmp_path / 'gone.md'}") == ""
