"""Block B.2 unit tests: IntentExtractor parsing + source attribution."""

from __future__ import annotations

import json as jsonlib

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.intake.intents import IntentExtractor
from orchestrator.intake.source import SourceDocument


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def _docs() -> list[SourceDocument]:
    return [
        SourceDocument(id="p1", title="Export feature", body="Users need CSV + JSON export."),
        SourceDocument(id="p2", title="Auth", body="SSO via Okta required."),
    ]


async def test_extract_parses_intents_with_fields() -> None:
    payload = {
        "intents": [
            {
                "title": "Add CSV export",
                "description": "Let users download data as CSV.",
                "scope": "CSV only; JSON is a separate intent.",
                "acceptance_criteria": ["export_csv(rows) -> Path writes a UTF-8 CSV"],
                "dependencies": ["data layer"],
                "nfrs": ["export <5s for 10k rows"],
                "open_questions": ["Which columns?"],
                "source_title": "Export feature",
            }
        ]
    }
    extractor = IntentExtractor(_llm_returning(jsonlib.dumps(payload)))
    intents = await extractor.extract(_docs())
    assert len(intents) == 1
    i = intents[0]
    assert i.id == "intent-add-csv-export"
    assert i.title == "Add CSV export"
    assert i.acceptance_criteria == ["export_csv(rows) -> Path writes a UTF-8 CSV"]
    assert i.dependencies == ["data layer"]
    assert i.nfrs == ["export <5s for 10k rows"]
    assert i.open_questions == ["Which columns?"]
    # source_title mapped back to the document id
    assert i.source_doc_ids == ["p1"]


async def test_extract_falls_back_to_all_doc_ids_when_source_unmapped() -> None:
    payload = {"intents": [{"title": "Mystery intent", "source_title": "Nonexistent doc"}]}
    extractor = IntentExtractor(_llm_returning(jsonlib.dumps(payload)))
    intents = await extractor.extract(_docs())
    assert intents[0].source_doc_ids == ["p1", "p2"]  # fallback = all inputs


async def test_extract_deduplicates_ids() -> None:
    payload = {
        "intents": [
            {"title": "Same title"},
            {"title": "Same title"},
        ]
    }
    extractor = IntentExtractor(_llm_returning(jsonlib.dumps(payload)))
    intents = await extractor.extract(_docs())
    ids = [i.id for i in intents]
    assert len(ids) == len(set(ids))  # unique
    assert ids[0] == "intent-same-title"


async def test_extract_skips_titleless_and_malformed() -> None:
    payload = {"intents": [{"description": "no title"}, "not a dict", {"title": "Keeper"}]}
    extractor = IntentExtractor(_llm_returning(jsonlib.dumps(payload)))
    intents = await extractor.extract(_docs())
    assert [i.title for i in intents] == ["Keeper"]


async def test_extract_degrades_on_garbage() -> None:
    extractor = IntentExtractor(_llm_returning("the model rambled, no json"))
    assert await extractor.extract(_docs()) == []


async def test_extract_empty_docs_skips_llm() -> None:
    extractor = IntentExtractor(_llm_returning('{"intents": [{"title": "should not appear"}]}'))
    empty = [SourceDocument(id="e", title="Empty", body="   ")]
    assert await extractor.extract(empty) == []


async def test_extract_tolerates_code_fence() -> None:
    payload = {"intents": [{"title": "Fenced intent"}]}
    fenced = "```json\n" + jsonlib.dumps(payload) + "\n```"
    extractor = IntentExtractor(_llm_returning(fenced))
    intents = await extractor.extract(_docs())
    assert intents[0].title == "Fenced intent"


# ---- what reaches the model (N14) -----------------------------------------------------------


def _ticket_and_pages(ticket_chars: int, page_chars: int, pages: int = 5) -> list[SourceDocument]:
    docs = [SourceDocument(id="FIN-42", title="Ticket", body="t" * ticket_chars)]
    docs += [
        SourceDocument(id=f"confluence:{i}", title=f"Linked page: P{i}", body="p" * page_chars)
        for i in range(pages)
    ]
    return docs


def test_the_fit_says_which_documents_reached_the_model() -> None:
    """N14: a full ticket and five long pages — the model sees one page, cut, and nothing said so."""
    from orchestrator.intake.intents import extraction_fit

    fit = extraction_fit(_ticket_and_pages(32_000, 30_000))
    assert [(d.id, d.state) for d in fit.documents] == [
        ("FIN-42", "full"),
        ("confluence:0", "cut"),
        ("confluence:1", "dropped"),
        ("confluence:2", "dropped"),
        ("confluence:3", "dropped"),
        ("confluence:4", "dropped"),
    ]
    assert fit.budget == 60_000 and not fit.complete


def test_the_fit_is_what_the_prompt_builder_sends() -> None:
    """One function for both sides: the message is exactly the fit's chunks, so a report computed
    from the fit cannot disagree with what the model was given."""
    from orchestrator.intake.intents import extraction_fit

    extractor = IntentExtractor.__new__(IntentExtractor)
    for ticket, page in [(5_000, 5_000), (32_000, 8_000), (32_000, 30_000), (60_000, 1_000), (59_990, 50)]:
        docs = _ticket_and_pages(ticket, page)
        fit = extraction_fit(docs)
        assert extractor._build_user_message(docs) == "Requirements documents:\n\n" + "\n\n---\n\n".join(
            fit.chunks
        )


def test_a_ticket_that_fits_reports_nothing_cut_and_empty_documents_are_not_sent() -> None:
    from orchestrator.intake.intents import extraction_fit

    fit = extraction_fit(
        [*_ticket_and_pages(1_000, 1_000, pages=2), SourceDocument(id="e", title="E", body="  ")]
    )
    assert fit.complete and [d.id for d in fit.documents] == ["FIN-42", "confluence:0", "confluence:1"]
