"""Render a traceable, tabular requirements report as Confluence storage XHTML.

One page, four numbered tables that chain together so any row can be traced
back to the source Confluence page:

  Intents (I-NN)  →  Requirements (R-NN)  →  Design Specs (S-NN)  →  Token Audit

Intents and requirements are 1:1 (a requirement is the formal restatement of
an intent); design specs are the *buildable* subset — the engineering items
that get acceptance/test criteria, an estimate, and a Jira tracking issue.
The token-audit table reports per-stage LLM consumption for the run that
produced the page.

Pure rendering: callers pass plain dicts (``FeatureSpec.model_dump()`` /
``Intent.model_dump()``) plus a ``TokenLedger``; this module returns a string.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

from orchestrator.core.llm.recording import TokenLedger


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "")).strip()


def _ul(items: Sequence[Any]) -> str:
    rows = [f"<li>{_esc(i)}</li>" for i in items if _esc(i)]
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


def intent_number(index: int) -> str:
    return f"I-{index:02d}"


def requirement_number(index: int) -> str:
    return f"R-{index:02d}"


def spec_number(index: int) -> str:
    return f"S-{index:02d}"


def _source_link(url: str, title: str) -> str:
    if not url:
        return _esc(title)
    return f'<a href="{_esc(url)}">{_esc(title)}</a>'


def _intents_table(intents: Sequence[Mapping[str, Any]], source_cell: str) -> str:
    head = "<tr><th>#</th><th>Intent</th><th>Description</th><th>Open questions</th><th>Source</th></tr>"
    rows = []
    for i, it in enumerate(intents, 1):
        rows.append(
            f"<tr><td>{intent_number(i)}</td>"
            f"<td><strong>{_esc(it.get('title'))}</strong></td>"
            f"<td>{_esc(it.get('description'))}</td>"
            f"<td>{_ul(it.get('open_questions') or [])}</td>"
            f"<td>{source_cell}</td></tr>"
        )
    return f"<h2>1. Intents</h2><table><tbody>{head}{''.join(rows)}</tbody></table>"


def _requirements_table(
    intents: Sequence[Mapping[str, Any]],
    specs_by_intent: Mapping[str, Mapping[str, Any]],
) -> str:
    head = "<tr><th>#</th><th>Requirement</th><th>Traces to</th></tr>"
    rows = []
    for i, it in enumerate(intents, 1):
        spec = specs_by_intent.get(str(it.get("id")), {})
        # The formal requirement statement: prefer the spec's user story, fall
        # back to its summary, then the intent description.
        statement = spec.get("user_story") or spec.get("summary") or it.get("description")
        rows.append(
            f"<tr><td>{requirement_number(i)}</td><td>{_esc(statement)}</td><td>{intent_number(i)}</td></tr>"
        )
    return f"<h2>2. Requirements</h2><table><tbody>{head}{''.join(rows)}</tbody></table>"


def _design_specs_table(
    intents: Sequence[Mapping[str, Any]],
    specs_by_intent: Mapping[str, Mapping[str, Any]],
    buildable_intent_ids: Sequence[str],
    jira_keys: Mapping[str, str],
    jira_browse_base: str,
) -> str:
    head = (
        "<tr><th>#</th><th>Feature / Design spec</th><th>Traces to</th>"
        "<th>Summary</th><th>How to test / test criteria</th>"
        "<th>Est.</th><th>Jira</th></tr>"
    )
    # Map each intent to its I-NN/R-NN index for trace refs.
    index_by_id = {str(it.get("id")): i for i, it in enumerate(intents, 1)}
    buildable_set = set(buildable_intent_ids)
    rows = []
    s = 0
    for it in intents:
        iid = str(it.get("id"))
        if iid not in buildable_set:
            continue
        s += 1
        spec = specs_by_intent.get(iid, {})
        idx = index_by_id.get(iid, 0)
        key = jira_keys.get(iid, "")
        jira_cell = f'<a href="{_esc(jira_browse_base)}/browse/{_esc(key)}">{_esc(key)}</a>' if key else "—"
        rows.append(
            f"<tr><td>{spec_number(s)}</td>"
            f"<td><strong>{_esc(spec.get('title') or it.get('title'))}</strong></td>"
            f"<td>{intent_number(idx)}, {requirement_number(idx)}</td>"
            f"<td>{_esc(spec.get('summary'))}</td>"
            f"<td>{_ul(spec.get('acceptance_criteria') or [])}</td>"
            f"<td>{_esc(spec.get('estimate') or '—')}</td>"
            f"<td>{jira_cell}</td></tr>"
        )
    note = (
        "<p><em>Design specs are the buildable engineering subset; each carries "
        "test criteria and a Jira tracking issue. Non-buildable intents "
        "(decisions, business) appear above as intents/requirements only.</em></p>"
    )
    return (
        "<h2>3. Design specs / features</h2>" + note + f"<table><tbody>{head}{''.join(rows)}</tbody></table>"
    )


def _audit_table(ledger: TokenLedger) -> str:
    head = (
        "<tr><th>Stage</th><th>Model(s)</th><th>Calls</th>"
        "<th>Prompt tokens</th><th>Completion tokens</th><th>Total tokens</th>"
        "<th>Cost (USD)</th><th>Latency (s)</th></tr>"
    )
    rows = []
    for u in ledger.ordered():
        rows.append(
            f"<tr><td>{_esc(u.stage)}</td><td>{_esc(', '.join(u.models))}</td>"
            f"<td>{u.calls}</td><td>{u.prompt_tokens:,}</td>"
            f"<td>{u.completion_tokens:,}</td><td>{u.total_tokens:,}</td>"
            f"<td>${u.cost_usd:.4f}</td><td>{u.latency_ms / 1000:.1f}</td></tr>"
        )
    t = ledger.total()
    rows.append(
        f"<tr><td><strong>TOTAL</strong></td><td>{_esc(', '.join(t.models))}</td>"
        f"<td><strong>{t.calls}</strong></td><td><strong>{t.prompt_tokens:,}</strong></td>"
        f"<td><strong>{t.completion_tokens:,}</strong></td>"
        f"<td><strong>{t.total_tokens:,}</strong></td>"
        f"<td><strong>${t.cost_usd:.4f}</strong></td>"
        f"<td><strong>{t.latency_ms / 1000:.1f}</strong></td></tr>"
    )
    return f"<h2>4. Token audit (per pipeline leg)</h2><table><tbody>{head}{''.join(rows)}</tbody></table>"


def render_traceability_report(
    *,
    source_title: str,
    source_url: str,
    intents: Sequence[Mapping[str, Any]],
    specs: Sequence[Mapping[str, Any]],
    buildable_intent_ids: Sequence[str],
    jira_keys: Mapping[str, str],
    jira_browse_base: str,
    ledger: TokenLedger,
) -> str:
    """Render the full four-table traceability report as storage-format XHTML."""
    specs_by_intent = {str(s.get("intent_id")): s for s in specs}
    source_cell = _source_link(source_url, source_title)
    intro = (
        "<p>Auto-generated by the SDLC orchestrator. Every row traces back to the "
        f"source page {source_cell}: intents (I-NN) → requirements (R-NN) → "
        "design specs (S-NN) → Jira issues. The final table is the token-consumption "
        "audit for the run that produced this page.</p>"
        f"<p><strong>{len(intents)}</strong> intents · "
        f"<strong>{len(buildable_intent_ids)}</strong> buildable design specs.</p>"
    )
    return (
        intro
        + _intents_table(intents, source_cell)
        + _requirements_table(intents, specs_by_intent)
        + _design_specs_table(intents, specs_by_intent, buildable_intent_ids, jira_keys, jira_browse_base)
        + _audit_table(ledger)
    )


__all__ = [
    "intent_number",
    "render_traceability_report",
    "requirement_number",
    "spec_number",
]
