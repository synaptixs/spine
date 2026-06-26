"""PKG-grounded codegen context — the seam between the codegen adapter and the PKG.

``LLMCodegenAdapter`` generates into a fresh worktree and knows nothing about
the codebase it is extending: it reinvents types that already exist and ignores
the conventions around them. ``PKGCodegenGrounder`` closes that gap. It scores
the spec's text against the repo's grounded symbols (``relevant_symbols``),
reads the *actual source* of the best matches off disk via their provenance
spans, and renders a context block the adapter prepends to its prompts — so
generated code imports the real APIs instead of guessing at them.

Deterministic and read-only: lexical retrieval + file reads, no LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore, GroundedRetriever, Node, RepoCodeExtractor, load_or_extract

_MAX_SNIPPET_LINES = 24  # per-symbol source excerpt
_MAX_CONTEXT_CHARS = 8_000  # total context budget


class PKGCodegenGrounder:
    """Builds an existing-codebase context block for a feature spec."""

    def __init__(self, retriever: GroundedRetriever, *, root: Path) -> None:
        self._retriever = retriever
        self._root = root

    @classmethod
    def from_repo(
        cls, root: Path | str, *, use_cache: bool = True, cache_dir: Path | None = None
    ) -> PKGCodegenGrounder:
        root_path = Path(root)
        batch = (
            load_or_extract(root_path, cache_dir=cache_dir)
            if use_cache
            else RepoCodeExtractor().extract(root_path)
        )
        return cls(GroundedRetriever(FactStore(batch)), root=root_path)

    def context_for_spec(self, spec: dict[str, Any]) -> str:
        """The codebase-context block, or '' when nothing in the repo is relevant.

        Leads with the committed memory bank's domain knowledge (if present), then
        the lexically-relevant PKG symbols. The memory bank shows even when no
        symbols match (e.g. a greenfield repo that's been `understand`-ed)."""
        from orchestrator.knowledge.access import memory_bank_grounding

        sections: list[str] = []
        mb = memory_bank_grounding(self._root)
        if mb:
            sections.append(mb)

        symbols = self._retriever.api_surface(_spec_query(spec))
        blocks: list[str] = []
        budget = _MAX_CONTEXT_CHARS
        for symbol in symbols:
            block = self._symbol_block(symbol)
            if len(block) > budget:
                break
            budget -= len(block)
            blocks.append(block)
        if blocks:
            sections.append(
                "EXISTING CODEBASE CONTEXT (from the Product Knowledge Graph — real "
                "code, with file:line provenance). Reuse these APIs instead of "
                "reinventing them; import them by the module paths shown; match "
                "their naming and style conventions:\n\n" + "\n".join(blocks)
            )
        return "\n\n".join(sections)

    def _symbol_block(self, symbol: Node) -> str:
        prov = symbol.provenance
        header = f"### {symbol.kind.value} `{symbol.id}`" + (f"  @ {prov}" if prov else "")
        snippet = self._read_span(symbol)
        return f"{header}\n```python\n{snippet}\n```\n" if snippet else f"{header}\n"

    def _read_span(self, symbol: Node) -> str:
        prov = symbol.provenance
        if prov is None:
            return ""
        try:
            lines = (self._root / prov.file).read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        start = prov.line - 1
        end = prov.end_line if prov.end_line is not None else prov.line
        excerpt = lines[start : min(end, start + _MAX_SNIPPET_LINES)]
        if end - start > _MAX_SNIPPET_LINES:
            excerpt.append("    # … truncated …")
        return "\n".join(excerpt)


def _spec_query(spec: dict[str, Any]) -> str:
    """Concatenate the spec's prose fields into one retrieval query."""
    parts = [str(spec.get(k) or "") for k in ("title", "summary", "user_story", "technical_notes")]
    criteria = spec.get("acceptance_criteria")
    if isinstance(criteria, list):
        parts.extend(str(c) for c in criteria)
    return " ".join(p for p in parts if p)


__all__ = ["PKGCodegenGrounder"]
