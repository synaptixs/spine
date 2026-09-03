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
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from orchestrator.pkg.facts import FactBatch, Node, NodeKind

_BACKTICK_RE = re.compile(r"`([^`\n]{2,120})`")
_DOTTED_RE = re.compile(r"\b[a-z_]\w*(?:\.[A-Za-z_]\w*){2,}\b")  # a.b.C at least
# Tails that make a dotted token something other than a symbol path. Domains were the original
# reason; file extensions were the omission. `compose.dev.yml`, `docs.astral.sh` and
# `orchestrator.pkg.persistence.md` are dotted, lowercase and multi-segment, so they read as
# symbol paths, match nothing, and land in the drift list as claims the code failed to support.
# They are filenames. This list drops 12 of them at extraction; `_can_drift` drops a further 43
# that arrive backticked. Measured 2026-09-01: **55 fewer drift findings, 1,651 -> 1,596, and
# zero change to MENTIONS edges** — this is drift precision, not binding coverage.
#
# It was investigated as a *binding* defect and is not one. See `test_docs.py` for what the
# leaf-only fallback in `bind` actually does: on this repository it rescues 76 mentions like
# `store.find` onto `FactStore.find` and mis-binds no filename at all.
_URL_TAILS = frozenset(
    {
        # domains
        "com",
        "net",
        "org",
        "io",
        "dev",
        "ai",
        "www",
        # file extensions — a dotted token ending in one names a FILE, never a symbol
        "html",
        "json",
        "yaml",
        "yml",
        "toml",
        "md",
        "rst",
        "txt",
        "cfg",
        "ini",
        "lock",
        "png",
        "svg",
        "jpg",
        "jpeg",
        "gif",
        "pdf",
        "csv",
        "tsv",
        "sh",
        "bat",
        "sql",
        # Source extensions. Measured zero edges either way on this repository; listed
        # because the rule is identical — `handler.py` names a file. A genuine path
        # mention takes the FILE branch and resolves against real paths.
        "py",
        "ts",
        "tsx",
        "js",
        "jsx",
        "go",
        "java",
        "cs",
        "c",
        "h",
        "cc",
        "cpp",
        "rs",
    }
)
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
    #: The document this claim is in, and the line its section starts at — the same pair
    #: `link_docs` uses for `Doc` provenance. Carried so a consumer can anchor the finding
    #: instead of partitioning `page_title` and guessing line 1: a drift comment at the top
    #: of a long README does not tell a reviewer where to look. Optional because a page can
    #: be synthesised without a source file (media, in-memory fixtures).
    source_file: str | None = None
    line: int = 1


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
        self._init(batch.nodes, repo_root=repo_root)

    @classmethod
    def from_nodes(cls, nodes: Iterable[Node], *, repo_root: Path | str | None = None) -> DocReconciler:
        """Build from nodes rather than a batch — for callers holding a ``FactStore``.

        The reconciler only ever reads ``batch.nodes``. A caller with a store would otherwise
        have to rebuild a batch just to be let in, and a second copy of the same nodes is a
        second thing that can drift from the first.
        """
        obj = cls.__new__(cls)
        obj._init(nodes, repo_root=repo_root)
        return obj

    def _init(self, nodes: Iterable[Node], *, repo_root: Path | str | None) -> None:
        self._root = Path(repo_root) if repo_root else None
        self._names: dict[str, list[str]] = {}
        self._suffixes: dict[str, list[str]] = {}
        self._files: set[str] = set()
        #: repo-relative path -> the Module nodes owning it. A document citing a path names
        #: that module, and the identity is the extractor's own provenance — not a guess. Only
        #: emitted when exactly one module owns the path: a C/C++ header can back several, and
        #: an ambiguous anchor is the fabrication this tier refuses. See
        #: `docs/specs/doc-file-binding.md`.
        self._modules_for_file: dict[str, list[str]] = {}
        #: lowercase file stem -> repo-relative paths, built lazily on first use. Prose cites a
        #: file by its stem — `comprehension_labels` for `comprehension_labels.yaml` — and the
        #: extractor's provenance only covers files it parsed, so this walks the tree instead.
        self._stems: dict[str, list[str]] | None = None
        for n in nodes:
            if not n.grounded:
                continue
            self._names.setdefault(n.name.lower(), []).append(n.id)
            tail = n.id.split(":", 1)[-1]
            self._suffixes.setdefault(tail.lower(), []).append(n.id)
            if n.provenance is not None:
                self._files.add(n.provenance.file)
                if n.kind is NodeKind.MODULE:
                    self._modules_for_file.setdefault(n.provenance.file, []).append(n.id)

    def _stem_index(self) -> dict[str, list[str]]:
        """Every file in the repository, keyed by its lowercase stem.

        Sorted at both levels: this feeds an "exactly one" test, and an unsorted walk would
        make *which* file a stem resolves to depend on filesystem order. The same directory
        exclusions as `doc_source` — a dot-directory is where the fixture corpora live, and
        binding prose to a fixture would be worse than not binding it.
        """
        if self._stems is None:
            index: dict[str, list[str]] = {}
            if self._root is not None:
                for path in sorted(self._root.rglob("*")):
                    rel = path.relative_to(self._root)
                    if any(part.startswith(".") or part == "node_modules" for part in rel.parts):
                        continue
                    if path.is_file():
                        index.setdefault(path.stem.lower(), []).append(str(rel))
            self._stems = {k: sorted(v) for k, v in index.items()}
        return self._stems

    def bind(self, mention: DocMention, *, base_dir: str = "") -> DocBinding:
        binding = DocBinding(mention=mention)
        text = mention.text.lower()
        if mention.kind is MentionKind.FILE:
            rel = mention.text.strip("/")
            # **Sorted**: `_files` is a set, so an unsorted comprehension yields hash order and
            # a caller taking `[0]` gets a different answer per process. Nothing read the order
            # until file mentions began naming a module, which is exactly how a latent
            # non-determinism becomes a live one.
            binding.anchor_files = sorted(
                f for f in self._files if f.endswith(mention.text) or mention.text in f
            )
            if not binding.anchor_files and self._root is not None:
                for candidate in (rel, f"{base_dir}/{rel}" if base_dir else rel):
                    if (self._root / candidate).exists():
                        binding.anchor_files = [candidate]
                        break
            # A cited path names the module the extractor built from it. Exactly one, or
            # nothing — see `docs/specs/doc-file-binding.md`.
            # One file, owned by one module, or nothing. A mention matching several paths is
            # ambiguous about which file it means before it is ambiguous about which module.
            if len(binding.anchor_files) == 1:
                owners = self._modules_for_file.get(binding.anchor_files[0], [])
                if len(owners) == 1:
                    binding.anchor_ids = list(owners)
            return binding
        # dotted path → match the id tail; names → exact symbol-name match.
        binding.anchor_ids = list(self._suffixes.get(text, [])) or list(self._names.get(text, []))
        if not binding.anchor_ids and "." in text:
            # partial dotted tail: "pkg.persistence" ⊂ "orchestrator.pkg.persistence"
            binding.anchor_ids = [ids[0] for tail, ids in self._suffixes.items() if tail.endswith(f".{text}")]
        if not binding.anchor_ids and "." in text:
            # Last resort: the leaf alone, which throws the qualifier away — this is what
            # binds `store.find` onto `FactStore.find`, 76 times here. The guard is repeated
            # rather than left to `extract_mentions` because a BACKTICK mention never passes
            # through the dotted-token filter, so `` `a.b.json` `` arrives intact. Nothing in
            # this repository currently reaches it; the asymmetry is the trap, not a live bug.
            leaf = text.rsplit(".", 1)[-1]
            if leaf.lower() not in _URL_TAILS:
                binding.anchor_ids = list(self._names.get(leaf, []))
        # Last resort: prose naming a file by its stem — `typescript_extractor` for
        # `typescript_extractor.py`, `comprehension_labels` for the YAML beside it.
        #
        # **Restricted to snake-shaped tokens, and that restriction is the whole rule.** Matching
        # any stem binds `bug`, `enhancement`, `invention` and `validity` to modules that happen
        # to share the name, when the prose meant the English words — measured at 149 bindings
        # against 88, with the extra 61 being exactly that mistake. An underscore is what makes a
        # token code-shaped rather than a word, which is why `_SNAKE_RE` exists at all.
        if not binding.anchor_ids and not binding.anchor_files and "_" in text:
            paths = self._stem_index().get(text, [])
            if len(paths) == 1:
                binding.anchor_files = list(paths)
                owners = self._modules_for_file.get(paths[0], [])
                if len(owners) == 1:
                    binding.anchor_ids = list(owners)
        return binding

    def _can_drift(self, mention: DocMention) -> bool:
        """Only claims with unambiguous code intent may drift.

        CamelCase prose, ALL-CAPS tokens (env vars/config keys), and single
        plain lowercase words (tool, branch, command names) bind when they
        resolve but never count against the docs.

        So do **filenames the FILE pattern does not recognise**. `md.js`, `graph.html`
        and `compose.dev.yml` are dotted, lowercase and multi-segment — indistinguishable
        from a symbol path by shape — and arrived as BACKTICK claims, so the drift list
        reported 55 of them as prose naming code that does not exist. A *recognised*
        extension (`README.md`, `src/a/b.py`) is a FILE mention and keeps its disk check:
        naming a file that is not there is a real finding, and must still drift.
        """
        if mention.kind is MentionKind.CAMEL:
            return False
        if (
            mention.kind is not MentionKind.FILE
            and "." in mention.text
            and mention.text.rsplit(".", 1)[-1].lower() in _URL_TAILS
        ):
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
                        source_file=page.source_file or None,
                        line=page.line,
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
