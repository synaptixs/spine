"""What the spec writer is told about the repository a ticket is for (NSS-1243).

The spec writer used to see the ticket and nothing else. On NSS-1243 ("Display Oil Quantity as
Yes/No based on PSI data") it read *PSI* as pounds per square inch — "sensor readings", "a PSI
reading above the defined threshold" — in a codebase where PSI is an identifier (`PsiLocalId`,
`ReleasePSI`); and it put the threshold "in oil_status.js" in a C# Blazor repository. Every
acceptance criterion was its own invention, and the build that followed chased them.

This block gives it two facts it cannot get from the ticket: the languages the repository is
written in, and the real symbols the ticket's words match — the same lexical retrieval
``investigate`` runs, so the writer and the brief see one reading. Deterministic and model-free;
it is an aid to the prompt, not a judgement, and the validity gate still checks the result.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Source suffix → the language a person would call it. Only what names a language; markup,
#: config and data files say nothing about what the code is written in.
LANGUAGE_OF_SUFFIX: dict[str, str] = {
    ".py": "Python",
    ".cs": "C#",
    ".razor": "C#",
    ".cshtml": "C#",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".go": "Go",
    ".php": "PHP",
    ".pl": "Perl",
    ".pm": "Perl",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sql": "SQL",
}
#: Files walked when counting languages — enough to rank them, bounded on a monorepo.
_MAX_FILES = 20_000
_MAX_SYMBOLS = 12
_MAX_LANGUAGES = 4


def repository_languages(root: Path) -> list[tuple[str, int]]:
    """``[(language, files)]``, most files first, walked with the extractor's ignore rules."""
    from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS, is_nested_repo

    counts: Counter[str] = Counter()
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".") and not is_nested_repo(here, d)
        )
        for name in filenames:
            language = LANGUAGE_OF_SUFFIX.get(Path(name).suffix.lower())
            if language:
                counts[language] += 1
        seen += len(filenames)
        if seen >= _MAX_FILES:
            break
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def repo_context(root: Path | str) -> Callable[[str], str]:
    """A ``SpecWriter.context_for`` for the checkout at ``root``. The graph is loaded on first use."""
    root_path = Path(root)
    state: dict[str, Any] = {}

    def context_for(text: str) -> str:
        if "store" not in state:
            from orchestrator.pkg import FactStore, load_or_extract

            state["store"] = FactStore(load_or_extract(root_path))
            state["languages"] = repository_languages(root_path)
        from orchestrator.sdlc.investigate import build_investigation

        title, _, rest = text.partition("\n")
        landing = list(build_investigation(title, rest, store=state["store"], root=root_path).landing)
        # Strong matches first; a weak one (a single shared word) is still a real name in the
        # code, which is what the writer needs, but it is marked so it is not read as a landing.
        landing.sort(key=lambda land: bool(getattr(land, "weak", False)))
        lines = ["REPOSITORY CONTEXT (read from the checkout, not the ticket):"]
        languages = state["languages"][:_MAX_LANGUAGES]
        if languages:
            lines.append("- Written in: " + ", ".join(f"{lang} ({n} files)" for lang, n in languages))
        if landing:
            lines.append("- Symbols in this repository that match the intent's words — prefer these names:")
            for land in landing[:_MAX_SYMBOLS]:
                weak = " (matches one common word only)" if getattr(land, "weak", False) else ""
                lines.append(f"  - `{land.name}` ({land.kind}) — {land.where}{weak}")
        return "\n".join(lines) if len(lines) > 1 else ""

    return context_for


def attach_repo_context(service: object, root: Path | str) -> None:
    """Hand ``service``'s spec writer the context for ``root``, when the service takes one.

    An optional aid, attached by duck type: callers (and their test doubles) pass any object
    with ``analyze``, and one without ``set_spec_context`` simply writes specs as before.
    """
    setter = getattr(service, "set_spec_context", None)
    if callable(setter):
        setter(repo_context(root))


__all__ = ["LANGUAGE_OF_SUFFIX", "attach_repo_context", "repo_context", "repository_languages"]
