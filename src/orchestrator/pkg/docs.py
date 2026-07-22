"""Doc-semantic layer (Track 1.2) — reconcile documentation against code anchors.

Documentation makes *claims about code*: "``BacklogService`` calls
``create_issue``", "see ``orchestrator.pkg.facts``". This module extracts
those claims deterministically and binds each one to a PKG anchor:

- **bound** — the mentioned symbol/file exists in the fact graph (with
  ``file:line`` provenance), so the doc is grounded;
- **unbound** — the doc names something the code doesn't define: the classic
  *doc drift* signal ("the docs lie about the code"), reported as a finding.

Precision-first by construction. Only mentions with clear *code intent* can
produce drift findings: backticked spans, dotted paths, and snake_case
identifiers. Bare CamelCase words ("GitHub", "Python") bind when they resolve
but never count as drift — prose capitalisation is not a code claim.

Adapter-agnostic: callers pass ``DocPage`` rows (Confluence pages via Block B's
adapter, local Markdown, anything with a title + text). No LLM, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from orchestrator.pkg.facts import FactBatch

_BACKTICK_RE = re.compile(r"`([^`\n]{2,120})`")
_DOTTED_RE = re.compile(r"\b[a-z_]\w*(?:\.[A-Za-z_]\w*){2,}\b")  # a.b.C at least
# Last segments that mark a URL/domain rather than a code path.
_URL_TAILS = frozenset({"com", "net", "org", "io", "dev", "ai", "html", "www"})
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_IDENT_RE = re.compile(r"[A-Za-z_][\w.]*")

# snake_case words that are ordinary English compounds in our docs, not code.
_SNAKE_STOPLIST = frozenset({"e_g", "i_e"})


class MentionKind(str, Enum):
    BACKTICK = "backtick"
    DOTTED = "dotted"
    SNAKE = "snake"
    CAMEL = "camel"
    FILE = "file"


@dataclass(frozen=True)
class DocPage:
    """One document (or one section of one): a Confluence page, a Markdown file, a spec.

    ``base_dir`` (repo-relative) lets file mentions resolve relative to the
    document itself — docs link siblings ("archive/PLAN.md"), not repo roots.

    ``source_file`` / ``line`` locate the page in a real file when it came from one
    (section-granular ingestion sets them to the heading's file + line, so a section
    node's provenance points at the heading, not the file top). They default to
    empty/1 for non-file pages (Confluence, an in-memory spec).
    """

    title: str
    text: str
    url: str = ""
    base_dir: str = ""
    source_file: str = ""
    line: int = 1


@dataclass(frozen=True)
class DocMention:
    """A code-intent mention found in a document."""

    text: str
    kind: MentionKind
    page_title: str


@dataclass
class DocBinding:
    """A mention reconciled against the fact graph."""

    mention: DocMention
    anchor_ids: list[str] = field(default_factory=list)
    anchor_files: list[str] = field(default_factory=list)

    @property
    def bound(self) -> bool:
        return bool(self.anchor_ids or self.anchor_files)


@dataclass(frozen=True)
class DocDriftFinding:
    """A code-intent claim the code doesn't support."""

    page_title: str
    mention: str
    kind: MentionKind
    message: str


def extract_mentions(page: DocPage) -> list[DocMention]:
    """Code-intent mentions, de-duplicated, backticks taking precedence."""
    seen: dict[str, DocMention] = {}

    def _add(text: str, kind: MentionKind) -> None:
        key = text.strip()
        if key and key not in seen:
            seen[key] = DocMention(key, kind, page.title)

    for raw in _BACKTICK_RE.findall(page.text):
        candidate = raw.strip().strip("()")
        if "/" in candidate or candidate.endswith((".py", ".md", ".ttl", ".json", ".yaml", ".toml")):
            _add(candidate, MentionKind.FILE)
        elif _IDENT_RE.fullmatch(candidate):
            _add(candidate, MentionKind.BACKTICK)

    plain = _BACKTICK_RE.sub(" ", page.text)  # don't re-match inside backticks
    for match in _DOTTED_RE.findall(plain):
        if match.rsplit(".", 1)[-1].lower() not in _URL_TAILS:
            _add(match, MentionKind.DOTTED)
    for match in _SNAKE_RE.findall(plain):
        if match not in _SNAKE_STOPLIST:
            _add(match, MentionKind.SNAKE)
    for match in _CAMEL_RE.findall(plain):
        _add(match, MentionKind.CAMEL)
    return list(seen.values())


_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PLAIN_WORD_RE = re.compile(r"^[a-z][a-z0-9]*$")


class DocReconciler:
    """Binds document mentions to PKG anchors; unbound code-claims = drift.

    ``repo_root`` (optional) lets FILE mentions bind against the actual
    filesystem, not just extracted provenance paths — docs reference Markdown
    and config files the code graph never sees.
    """

    def __init__(self, batch: FactBatch, *, repo_root: Path | str | None = None) -> None:
        self._root = Path(repo_root) if repo_root else None
        self._names: dict[str, list[str]] = {}
        self._suffixes: dict[str, list[str]] = {}
        self._files: set[str] = set()
        for n in batch.nodes:
            if not n.grounded:
                continue
            self._names.setdefault(n.name.lower(), []).append(n.id)
            tail = n.id.split(":", 1)[-1]
            self._suffixes.setdefault(tail.lower(), []).append(n.id)
            if n.provenance is not None:
                self._files.add(n.provenance.file)

    def bind(self, mention: DocMention, *, base_dir: str = "") -> DocBinding:
        binding = DocBinding(mention=mention)
        text = mention.text.lower()
        if mention.kind is MentionKind.FILE:
            rel = mention.text.strip("/")
            binding.anchor_files = [f for f in self._files if f.endswith(mention.text) or mention.text in f]
            if not binding.anchor_files and self._root is not None:
                for candidate in (rel, f"{base_dir}/{rel}" if base_dir else rel):
                    if (self._root / candidate).exists():
                        binding.anchor_files = [candidate]
                        break
            return binding
        # dotted path → match the id tail; names → exact symbol-name match.
        binding.anchor_ids = list(self._suffixes.get(text, [])) or list(self._names.get(text, []))
        if not binding.anchor_ids and "." in text:
            # partial dotted tail: "pkg.persistence" ⊂ "orchestrator.pkg.persistence"
            binding.anchor_ids = [ids[0] for tail, ids in self._suffixes.items() if tail.endswith(f".{text}")]
        if not binding.anchor_ids and "." in text:
            leaf = text.rsplit(".", 1)[-1]
            binding.anchor_ids = list(self._names.get(leaf, []))
        return binding

    def _can_drift(self, mention: DocMention) -> bool:
        """Only claims with unambiguous code intent may drift.

        CamelCase prose, ALL-CAPS tokens (env vars/config keys), and single
        plain lowercase words (tool, branch, command names) bind when they
        resolve but never count against the docs.
        """
        if mention.kind is MentionKind.CAMEL:
            return False
        if _ALL_CAPS_RE.match(mention.text):
            return False
        if mention.kind is MentionKind.FILE:
            text = mention.text
            # Absolute, home, glob, or command-line paths point outside this
            # repo (or aren't paths at all) — they can't be claims about it.
            if text.startswith(("/", "~", "..")) or "*" in text or " " in text:
                return False
        return not (mention.kind is MentionKind.BACKTICK and _PLAIN_WORD_RE.match(mention.text))

    def reconcile(self, pages: list[DocPage]) -> tuple[list[DocBinding], list[DocDriftFinding]]:
        """All bindings plus drift findings for unbound code-intent claims."""
        bindings: list[DocBinding] = []
        drift: list[DocDriftFinding] = []
        for page in pages:
            for mention in extract_mentions(page):
                binding = self.bind(mention, base_dir=page.base_dir)
                bindings.append(binding)
                if binding.bound or not self._can_drift(mention):
                    continue
                drift.append(
                    DocDriftFinding(
                        page_title=page.title,
                        mention=mention.text,
                        kind=mention.kind,
                        message=(
                            f"Doc claim is unbound: '{page.title}' references "
                            f"`{mention.text}` but the code defines no such "
                            f"{'file' if mention.kind is MentionKind.FILE else 'symbol'}. "
                            "The docs and the code disagree — one of them is wrong."
                        ),
                    )
                )
        return bindings, drift


__all__ = [
    "DocBinding",
    "DocDriftFinding",
    "DocMention",
    "DocPage",
    "DocReconciler",
    "MentionKind",
    "extract_mentions",
]
