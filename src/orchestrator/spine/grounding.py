"""The Grounding Block contract — cited domain knowledge for a build (Phase 1).

Seam 1 (``docs/specs/tri-repo-integration.md``) feeds domain semantics into
codegen. A ``GroundingBlock`` is the open, render-able payload that carries it:
the prose plus the **citations** (ontology IRIs) that justify every claim, so
generated code can be traced back to the domain fact behind it. Same prompt slot
the code-true ``memory_bank_grounding`` fills — this is the domain-true companion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_BUDGET = 2000


@dataclass(frozen=True)
class Citation:
    """A traceable source backing a claim — an ontology IRI (+ optional label).

    Mirrors ontomesh's citation contract so a `ReasonedAnswer` maps over directly.
    ``inferred`` marks facts derived by the reasoner rather than read from data.
    """

    iri: str
    label: str = ""
    source_table: str = ""
    inferred: bool = False

    def render(self) -> str:
        tag = " (inferred)" if self.inferred else ""
        return f"{self.label or self.iri} <{self.iri}>{tag}"


@dataclass(frozen=True)
class GroundingBlock:
    """Cited domain knowledge ready to prepend to a build prompt."""

    text: str
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    source: str = "ontomesh"

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def render(self, *, budget: int = _DEFAULT_BUDGET) -> str:
        """The prompt-ready block, or '' when empty. Citations always included so
        the grounding stays auditable even when the prose is truncated."""
        body = self.text.strip()
        if not body:
            return ""
        body = body[:budget]
        lines = [
            f"DOMAIN KNOWLEDGE ({self.source}, cited; verify against the code):",
            "",
            body,
        ]
        if self.citations:
            lines.append("")
            lines.append("Sources:")
            lines.extend(f"  - {c.render()}" for c in self.citations)
        return "\n".join(lines)


__all__ = ["Citation", "GroundingBlock"]
