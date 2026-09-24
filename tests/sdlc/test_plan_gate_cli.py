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
        page = SourceDocument(
            id="confluence:9", title="Linked page: Spec", body="- a criterion from the page"
        )
        return FetchTreeResult(documents=[*docs, page], linked_pages="followed — 1 read")


@pytest.mark.parametrize(
    ("flags", "header"),
    [
        ([], "**Linked pages:** not followed — `--follow-links` reads them"),
        (["--follow-links"], "**Linked pages:** followed — 1 read"),
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
    for flags in ([], ["--follow-links"]):
        result = CliRunner().invoke(app, ["investigate", str(checkout), "--source", "jira://PROJ-42", *flags])
        assert result.exit_code == 0, result.output
    assert service.asked == [False, True]


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
