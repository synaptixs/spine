"""A deterministic score for an intake output (SSPN-204, P0 of the intake-quality plan).

Intake turns a ticket into the contract every later stage works from, and it used to be improved
one failure at a time. This module is the other half of the fix: a **contract for what a good intake
is, scored without a model**, so a change to intake is judged on a free, repeatable measure of intake
alone before it is judged on resolution (which is noisy: one run, a stochastic model, about nine
points of interval at a hundred issues).

Five properties, each taken from a real defect seen in the P6 and IR17 runs:

* **faithful** — a stated criterion is an exact span of the report (whitespace aside);
* **typed** — no code, session or output stands in the contract (the 32% of P6's criteria that did);
* **well-formed** — a stated criterion is a sentence, not a fragment ("which should be") or a question;
* **linked** — a derived criterion's ``[from: "…"]`` tag is a real quote of the report;
* **grounded** — a name the graph does not hold is classified by where it comes from (builtin, standard
  library, the reporter's own example, a path) instead of being left to park the run.

Pure functions, no I/O: the same text in, the same score out. The corpus loader and the report are in
``scripts/intake_quality.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Literal

from orchestrator.pkg.repro_material import (
    classify_name,
    is_reproduction,
    reporter_defined_names,
)

__all__ = [
    "IntakeScore",
    "classify_name",
    "criterion_form",
    "is_exact_span",
    "merge",
    "quote_in_source",
    "score_intake",
    "tag_status",
]

Form = Literal["sentence", "fragment", "question", "code"]
TagStatus = Literal["none", "verified", "unverified"]

_FROM_TAG = re.compile(r'\[from:\s*"(.*?)"\s*\]', re.DOTALL)
_PARAPHRASE_TAG = re.compile(r"\[from:\s*paraphrase", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.split())


def criterion_form(text: str) -> Form:
    """Is a stated criterion a sentence, a fragment, a question, or code copied from the report?"""
    t = text.strip()
    if not t:
        return "fragment"
    if is_reproduction(t):
        return "code"
    if t.endswith("?"):
        return "question"
    if t.endswith(":") or len(t.split()) < 5 or not (t[0].isupper() or t[0] in "`\"'("):
        return "fragment"
    return "sentence"


def is_exact_span(text: str, source: str) -> bool:
    """Does the report say ``text`` word for word? Whitespace is not a difference; empty is not a span."""
    needle = _norm(text)
    return bool(needle) and needle in _norm(source)


def quote_in_source(quote: str, source: str) -> bool:
    """Is ``quote`` in the report? JSON escapes are undone; ``...`` joins fragments that must each appear."""
    unescaped = quote.replace('\\"', '"').replace("\\n", " ")
    parts = [_norm(p) for p in re.split(r"\.\.\.|…", unescaped) if p.strip()]
    haystack = _norm(source)
    return bool(parts) and all(p in haystack for p in parts)


def tag_status(proposed: str, source: str) -> TagStatus:
    """``none`` with no ``[from: …]`` tag, ``verified`` if its quote is in the report, else ``unverified``."""
    match = _FROM_TAG.search(proposed)
    if match is None:
        return "unverified" if _PARAPHRASE_TAG.search(proposed) else "none"
    return "verified" if quote_in_source(match.group(1), source) else "unverified"


@dataclass(frozen=True)
class IntakeScore:
    """Counts over one intake output, or over many (see :func:`merge`). Rates are derived, never stored."""

    stated: int = 0
    exact_span: int = 0
    code: int = 0
    sentence: int = 0
    fragment: int = 0
    question: int = 0
    proposed: int = 0
    tag_none: int = 0
    tag_verified: int = 0
    tag_unverified: int = 0
    unresolved: int = 0
    unresolved_classified: int = 0
    unresolved_unclassified: int = 0

    def rates(self) -> dict[str, float]:
        """Shares over their own denominators; a share with nothing to divide is left out."""
        out: dict[str, float] = {}
        if self.stated:
            out["faithful_share"] = self.exact_span / self.stated
            out["code_share"] = self.code / self.stated
            out["well_formed_share"] = self.sentence / self.stated
            out["fragment_share"] = self.fragment / self.stated
            out["question_share"] = self.question / self.stated
        if self.proposed:
            out["tag_verified_share"] = self.tag_verified / self.proposed
            out["tag_unverified_share"] = self.tag_unverified / self.proposed
            out["tag_none_share"] = self.tag_none / self.proposed
        if self.unresolved:
            out["classified_share"] = self.unresolved_classified / self.unresolved
        return out


def score_intake(
    source: str,
    stated: Sequence[str],
    proposed: Sequence[str],
    unresolved: Mapping[str, Sequence[str]],
    reporter_defined: frozenset[str] | None = None,
) -> IntakeScore:
    """Score one intake output against the report it was read from.

    ``unresolved`` maps a stated criterion to the names in it that the repository graph does not hold
    (the binder's ``claims``); the scorer classifies them but does not resolve them itself.
    """
    defined = reporter_defined_names(source) if reporter_defined is None else reporter_defined
    forms = [criterion_form(c) for c in stated]
    tags = [tag_status(p, source) for p in proposed]
    names = [n for claims in unresolved.values() for n in claims]
    classified = sum(1 for n in names if classify_name(n, defined) != "unclassified")
    return IntakeScore(
        stated=len(stated),
        exact_span=sum(is_exact_span(c, source) for c in stated),
        code=forms.count("code"),
        sentence=forms.count("sentence"),
        fragment=forms.count("fragment"),
        question=forms.count("question"),
        proposed=len(proposed),
        tag_none=tags.count("none"),
        tag_verified=tags.count("verified"),
        tag_unverified=tags.count("unverified"),
        unresolved=len(names),
        unresolved_classified=classified,
        unresolved_unclassified=len(names) - classified,
    )


def merge(scores: Iterable[IntakeScore]) -> IntakeScore:
    """The sum of several scores, field by field."""
    total = {f.name: 0 for f in fields(IntakeScore)}
    for score in scores:
        for f in fields(IntakeScore):
            total[f.name] += getattr(score, f.name)
    return IntakeScore(**total)
