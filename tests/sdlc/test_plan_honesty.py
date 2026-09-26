"""The plan says what it could not establish (NSS-1231, NSS-1243).

NSS-1243's spec was the spec writer's invention — "a PSI reading above the threshold" in a
codebase where PSI is an identifier, "a constant in oil_status.js" in a C# repository — every
criterion proposed, none stated. The validity gate said PROCEED, the named file vanished from
the plan, and §12 rated the analysis **high**: the files had come from the plan's own keyword
match, so the checks that remained were true of almost any ticket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake.intents import Intent
from orchestrator.intake.specs import SpecWriter
from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch
from orchestrator.sdlc.builddoc import render_build_md
from orchestrator.sdlc.design import _test_strategy
from orchestrator.sdlc.spec_context import attach_repo_context, repo_context, repository_languages
from orchestrator.sdlc.validity import Finding, Verdict, assess

_NSS_1243: dict[str, Any] = {
    "intent_id": "intent-display-oil-quantity-as-yes-no-based-on-psi-data",
    "title": "Display Oil Quantity as Yes/No based on PSI data",
    "summary": (
        "Display the oil quantity as a Yes/No value determined by PSI data. The threshold for "
        "Yes/No will be set via a constant in oil_status.js."
    ),
    "acceptance_criteria": [],
    "proposed_criteria": [
        "Given a current PSI reading above the defined threshold, when the oil status is displayed, "
        "then the oil quantity shows 'Yes'."
    ],
}


def _blazor(root: Path) -> None:
    (root / "WebApp" / "Shared").mkdir(parents=True)
    (root / "WebApp" / "Shared" / "SSConstants.cs").write_text(
        "namespace Commercial.Secondary.Sales.Shared;\npublic static class SSConstants {\n"
        '    public const string OR_OilQuantity = "Oil Quantity";\n}\n',
        encoding="utf-8",
    )


def _Store() -> FactStore:  # noqa: N802 — reads like the double it replaces
    """An empty graph: the gate's count checks have nothing to contradict."""
    return FactStore(FactBatch())


# --- P7: the gate reports the invention -----------------------------------------------------


def test_a_missing_file_in_another_language_is_flagged(tmp_path: Path) -> None:
    _blazor(tmp_path)

    result = assess(_NSS_1243, store=_Store(), root=tmp_path, language="csharp")

    assert result.verdict is Verdict.PROCEED  # reported, never a refusal on its own
    checks = {f.check: f.detail for f in result.findings}
    assert "`oil_status.js`" in checks["named_path_other_language"]
    assert "JavaScript" in checks["named_path_other_language"]
    assert "every one in §8 was proposed" in checks["no_stated_criteria"]


@pytest.mark.parametrize(
    ("summary", "language"),
    [
        ("The threshold lives in oil_status.js.", "typescript"),  # JS is TypeScript's own
        ("Add OilStatusHelper.cs for the threshold.", "csharp"),  # to create, same language
        ("The threshold lives in oil_status.js.", ""),  # no language given: nothing to compare
        ("Serve it the way our Node.js gateway does.", "csharp"),  # a runtime, not a file
    ],
)
def test_no_other_language_finding_when_the_name_is_plausible(
    tmp_path: Path, summary: str, language: str
) -> None:
    _blazor(tmp_path)
    spec = {**_NSS_1243, "summary": summary, "acceptance_criteria": ["It works."]}

    result = assess(spec, store=_Store(), root=tmp_path, language=language)

    assert not result.findings


def test_an_existing_file_in_another_language_is_not_an_invention(tmp_path: Path) -> None:
    _blazor(tmp_path)
    (tmp_path / "WebApp" / "wwwroot" / "js").mkdir(parents=True)
    (tmp_path / "WebApp" / "wwwroot" / "js" / "oil_status.js").write_text("export const T = 1;\n")

    result = assess(_NSS_1243, store=_Store(), root=tmp_path, language="csharp")

    assert "named_path_other_language" not in {f.check for f in result.findings}


# --- P7 + P8: the document shows it, and the band reflects it --------------------------------


class _Validity:
    def __init__(self, findings: list[Finding]) -> None:
        self.verdict = Verdict.PROCEED
        self.findings = findings


def _render(root: Path, spec: dict[str, Any], *, files: list[str], origin: str, validity: Any) -> str:
    return render_build_md(
        spec,
        investigation=type("I", (), {"landing": [], "areas": []})(),
        design={"files_to_touch": files, "files_origin": origin, "llm": False, "blast_radius": {}},
        validity=validity,
        root=root,
        commit="abc1234",
        context_budget=200_000,
        language="csharp",
    )


