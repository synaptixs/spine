"""The plan → approve → gate seam, driven through the real CLI in a real git checkout.

Every gate test in `test_builddoc.py` builds its repository in a bare `tmp_path` — no git — so
`derived_at` answers ``unknown`` on both sides of the comparison and the commit stamp the body
carries can never differ. That is how four defects in this seam shipped at once (ledger rows
B15–B18): nothing ever ran `sdlc plan`, then `sdlc approve`, then the gate, in a checkout that
looks like the one an adopter — or `.github/workflows/spine-sdlc.yml`'s build job — actually has.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_SPEC = {
    "intent_id": "PROJ-42",
    "title": "Cart.total raises KeyError for an unknown sku",
    "summary": "Cart.total in shop/cart.py crashes when a sku has no price.",
    "acceptance_criteria": ["Cart.total skips skus without a price instead of raising KeyError"],
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A committed repository with no `.gitignore` — what a fresh adopter checkout looks like."""
    root = tmp_path / "repo"
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "cart.py").write_text(
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n\n"
        "    def total(self, prices):\n"
        "        return sum(prices[s] * q for s, q in self.items)\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    return root


def _spec_file(tmp_path: Path, **over: object) -> Path:
    spec = {**_SPEC, **over}
    path = tmp_path / f"{spec['intent_id']}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _gate(spec_file: Path, root: Path) -> str:
    """What `autorun`'s plan gate says — the function it calls, with the spec it would load."""
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan
    from orchestrator.sdlc.spec_file import load_spec_file

    try:
        approval = asyncio.run(require_approved_plan(load_spec_file(spec_file), root=root))
    except PlanNotApprovedError as exc:
        return f"REFUSED: {exc}"
    return f"PASSED: {approval.decided_by}"


def _plan_and_approve(runner: CliRunner, spec_file: Path, root: Path, *extra: str) -> None:
    planned = runner.invoke(
        app, ["sdlc", "plan", "--spec", str(spec_file), "--path", str(root), "--quiet", *extra]
    )
    assert planned.exit_code == 0, planned.output
    approved = runner.invoke(app, ["sdlc", "approve", "PROJ-42", "--path", str(root), "--by", "reviewer"])
    assert approved.exit_code == 0, approved.output


def test_a_plan_approved_in_a_fresh_checkout_is_the_plan_the_gate_accepts(
    checkout: Path, tmp_path: Path
) -> None:
    """The build job of `spine-sdlc.yml`, step for step: plan, approve, gate — no `.gitignore`."""
    spec_file = _spec_file(tmp_path)
    _plan_and_approve(CliRunner(), spec_file, checkout)
    assert _gate(spec_file, checkout) == "PASSED: reviewer"


def test_writing_a_plan_leaves_the_tree_trusted(checkout: Path, tmp_path: Path) -> None:
    from orchestrator.pkg.persistence import repo_state

    result = CliRunner().invoke(
        app, ["sdlc", "plan", "--spec", str(_spec_file(tmp_path)), "--path", str(checkout), "--quiet"]
    )
    assert result.exit_code == 0, result.output
    assert (checkout / ".spine" / "plans" / "PROJ-42-build.md").is_file()
    assert repo_state(checkout)[1] is False


def test_a_spec_with_its_ticket_plans_from_the_spec_and_keeps_the_ticket_text(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D1: the spec is the requirements; the source only supplies the text §8 checks them against.

    Fetched, never analysed — the spec already says what to build, so the path stays free of the
    model call intake's analysis makes. (It crashed on an unbound `plan_result`: ledger B15.)
    """
    import orchestrator.intake.cache as intake_cache

    def _no_analysis(*_a: object, **_k: object) -> object:
        raise AssertionError("intake analysed the source although --spec supplied the requirements")

    monkeypatch.setattr(intake_cache, "analyze_cached", _no_analysis)
    ticket = tmp_path / "PROJ-42.md"
    criterion = "Cart.total skips skus without a price instead of raising KeyError"
    ticket.write_text(f"# PROJ-42\n\n## Acceptance criteria\n- {criterion}\n", encoding="utf-8")
    spec_file = _spec_file(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(spec_file),
            "--source",
            f"file://{ticket}",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    from orchestrator.sdlc.builddoc import load_source_text

    assert "skips skus without a price" in load_source_text("PROJ-42", root=checkout)
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert "Cart.total raises KeyError for an unknown sku" in document  # the spec's title, not the ticket's


@pytest.mark.parametrize("command", ["plan", "approve"])
def test_a_plan_can_only_be_written_where_the_gate_reads_it(command: str, tmp_path: Path) -> None:
    """Ledger B17 → N11: `--out` wrote plans and approvals where `require_approved_plan` never
    looks. Deprecated in 3.44, removed in 3.45 — asking for it is now an error, not a plan that
    can never be built."""
    args = (
        ["sdlc", "plan", "--spec", str(_spec_file(tmp_path))]
        if command == "plan"
        else ["sdlc", "approve", "PROJ-42"]
    )
    # CI forces colour (rich switches it on under GITHUB_ACTIONS) and a narrow panel wraps the
    # message across boxed lines — read the words, not the rendering.
    result = CliRunner().invoke(app, [*args, "--out", str(tmp_path / "elsewhere")], env={"COLUMNS": "200"})
    assert result.exit_code == 2
    plain = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).replace("│", " ").split())
    assert "No such option" in plain and "--out" in plain


def test_a_bug_that_lands_nowhere_keeps_its_approval(checkout: Path, tmp_path: Path) -> None:
    """Typed `Bug`, §12's validity row reads UNLOCALIZED; re-derived untyped it reads PROCEED.

    `.spine/` is excluded through `.git/info/exclude` so this measures B18 alone, not B16.
    """
    (checkout / ".git" / "info" / "exclude").write_text(".spine/\n", encoding="utf-8")
    spec_file = _spec_file(
        tmp_path,
        title="Zqxv flurble wibbles",
        summary="The flurble wibbles zqxv.",
        acceptance_criteria=["No flurble wibbles."],
    )
    _plan_and_approve(CliRunner(), spec_file, checkout, "--issue-type", "Bug")
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert "**Validity:** UNLOCALIZED" in document  # the fixture reaches the diverging verdict
    assert _gate(spec_file, checkout) == "PASSED: reviewer"


def test_a_spec_and_a_ticket_that_disagree_are_planned_as_the_spec_and_said_so(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D13: `PROJ-42.json` with `jira://PROJ-43` plans and keys the approval as PROJ-42 — and warns.

    The tracker is stubbed: this must never reach a real Jira through a developer's `.env`.
    """
    from orchestrator.intake.source import FetchTreeResult, SourceDocument

    fetched: list[str] = []

    class _Service:
        async def fetch_source_documents(self, root_id: str) -> FetchTreeResult:
            fetched.append(root_id)
            return FetchTreeResult(documents=[SourceDocument(id=root_id, title=root_id, body="PROJ-43 text")])

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Service())
    spec_file = _spec_file(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(spec_file),
            "--source",
            "jira://PROJ-43",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert fetched == ["PROJ-43"]
    assert "WARNING" in result.output and "PROJ-42" in result.output and "PROJ-43" in result.output
    assert (checkout / ".spine" / "plans" / "PROJ-42-build.md").is_file()


def test_an_approval_written_before_it_carried_a_type_still_loads_and_holds(
    checkout: Path, tmp_path: Path
) -> None:
    """Approval JSON on disk today has no `issue_type`: it must load, and re-derive untyped as before."""
    spec_file = _spec_file(tmp_path)
    _plan_and_approve(CliRunner(), spec_file, checkout)
    approval = checkout / ".spine" / "plans" / "PROJ-42-approval.json"
    payload = json.loads(approval.read_text(encoding="utf-8"))
    recorded = payload.pop("issue_type")
    assert recorded == ""
    approval.write_text(json.dumps(payload), encoding="utf-8")
    assert _gate(spec_file, checkout) == "PASSED: reviewer"


def test_the_ticket_text_a_plan_checks_against_is_the_whole_ticket(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Track E, E1: `source.txt` carries the full view, not the extractor's bounded one."""
    from orchestrator.intake.source import FetchTreeResult, SourceDocument
    from orchestrator.sdlc.builddoc import load_source_text

    class _Service:
        async def fetch_source_documents(self, root_id: str) -> FetchTreeResult:
            doc = SourceDocument(
                id=root_id, title=root_id, body="bounded …[truncated]", full_body="the whole attachment"
            )
            return FetchTreeResult(documents=[doc])

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Service())
    spec_file = _spec_file(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(spec_file),
            "--source",
            "jira://PROJ-42",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_source_text("PROJ-42", root=checkout) == "the whole attachment"


def test_a_cached_ticket_is_planned_from_its_cached_spec_and_its_fresh_text(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Track E, D3: the spec comes from the intake cache — re-extracting it could move an approved
    plan — while `source.txt` is read fresh, with no model call."""
    import orchestrator.intake.cache as intake_cache
    from orchestrator.intake.service import BacklogPlan
    from orchestrator.intake.source import FetchTreeResult, SourceDocument
    from orchestrator.intake.specs import FeatureSpec
    from orchestrator.sdlc.builddoc import load_source_text

    cached_spec = FeatureSpec.model_validate({**_SPEC, "user_story": "", "summary": _SPEC["summary"]})

    async def _cached(*_a: object, **_k: object) -> BacklogPlan:
        stale = SourceDocument(id="PROJ-42", title="PROJ-42", body="what the ticket said when first cached")
        return BacklogPlan(documents=[stale], specs=[cached_spec])

    class _Service:
        async def fetch_source_documents(self, root_id: str) -> FetchTreeResult:
            return FetchTreeResult(
                documents=[SourceDocument(id=root_id, title=root_id, body="what it says now")]
            )

    monkeypatch.setattr(intake_cache, "analyze_cached", _cached)
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Service())
    result = CliRunner().invoke(
        app, ["sdlc", "plan", "--source", "jira://PROJ-42", "--path", str(checkout), "--quiet"]
    )
    assert result.exit_code == 0, result.output
    assert load_source_text("PROJ-42", root=checkout) == "what it says now"
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert str(_SPEC["title"]) in document  # the cached spec, unchanged


def test_a_source_that_cannot_be_read_is_an_error_not_a_traceback(checkout: Path, tmp_path: Path) -> None:
    """Ledger N12: `file://missing.md` escaped as an uncaught `FileSourceError`."""
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            f"file://{tmp_path / 'missing.md'}",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 2
    assert "ERROR: could not read" in result.output and "missing.md" in result.output
    assert isinstance(result.exception, SystemExit)  # a clean exit, not an uncaught error


def test_a_source_that_returns_nothing_says_so(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger N13: an empty source planned silently, as if the ticket said nothing."""
    from orchestrator.intake.source import FetchTreeResult

    class _Empty:
        async def fetch_source_documents(self, root_id: str) -> FetchTreeResult:
            return FetchTreeResult(documents=[])

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Empty())
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            "openspec://nochange",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "WARNING: openspec://nochange returned no text" in result.output


class _LinkedService:
    """A ticket whose linked pages `--follow-links` reads; records whether it was asked to."""

    def __init__(self) -> None:
        self.asked: list[bool] = []

    async def fetch_source_documents(self, root_id: str, *, follow_links: bool = False) -> Any:
        from orchestrator.intake.source import FetchTreeResult, SourceDocument

        self.asked.append(follow_links)
        docs = [SourceDocument(id=root_id, title=root_id, body="the ticket")]
        if not follow_links:
            return FetchTreeResult(documents=docs)
        from orchestrator.intake.follow_links import FollowReport

        page = SourceDocument(
            id="confluence:9", title="Linked page: Spec", body="- a criterion from the page"
        )
        report = FollowReport(documents=[page])
        return FetchTreeResult(documents=[*docs, page], linked_pages=report.summary(), follow=report)


@pytest.mark.parametrize(
    ("flags", "header"),
    [
        ([], "**Linked pages:** not followed — `--follow-links` reads them"),
        (
            ["--follow-links"],
            "**Linked pages:** followed — 1 read into source.txt; "
            "the spec is hand-written, so none was extracted",
        ),
    ],
)
def test_the_header_says_whether_linked_pages_were_read(
    flags: list[str], header: str, checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Track E, D6: a reviewer sees in the document whether linked pages were part of it."""
    from orchestrator.sdlc.builddoc import load_source_text

    service = _LinkedService()
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: service)
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            "jira://PROJ-42",
            "--path",
            str(checkout),
            "--quiet",
            *flags,
        ],
    )
    assert result.exit_code == 0, result.output
    document = (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")
    assert header in document
    assert "WARNING" not in result.output  # a hand-written spec was never extracted, so nothing was cut
    assert service.asked == [bool(flags)]
    assert ("a criterion from the page" in load_source_text("PROJ-42", root=checkout)) is bool(flags)


def test_following_links_without_confluence_access_is_refused(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D12: the refusal reaches the user as an error that says what to configure, exit 2."""
    from orchestrator.intake.factory import IntakeNotConfiguredError

    class _NoWiki:
        async def fetch_source_documents(self, root_id: str, *, follow_links: bool = False) -> Any:
            raise IntakeNotConfiguredError("Confluence not configured: set CONFLUENCE_BASE_URL …")

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _NoWiki())
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            "jira://PROJ-42",
            "--path",
            str(checkout),
            "--quiet",
            "--follow-links",
        ],
    )
    assert result.exit_code == 2
    assert "Confluence not configured" in result.output
    assert not (checkout / ".spine" / "plans" / "PROJ-42-build.md").exists()


def test_investigate_reads_linked_pages_only_when_asked(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    service = _LinkedService()
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: service)
    briefs = []
    for flags in ([], ["--follow-links"]):
        result = CliRunner().invoke(app, ["investigate", str(checkout), "--source", "jira://PROJ-42", *flags])
        assert result.exit_code == 0, result.output
        briefs.append(result.output)
    assert service.asked == [False, True]
    # N14 (D4): the brief says what was followed — investigate has no budget, so no fit clause.
    assert "**Linked pages:**" not in briefs[0]
    assert "**Linked pages:** followed — 1 read" in briefs[1]


def test_a_source_that_cannot_be_read_is_an_error_on_every_path(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2: with `--source` alone and a cold cache, the intake analysis reads the
    source first — the fix covered only `--spec` + `--source`. `investigate` had the same gap."""
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "cold-cache"))
    missing = f"file://{tmp_path / 'missing.md'}"
    runs = {
        "plan": ["sdlc", "plan", "--source", missing, "--path", str(checkout), "--quiet"],
        "investigate": ["investigate", str(checkout), "--source", missing],
    }
    for name, args in runs.items():
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 2, (name, result.output)
        assert "ERROR: could not read" in result.output, name
        assert isinstance(result.exception, SystemExit), name


def test_a_bug_in_our_own_code_is_not_reported_as_an_unreadable_source(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 8: catching `RuntimeError`/`ValueError` turned a programming error into
    "could not read" with no traceback. Only the named source failures are an ERROR."""

    class _Buggy:
        async def fetch_source_documents(self, root_id: str, **_k: object) -> Any:
            raise ValueError("a bug in an adapter")

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Buggy())
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            "jira://PROJ-42",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert isinstance(result.exception, ValueError)
    assert "could not read" not in result.output


def test_a_blank_page_warns_like_an_empty_source(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 9: N13 checked for no documents, not for no text."""
    from orchestrator.intake.source import FetchTreeResult, SourceDocument

    class _Blank:
        async def fetch_source_documents(self, root_id: str, **_k: object) -> Any:
            return FetchTreeResult(documents=[SourceDocument(id="1", title="Spec", body="   \n")])

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Blank())
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--source",
            "confluence://1",
            "--path",
            str(checkout),
            "--quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "WARNING: confluence://1 returned no text" in result.output


# ---- what the model saw (N14) ----------------------------------------------------------------

_WIKI = "https://acme.atlassian.net/wiki/spaces/ENG/pages"


def _plan_from_cache(
    monkeypatch: pytest.MonkeyPatch,
    checkout: Path,
    *,
    ticket_chars: int,
    page_chars: int,
    pages: int,
    flags: list[str],
    extracts_with_model: bool = True,
) -> Any:
    """`sdlc plan --source` whose spec comes from a cached extraction of exactly these documents,
    and whose fresh fetch returns the same ones — so the header can only be computed from the fit."""
    import orchestrator.intake.cache as intake_cache
    from orchestrator.intake.follow_links import FollowReport
    from orchestrator.intake.service import BacklogPlan
    from orchestrator.intake.source import FetchTreeResult, SourceDocument
    from orchestrator.intake.specs import FeatureSpec

    ticket = SourceDocument(id="PROJ-42", title="PROJ-42", body="t" * ticket_chars)
    linked = [
        SourceDocument(
            id=f"confluence:{i}", title=f"Linked page: P{i}", body="p" * page_chars, url=f"{_WIKI}/{i}"
        )
        for i in range(pages)
    ]
    spec = FeatureSpec.model_validate({**_SPEC, "user_story": "", "summary": _SPEC["summary"]})

    async def _cached(*_a: object, **_k: object) -> BacklogPlan:
        return BacklogPlan(documents=[ticket, *linked], specs=[spec])

    class _Service:
        # An OpenSpec source parses its changes verbatim: no model, so no budget to cut.
        uses_the_extractor = extracts_with_model

        async def fetch_source_documents(
            self, root_id: str, *, follow_links: bool = False
        ) -> FetchTreeResult:
            if not follow_links:
                return FetchTreeResult(documents=[ticket])
            report = FollowReport(documents=linked)
            return FetchTreeResult(documents=[ticket, *linked], linked_pages=report.summary(), follow=report)

    monkeypatch.setattr(intake_cache, "analyze_cached", _cached)
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: _Service())
    return CliRunner().invoke(
        app, ["sdlc", "plan", "--source", "jira://PROJ-42", "--path", str(checkout), "--quiet", *flags]
    )


def _document(checkout: Path) -> str:
    return (checkout / ".spine" / "plans" / "PROJ-42-build.md").read_text(encoding="utf-8")


def test_linked_pages_the_extraction_left_out_are_counted_named_and_warned_about(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N14: a full ticket and five long pages — the header said "5 read" while the spec was derived
    from one page, cut."""
    result = _plan_from_cache(
        monkeypatch, checkout, ticket_chars=32_000, page_chars=30_000, pages=5, flags=["--follow-links"]
    )
    assert result.exit_code == 0, result.output
    header = next(line for line in _document(checkout).splitlines() if line.startswith("**Linked pages:**"))
    assert header == (
        f"**Linked pages:** followed — 5 read; 1 cut ({_WIKI}/0), 4 did not fit the 60,000-char budget "
        f"({_WIKI}/1, {_WIKI}/2, {_WIKI}/3, {_WIKI}/4)"
    )
    assert "**Extraction:**" not in _document(checkout)  # the ticket itself fitted
    assert "WARNING: the spec was extracted from part of the source — " in result.output
    assert "source.txt still holds every word" in result.output


def test_pages_that_all_reached_the_model_say_so_and_warn_nothing(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _plan_from_cache(
        monkeypatch, checkout, ticket_chars=500, page_chars=500, pages=2, flags=["--follow-links"]
    )
    assert result.exit_code == 0, result.output
    assert "**Linked pages:** followed — 2 read, all 2 in the spec's extraction" in _document(checkout)
    assert "WARNING" not in result.output and "**Extraction:**" not in _document(checkout)


def test_a_ticket_the_extraction_cut_is_on_the_document_even_without_follow_links(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8/D9: the ticket's own text cut is recorded where the reviewer reads — shown only then."""
    result = _plan_from_cache(monkeypatch, checkout, ticket_chars=70_000, page_chars=0, pages=0, flags=[])
    assert result.exit_code == 0, result.output
    assert (
        "**Extraction:** PROJ-42 cut at the 60,000-char extraction budget; "
        "§8 still checks the criteria against every word of it in source.txt"
    ) in _document(checkout)
    assert "WARNING: the spec was extracted from part of the source — PROJ-42 cut" in result.output


def test_a_plan_with_an_extraction_line_is_the_plan_the_gate_accepts(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Header, not body: the gate re-derives the plan without the line, and must still pass it."""
    result = _plan_from_cache(monkeypatch, checkout, ticket_chars=70_000, page_chars=0, pages=0, flags=[])
    assert result.exit_code == 0, result.output
    assert "**Extraction:**" in _document(checkout)
    approved = CliRunner().invoke(
        app, ["sdlc", "approve", "PROJ-42", "--path", str(checkout), "--by", "reviewer"]
    )
    assert approved.exit_code == 0, approved.output
    assert _gate(_spec_file(tmp_path), checkout) == "PASSED: reviewer"


def test_a_structured_source_is_parsed_not_extracted_so_nothing_is_reported_cut(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, both passes: an OpenSpec change is parsed verbatim, with no model and no budget — a
    70,000-char one was reported "cut", in a warning and on the document a reviewer approves."""
    result = _plan_from_cache(
        monkeypatch, checkout, ticket_chars=70_000, page_chars=0, pages=0, flags=[], extracts_with_model=False
    )
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output and "**Extraction:**" not in _document(checkout)


# ---- `sdlc plan --refresh` (B37, D1–D4) ------------------------------------------------------


class _Extractor:
    """A ticket intake extracts, through the real intake cache: `analyze` is the model call, and
    what it returns is whatever the test set last — so a re-extraction can move the spec or not."""

    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.intent = {False: "intent-cart-total", True: "intent-cart-total"}
        self.criteria = {False: ["Cart.total skips unpriced skus"], True: ["Cart.total skips unpriced skus"]}
        # Fields of the first spec beyond its criteria — some the plan renders, some it does not.
        self.extra: dict[bool, dict[str, Any]] = {False: {}, True: {}}
        # Further intents of the ticket: id → criteria.
        self.others: dict[bool, dict[str, list[str]]] = {False: {}, True: {}}
        self.fetch_error: Exception | None = None

    async def analyze(self, root_id: str, *, follow_links: bool = False) -> Any:
        from orchestrator.intake.intents import Intent
        from orchestrator.intake.service import BacklogPlan
        from orchestrator.intake.source import SourceDocument
        from orchestrator.intake.specs import FeatureSpec

        self.calls.append(follow_links)
        first = (self.intent[follow_links], list(self.criteria[follow_links]))
        title = "Cart.total raises KeyError for an unknown sku"
        every = [first, *((iid, list(c)) for iid, c in self.others[follow_links].items())]
        titles = [title, *(f"{title} ({iid})" for iid in self.others[follow_links])]
        return BacklogPlan(
            documents=[SourceDocument(id=root_id, title=root_id, body="the ticket")],
            intents=[
                Intent(id=iid, title=titles[n], description="d", acceptance_criteria=criteria)
                for n, (iid, criteria) in enumerate(every)
            ],
            specs=[
                FeatureSpec(
                    intent_id=iid,
                    title=titles[n],
                    acceptance_criteria=criteria,
                    **(self.extra[follow_links] if n == 0 else {}),
                )
                for n, (iid, criteria) in enumerate(every)
            ],
        )

    async def fetch_source_documents(self, root_id: str, *, follow_links: bool = False) -> Any:
        from orchestrator.intake.source import FetchTreeResult, SourceDocument

        if self.fetch_error is not None:
            raise self.fetch_error
        return FetchTreeResult(documents=[SourceDocument(id=root_id, title=root_id, body="the ticket")])


@pytest.fixture
def extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Extractor:
    service = _Extractor()
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "intake-cache"))
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *_a, **_k: service)
    return service


def _plan_source(root: Path, *extra: str) -> Any:
    return CliRunner().invoke(
        app, ["sdlc", "plan", "--source", "jira://PROJ-42", "--path", str(root), "--quiet", *extra]
    )


def _approve(root: Path, intent: str = "intent-cart-total") -> None:
    approved = CliRunner().invoke(app, ["sdlc", "approve", intent, "--path", str(root), "--by", "reviewer"])
    assert approved.exit_code == 0, approved.output


def _cached_criteria(*, follow_links: bool) -> list[str]:
    from orchestrator.intake.cache import FOLLOW_LINKS, load_cached_plan

    plan = load_cached_plan("jira://PROJ-42", variant=FOLLOW_LINKS if follow_links else "")
    assert plan is not None
    return list(plan.specs[0].acceptance_criteria)


def _build_doc(root: Path, intent: str = "intent-cart-total") -> str:
    return (root / ".spine" / "plans" / f"{intent}-build.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("follow", [True, False])
def test_refresh_re_extracts_only_the_entry_its_flags_select(
    follow: bool, checkout: Path, extractor: _Extractor
) -> None:
    """B37: nothing re-extracted a `--follow-links` spec — `ingest --refresh` rewrote the flag-off
    entry and left the variant as it was. D3: the entry the flags select, and only that one."""
    links = ["--follow-links"] if follow else []
    assert _plan_source(checkout).exit_code == 0
    assert _plan_source(checkout, "--follow-links").exit_code == 0
    extractor.criteria[follow] = ["Cart.total skips unpriced skus", "and logs the sku"]

    result = _plan_source(checkout, "--refresh", *links)

    assert result.exit_code == 0, result.output
    assert extractor.calls == [False, True, follow]
    assert _cached_criteria(follow_links=follow) == ["Cart.total skips unpriced skus", "and logs the sku"]
    assert _cached_criteria(follow_links=not follow) == ["Cart.total skips unpriced skus"]
    assert "and logs the sku" in _build_doc(checkout)


def test_refresh_with_a_hand_written_spec_is_refused(
    checkout: Path, tmp_path: Path, extractor: _Extractor
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "sdlc",
            "plan",
            "--spec",
            str(_spec_file(tmp_path)),
            "--path",
            str(checkout),
            "--quiet",
            "--refresh",
        ],
    )
    assert result.exit_code == 2
    assert "--refresh" in result.output and "--spec" in result.output
    assert extractor.calls == []


def _warnings(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("WARNING:")]


def test_a_refresh_that_changes_an_approved_spec_says_so_and_the_gate_refuses(
    checkout: Path, extractor: _Extractor
) -> None:
    """D4(a): the plan is re-rendered (its header says stale) and a WARNING names the approver,
    that autorun parks with exit 6 — because the re-rendered plan's digest moved, the comparison
    the gate makes — and that the cache is shared with other checkouts."""
    from orchestrator.sdlc.builddoc import PlanNotApprovedError, require_approved_plan

    assert _plan_source(checkout, "--follow-links").exit_code == 0
    _approve(checkout)
    extractor.criteria[True] = ["Cart.total skips unpriced skus", "and logs the sku"]

    result = _plan_source(checkout, "--refresh", "--follow-links")

    assert result.exit_code == 0, result.output
    assert "**stale** — approved by reviewer" in _build_doc(checkout)
    changed, shared, moved = _warnings(result.output)  # the digest is known only once rendered
    assert "changed the spec of intent-cart-total that reviewer approved" in changed
    assert "whether the approval still holds" in changed and "exit 6" not in changed
    assert "the re-rendered plan of intent-cart-total is not the one reviewer approved" in moved
    assert "`sdlc autorun --follow-links` for it parks (exit 6)" in moved
    assert f"`sdlc approve intent-cart-total --path {checkout}` again" in moved
    assert "shared by every checkout" in shared and "with `--follow-links` now reads the new spec" in shared
    # Approvals do not say which entry they were read from, so the other flag's is named.
    assert "the plan without `--follow-links`" in shared and "`sdlc autorun`" in shared
    from orchestrator.intake.cache import FOLLOW_LINKS, load_cached_plan

    fresh = load_cached_plan("jira://PROJ-42", variant=FOLLOW_LINKS)
    assert fresh is not None
    with pytest.raises(PlanNotApprovedError, match="changed since"):
        asyncio.run(require_approved_plan(fresh.specs[0].model_dump(), root=checkout))


def test_a_refresh_that_changes_only_what_the_plan_does_not_render_says_the_approval_holds(
    checkout: Path, extractor: _Extractor
) -> None:
    """Review pass 1: the plan's digest covers the rendered sections, not every spec field — a
    re-extraction that moves only `nfrs` or `estimate` leaves the gate passing, so claiming that
    autorun parks (exit 6) was false."""
    from orchestrator.intake.cache import load_cached_plan
    from orchestrator.sdlc.builddoc import require_approved_plan

    assert _plan_source(checkout).exit_code == 0
    _approve(checkout)
    extractor.extra[False] = {"nfrs": ["p99 under 50ms"], "estimate": "2d"}

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 0, result.output
    assert "exit 6" not in result.output
    assert "changed the spec of intent-cart-total that reviewer approved" in _warnings(result.output)[0]
    assert (
        "[plan] the re-rendered plan of intent-cart-total reads the same as the one reviewer approved"
        in result.output
    )
    assert "the approval still holds" in result.output
    assert "**approved** by reviewer" in _build_doc(checkout)
    fresh = load_cached_plan("jira://PROJ-42")
    assert fresh is not None and fresh.specs[0].nfrs == ["p99 under 50ms"]
    assert (
        asyncio.run(require_approved_plan(fresh.specs[0].model_dump(), root=checkout)).decided_by
        == "reviewer"
    )


def test_a_refresh_names_every_approved_intent_it_changed_and_how_to_re_plan_it(
    checkout: Path, extractor: _Extractor
) -> None:
    """Review pass 1: for an approved intent this command does not render, `sdlc approve` would
    digest the document on disk — rendered from the old spec — so the advice is to re-plan it
    first, and nothing is claimed about exit 6 until it has been."""
    extractor.others[False] = {"intent-cart-log": ["Cart.total logs the sku"]}
    assert _plan_source(checkout).exit_code == 0
    assert _plan_source(checkout, "--intent", "intent-cart-log").exit_code == 0
    _approve(checkout)
    _approve(checkout, "intent-cart-log")
    extractor.others[False] = {"intent-cart-log": ["Cart.total logs the sku", "at warning level"]}

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 0, result.output
    other, shared = _warnings(result.output)
    assert "changed the spec of intent-cart-log that reviewer approved" in other
    assert "rendered from the old spec" in other
    assert (
        f"`sdlc plan --source jira://PROJ-42 --intent intent-cart-log --path {checkout}`, then, if it "
        f"reads as stale, read it and `sdlc approve intent-cart-log --path {checkout}`"
    ) in other
    assert "exit 6" not in other and "now reads as stale" not in other
    assert "shared by every checkout" in shared
    # The rendered intent did not change, so nothing is said about it.
    assert not any("intent-cart-total" in line for line in _warnings(result.output))


@pytest.mark.parametrize("follow", [False, True])
def test_a_refresh_keeps_the_other_entry_and_every_recorded_pr(
    follow: bool, checkout: Path, extractor: _Extractor
) -> None:
    """D3: a refresh of one entry leaves the other's spec as it was, and the per-ticket progress
    — the PR recorded for each entry's intent — survives it, whichever entry was refreshed."""
    from orchestrator.intake.cache import FOLLOW_LINKS, load_cached_plan, load_progress, set_progress

    extractor.intent[True] = "intent-with-links"
    assert _plan_source(checkout).exit_code == 0
    assert _plan_source(checkout, "--follow-links").exit_code == 0
    set_progress("jira://PROJ-42", "intent-cart-total", status="in_progress", pr_url="https://x/pr/1")
    set_progress("jira://PROJ-42", "intent-with-links", status="in_progress", pr_url="https://x/pr/2")
    extractor.criteria[follow] = ["Cart.total skips unpriced skus", "and logs the sku"]

    result = _plan_source(checkout, "--refresh", *(["--follow-links"] if follow else []))

    assert result.exit_code == 0, result.output
    assert load_progress("jira://PROJ-42") == {
        "intent-cart-total": {"status": "in_progress", "pr_url": "https://x/pr/1"},
        "intent-with-links": {"status": "in_progress", "pr_url": "https://x/pr/2"},
    }
    other = load_cached_plan("jira://PROJ-42", variant="" if follow else FOLLOW_LINKS)
    assert other is not None
    assert [s.intent_id for s in other.specs] == ["intent-cart-total" if follow else "intent-with-links"]
    assert other.specs[0].acceptance_criteria == ["Cart.total skips unpriced skus"]


def test_a_cold_cache_refresh_still_renders_an_approval_it_moved_as_stale(
    checkout: Path, extractor: _Extractor, tmp_path: Path
) -> None:
    """With no cached spec to compare, nothing is warned — the header is what says it."""
    import shutil

    assert _plan_source(checkout).exit_code == 0
    _approve(checkout)
    shutil.rmtree(tmp_path / "intake-cache")
    extractor.criteria[False] = ["something else entirely"]

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output
    assert "**stale** — approved by reviewer" in _build_doc(checkout)


def test_a_refresh_that_leaves_the_spec_as_it_was_warns_nothing(
    checkout: Path, extractor: _Extractor
) -> None:
    """The warning compares the spec before and after, never the digest — a re-extraction that
    returns the same spec stales nothing and says nothing."""
    assert _plan_source(checkout).exit_code == 0
    _approve(checkout)

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 0, result.output
    assert extractor.calls == [False, False]
    assert "WARNING" not in result.output
    assert "**approved** by reviewer" in _build_doc(checkout)


def test_a_refresh_that_changes_an_unapproved_spec_warns_nothing(
    checkout: Path, extractor: _Extractor
) -> None:
    assert _plan_source(checkout).exit_code == 0
    extractor.criteria[False] = ["something else entirely"]

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.output
    assert "something else entirely" in _build_doc(checkout)


def test_a_refresh_that_renames_the_pinned_intent_exits_3_naming_it(
    checkout: Path, extractor: _Extractor
) -> None:
    """Intent ids come from the model's titles, so a re-extraction can rename the one `--intent`
    pins; today's exit 3, saying why."""
    assert _plan_source(checkout, "--intent", "intent-cart-total").exit_code == 0
    extractor.intent[False] = "intent-cart-total-skips-unpriced"

    result = _plan_source(checkout, "--refresh", "--intent", "intent-cart-total")

    assert result.exit_code == 3
    assert "'intent-cart-total' not found" in result.output
    assert "intent-cart-total-skips-unpriced" in result.output
    assert "re-extraction renamed or dropped it" in result.output


@pytest.mark.parametrize("follow", [False, True])
def test_a_refresh_that_renames_an_approved_intent_warns_naming_it(
    follow: bool, checkout: Path, extractor: _Extractor
) -> None:
    """A renamed intent is not found by `autorun --intent` (exit 3, not 6), and its approval does
    not carry to the new id. A flag-off refresh prunes progress to the intents still held, so the
    PR recorded for it goes too — said only then: a variant's refresh never touches progress."""
    from orchestrator.intake.cache import load_progress, set_progress

    links = ["--follow-links"] if follow else []
    assert _plan_source(checkout, *links).exit_code == 0
    _approve(checkout)
    set_progress("jira://PROJ-42", "intent-cart-total", status="in_progress", pr_url="https://x/pr/7")
    extractor.intent[follow] = "intent-cart-total-skips-unpriced"

    result = _plan_source(checkout, "--refresh", *links)

    assert result.exit_code == 0, result.output
    warning = _warnings(result.output)[0]
    assert "renamed or dropped intent-cart-total, which reviewer approved" in warning
    assert "the ticket's intents are now: intent-cart-total-skips-unpriced" in warning
    autorun = "sdlc autorun --follow-links" if follow else "sdlc autorun"
    assert f"`{autorun} --intent intent-cart-total` no longer finds it (exit 3)" in warning
    assert "exit 6" not in warning
    dropped = "Its recorded progress (in_progress, PR https://x/pr/7) was dropped with it."
    assert (dropped in warning) is (not follow)
    assert ("intent-cart-total" in load_progress("jira://PROJ-42")) is follow


def test_a_refresh_that_renames_the_pinned_approved_intent_warns_before_exit_3(
    checkout: Path, extractor: _Extractor
) -> None:
    """Review pass 1: the cache is rewritten inside the refresh, so its warning is said before
    any later exit — here the exit 3 for a pinned `--intent` the re-extraction renamed."""
    assert _plan_source(checkout, "--intent", "intent-cart-total").exit_code == 0
    _approve(checkout)
    extractor.intent[False] = "intent-cart-total-skips-unpriced"

    result = _plan_source(checkout, "--refresh", "--intent", "intent-cart-total")

    assert result.exit_code == 3
    assert "renamed or dropped intent-cart-total, which reviewer approved" in _warnings(result.output)[0]
    assert "shared by every checkout" in result.output


def test_a_refresh_whose_fresh_fetch_fails_still_warns(checkout: Path, extractor: _Extractor) -> None:
    from orchestrator.intake.jira import IssueTrackerError

    assert _plan_source(checkout).exit_code == 0
    _approve(checkout)
    extractor.criteria[False] = ["something else entirely"]
    extractor.fetch_error = IssueTrackerError("503 from the tracker")

    result = _plan_source(checkout, "--refresh")

    assert result.exit_code == 2
    assert "ERROR: could not read jira://PROJ-42" in result.output
    assert "changed the spec of intent-cart-total that reviewer approved" in _warnings(result.output)[0]
    assert "shared by every checkout" in result.output


def test_a_refresh_whose_render_fails_still_warns(
    checkout: Path, extractor: _Extractor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extraction is saved before the plan is rendered, so a render that fails must not take
    the warning with it."""
    assert _plan_source(checkout).exit_code == 0
    _approve(checkout)
    extractor.criteria[False] = ["something else entirely"]

    async def _boom(*_a: object, **_k: object) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr("orchestrator.sdlc.builddoc.build_plan", _boom)

    result = _plan_source(checkout, "--refresh")

    assert isinstance(result.exception, RuntimeError)
    assert "changed the spec of intent-cart-total that reviewer approved" in _warnings(result.output)[0]
    assert "shared by every checkout" in result.output
    assert _cached_criteria(follow_links=False) == ["something else entirely"]


def test_without_refresh_a_cached_spec_is_planned_as_before(checkout: Path, extractor: _Extractor) -> None:
    """The flag is inert unless given: a warm cache makes no model call, and the document is the
    one a refresh returning the same spec renders."""
    assert _plan_source(checkout).exit_code == 0
    first = _build_doc(checkout)
    again = _plan_source(checkout)
    assert again.exit_code == 0 and "WARNING" not in again.output
    assert extractor.calls == [False]
    assert _build_doc(checkout) == first
    assert _plan_source(checkout, "--refresh").exit_code == 0
    assert extractor.calls == [False, False]
    assert _build_doc(checkout) == first
