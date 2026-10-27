"""Read the committed memory bank — for codegen grounding and the MCP tool.

The memory bank is canonical project knowledge persisted in the repo. These
helpers let the codegen grounder prepend it (so generated code respects the
project's domain model) and let external agents pull it over MCP.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from orchestrator.knowledge.understand import existing_bank_dir

# Sections worth feeding into codegen grounding (domain model + terms). Conventions
# are already injected by the codegen convention digest; architecture is large/noisy.
_GROUNDING_SECTIONS = ("domain-model.md", "glossary.md")


def inside_root(path: Path, root: Path | str) -> bool:
    """Whether ``path`` resolves to somewhere under ``root``. A link committed in a repository
    must not take a read out of it: on a shared server the repository is content other people
    push, and ``glossary.md -> /proc/self/environ`` would otherwise be served back. The same
    rule the code and doc walkers apply (``pkg.extractor.symlink_admitted``)."""
    try:
        return path.resolve(strict=True).is_relative_to(Path(root).resolve())
    except OSError:
        return False


def bank_read_root(root: Path | str) -> Path:
    """What bank reads are confined to: the repository — or, when the operator set
    ``ORCHESTRATOR_MEMORY_BANK_DIR``, that directory, which is theirs to put anywhere."""
    configured = os.getenv("ORCHESTRATOR_MEMORY_BANK_DIR")
    return Path(configured) if configured else Path(root)


def _strip_header(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("<!-- generated"))


def memory_bank_grounding(root: Path | str, *, budget: int = 2500) -> str:
    """A compact grounding block from the committed knowledge base, or '' if absent."""
    mb = existing_bank_dir(root)
    parts: list[str] = []
    for name in _GROUNDING_SECTIONS:
        path = mb / name
        if path.is_file() and inside_root(path, bank_read_root(root)):
            try:
                cleaned = _strip_header(path.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
            if cleaned:
                parts.append(cleaned)
    body = "\n\n".join(parts).strip()
    if not body:
        return ""
    return f"PROJECT KNOWLEDGE (committed {mb.name}/, code-true):\n\n" + body[:budget]


def read_memory_bank(root: Path | str, section: str | None = None) -> dict[str, Any]:
    """Read the knowledge base: the index + section list, or one section's content."""
    mb = existing_bank_dir(root)
    if not mb.is_dir() or not inside_root(mb, bank_read_root(root)):  # a linked-in bank is no bank
        return {"exists": False, "dir": str(mb), "sections": []}
    sections = sorted(p.name for p in mb.glob("*.md"))
    if section:
        name = section if section.endswith(".md") else f"{section}.md"
        # `section` is untrusted (it arrives as an MCP tool argument). Without this
        # guard, "../../secrets.md" or an absolute path — or a symlink inside the bank
        # pointing outside it — reaches read_text() and discloses arbitrary files.
        # Resolve, then confine to the bank dir. Same idiom as evals/graders.py.
        path = (mb / name).resolve()
        contained = path.is_relative_to(mb.resolve())
        content = path.read_text(encoding="utf-8") if contained and path.is_file() else None
        return {"exists": True, "section": name, "content": content}
    readme = mb / "README.md"
    return {
        "exists": True,
        "dir": str(mb),
        "sections": sections,
        "index": readme.read_text(encoding="utf-8")
        if readme.is_file() and inside_root(readme, bank_read_root(root))
        else "",
    }


__all__ = ["bank_read_root", "inside_root", "memory_bank_grounding", "read_memory_bank"]
