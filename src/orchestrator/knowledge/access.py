"""Read the committed memory bank — for codegen grounding and the MCP tool.

The memory bank is canonical project knowledge persisted in the repo. These
helpers let the codegen grounder prepend it (so generated code respects the
project's domain model) and let external agents pull it over MCP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.knowledge.understand import memory_bank_dir

# Sections worth feeding into codegen grounding (domain model + terms). Conventions
# are already injected by the codegen convention digest; architecture is large/noisy.
_GROUNDING_SECTIONS = ("domain-model.md", "glossary.md")


def _strip_header(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("<!-- generated"))


def memory_bank_grounding(root: Path | str, *, budget: int = 2500) -> str:
    """A compact grounding block from the committed memory bank, or '' if absent."""
    mb = memory_bank_dir(root)
    parts: list[str] = []
    for name in _GROUNDING_SECTIONS:
        path = mb / name
        if path.is_file():
            try:
                cleaned = _strip_header(path.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
            if cleaned:
                parts.append(cleaned)
    body = "\n\n".join(parts).strip()
    if not body:
        return ""
    return "PROJECT KNOWLEDGE (committed memory-bank/, code-true):\n\n" + body[:budget]


def read_memory_bank(root: Path | str, section: str | None = None) -> dict[str, Any]:
    """Read the memory bank: the index + section list, or one section's content."""
    mb = memory_bank_dir(root)
    if not mb.is_dir():
        return {"exists": False, "dir": str(mb), "sections": []}
    sections = sorted(p.name for p in mb.glob("*.md"))
    if section:
        name = section if section.endswith(".md") else f"{section}.md"
        path = mb / name
        content = path.read_text(encoding="utf-8") if path.is_file() else None
        return {"exists": True, "section": name, "content": content}
    readme = mb / "README.md"
    return {
        "exists": True,
        "dir": str(mb),
        "sections": sections,
        "index": readme.read_text(encoding="utf-8") if readme.is_file() else "",
    }


__all__ = ["memory_bank_grounding", "read_memory_bank"]