def test_the_nss_1243_plan_is_no_longer_rated_high(tmp_path: Path) -> None:
    _blazor(tmp_path)
    validity = assess(_NSS_1243, store=_Store(), root=tmp_path, language="csharp")

    md = _render(
        tmp_path, _NSS_1243, files=["WebApp/Shared/SSConstants.cs"], origin="landing", validity=validity
    )

    section_7 = md.split("## 7. Files", 1)[1].split("## 8.", 1)[0]
    assert "**Named but absent**" in section_7 and "`oil_status.js`" in section_7
    assert "JavaScript, not csharp" in section_7
    confidence = md.split("## 12. Confidence", 1)[1]
    assert "Is the analysis right? — high" not in confidence
    assert "| Stated criteria | none stated" in confidence
    assert "| Files the spec names | a file in another language" in confidence


def test_a_plan_with_no_files_is_low_whatever_else_holds(tmp_path: Path) -> None:
    spec = {**_NSS_1243, "summary": "Do the thing.", "acceptance_criteria": ["It works."]}

    md = _render(tmp_path, spec, files=[], origin="none", validity=_Validity([]))

    assert "**Is the analysis right? — low**" in md
    assert "**Capped at low:** the design proposes no files" in md


def test_a_same_language_file_to_create_is_listed_not_dropped(tmp_path: Path) -> None:
    _blazor(tmp_path)
    spec = {**_NSS_1243, "summary": "Add WebApp/Shared/OilStatusHelper.cs.", "acceptance_criteria": ["x"]}

    md = _render(
        tmp_path, spec, files=["WebApp/Shared/SSConstants.cs"], origin="stated", validity=_Validity([])
    )

    assert (
        "| `WebApp/Shared/OilStatusHelper.cs` | named by the spec, not in the repository — a file to create"
        in md
    )


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"acceptance_criteria": ["A.", "B."]}, "Add tests covering each acceptance criterion: A.; B."),
        ({"acceptance_criteria": [], "proposed_criteria": ["P."]}, "confirm them first: P."),
        ({"acceptance_criteria": [], "proposed_criteria": [{"criterion": "Q."}]}, "confirm them first: Q."),
        ({}, "derive the tests from the requirement"),
    ],
)
def test_the_test_strategy_never_ends_on_a_colon(spec: dict[str, Any], expected: str) -> None:
    strategy = _test_strategy(spec)
    assert expected in strategy and not strategy.rstrip().endswith(":")


# --- P7: the spec writer is told what the repository is -------------------------------------


class _LLM:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def complete(self, messages: list[Any], **_: Any) -> Any:
        self.messages = messages
        return type("R", (), {"tool_calls": [], "text": '{"summary": "s", "acceptance_criteria": []}'})()


_INTENT = Intent(
    id="i-1",
    title="Display Oil Quantity as Yes/No based on PSI data",
    description="Show Yes/No from PSI.",
)


async def test_the_spec_writer_carries_the_repository_context_and_the_grounding_rule() -> None:
    llm = _LLM()
    writer = SpecWriter(llm, model="m")
    seen: list[str] = []

    def context_for(text: str) -> str:
        seen.append(text)
        return "REPOSITORY CONTEXT (read from the checkout, not the ticket):\n- Written in: C# (400 files)"

    writer.context_for = context_for
    await writer.write(_INTENT)

    system, user = llm.messages[0].content, llm.messages[1].content
    assert "GROUNDING:" in system and "never propose a file in a language" in system
    assert "- Written in: C# (400 files)" in user
    assert seen and seen[0].startswith("Display Oil Quantity")


async def test_a_context_that_fails_never_stops_the_spec() -> None:
    llm = _LLM()
    writer = SpecWriter(llm, model="m")

    def broken(_text: str) -> str:
        raise RuntimeError("graph unavailable")

    writer.context_for = broken
    spec = await writer.write(_INTENT)

    assert spec.intent_id == "i-1"
    assert "REPOSITORY CONTEXT" not in llm.messages[1].content


def test_repository_languages_are_counted_with_the_extractor_rules(tmp_path: Path) -> None:
    _blazor(tmp_path)
    (tmp_path / "WebApp" / "Home.razor").write_text("<h1/>\n")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "a.js").write_text("")

    assert repository_languages(tmp_path) == [("C#", 2)]


def test_repo_context_names_the_symbols_the_ticket_matches(tmp_path: Path) -> None:
    (tmp_path / "psi.py").write_text("class PsiReading:\n    local_id: str = ''\n", encoding="utf-8")

    block = repo_context(tmp_path)("Display PSI reading\nShow the PSI reading as Yes/No.")

    assert block.startswith("REPOSITORY CONTEXT")
    assert "- Written in: Python (1 files)" in block
    assert "`PsiReading`" in block


def test_a_service_without_the_setter_is_left_alone() -> None:
    attach_repo_context(object(), ".")  # no attribute error: a test double or older service
