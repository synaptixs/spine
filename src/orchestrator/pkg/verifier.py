"""GroundingVerifier v0 — SHACL conformance + fact freshness (Track 1.4).

Two ways the knowledge graph can lie, two checks:

1. **Shape violations** (``shacl_findings``) — the facts break an invariant
   declared in SHACL (ontomesh emits these shapes from our exported
   projection; hand-written shapes work identically). The facts are
   materialised as RDF (``pkg.rdf``) and validated with ``pyshacl``.
2. **Stale facts** (``stale_findings``) — the graph asserts a symbol that the
   *current source* no longer contains: the file is re-extracted **by its own
   front-end** and every grounded fact for it is re-checked. A stale fact is the
   one sin the PKG must never commit silently.

   Until 2026-08-31 that re-extraction was ``PythonExtractor`` for *every* file,
   so a Go or TypeScript file parsed as Python, raised, and every fact in it was
   reported stale — on the pull-request review path, where the only repositories
   this runs against are other people's. Measured on an unmodified scratch repo:
   Python 0, TypeScript 2/2, Go 3/3. Invisible here, because ``src/`` is
   Python-only and both walkers skip ``corpus/``'s dot-prefixed fixture roots.

Both return ``GroundingFinding`` rows carrying ``file:line`` provenance, so
they anchor cleanly as review comments or audit entries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import Graph, Namespace

from orchestrator.pkg.extractor import LanguageExtractor, default_extractors
from orchestrator.pkg.facts import FactBatch, Node
from orchestrator.pkg.rdf import DEFAULT_NAMESPACE, facts_to_graph, symbol_iri

logger = logging.getLogger("orchestrator.pkg")


@dataclass(frozen=True)
class GroundingFinding:
    """One grounding failure, anchored to source where possible."""

    rule: str  # "shacl_violation" | "stale_fact" | "doc_drift"
    message: str
    file: str | None = None
    line: int | None = None
    symbol_id: str | None = None


class GroundingVerifier:
    """Validates a ``FactBatch`` against SHACL shapes and current source."""

    def __init__(
        self,
        batch: FactBatch,
        *,
        shapes_path: Path | str | None = None,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        self._batch = batch
        self._shapes_path = Path(shapes_path) if shapes_path else None
        self._namespace = namespace
        self._by_iri: dict[str, Node] = {str(symbol_iri(Namespace(namespace), n.id)): n for n in batch.nodes}
        self._by_suffix: dict[str, LanguageExtractor] | None = None
        #: Files whose freshness could not be checked because no front-end claims their
        #: suffix — on a base install that is *every* non-Python file, since the
        #: tree-sitter front-ends load only with their extra. Recorded rather than
        #: returned: a finding becomes a review WARNING, and "could not check this file"
        #: posted on every polyglot PR is noise worse than the bug it replaced. The fact
        #: stays recoverable here and in the debug log, and is never counted as clean.
        self.skipped_freshness: list[str] = []

    def _extractor_for(self, suffix: str) -> LanguageExtractor | None:
        """The front-end owning ``suffix``, or ``None`` if this install has none.

        Built the same way :class:`RepoCodeExtractor` builds it, from the same registry,
        so freshness re-extracts a file exactly as extraction produced it. Lazy because a
        verifier that only runs SHACL should not pay for importing eight grammars.
        """
        if self._by_suffix is None:
            self._by_suffix = {sfx: ex for ex in default_extractors() for sfx in ex.suffixes}
        return self._by_suffix.get(suffix)

    # ---- 1. SHACL conformance ---------------------------------------------

    def shacl_findings(self) -> list[GroundingFinding]:
        """Validate the materialised fact graph against the shapes file."""
        if self._shapes_path is None:
            return []
        shapes = Graph()
        shapes.parse(self._shapes_path, format="turtle")
        data = facts_to_graph(self._batch, namespace=self._namespace)
        conforms, report_graph, _ = shacl_validate(data, shacl_graph=shapes, inference="none")
        if conforms:
            return []
        return self._findings_from_report(report_graph)

    def _findings_from_report(self, report: Graph) -> list[GroundingFinding]:
        sh = Namespace("http://www.w3.org/ns/shacl#")
        findings: list[GroundingFinding] = []
        for result in report.subjects(predicate=sh["resultMessage"]):
            message = str(report.value(result, sh["resultMessage"]) or "SHACL violation")
            focus = report.value(result, sh["focusNode"])
            node = self._by_iri.get(str(focus)) if focus is not None else None
            prov = node.provenance if node is not None else None
            findings.append(
                GroundingFinding(
                    rule="shacl_violation",
                    message=message,
                    file=prov.file if prov else None,
                    line=prov.line if prov else None,
                    symbol_id=node.id if node else None,
                )
            )
        return findings

    # ---- 3. documentation drift --------------------------------------------

    def doc_findings(
        self,
        root: Path | str,
        *,
        limit: int = 20,
        files: list[str] | None = None,
        mentions: set[str] | None = None,
        exclude: set[tuple[str, str]] | None = None,
    ) -> list[GroundingFinding]:
        """Docs that claim a *symbol* the graph doesn't have — the third way the knowledge can
        lie: not the code being stale, but the *prose* about it. Deterministic, informational
        (a review comment, not a blocker): high-confidence symbol drift only (paths/URLs/filenames
        filtered out by :func:`doc_link.symbolish_drift`), anchored to the doc's ``file:line``.
        Empty when the repo has no docs.

        ``files`` and ``mentions`` narrow the result to drift attributable to some change: a
        finding is kept when its **document** is in ``files`` *or* its **mention** is in
        ``mentions`` (matched whole, or on the final dotted segment, so a doc saying
        ``mypkg.foo`` matches a change to ``foo``). Passing neither returns everything, which is
        what the repo-wide surfaces want.

        ``exclude`` is the **delta** mode and supersedes both: given the drift a *base* tree
        already had, keyed ``(page_title, mention)``, only what is new is reported. That is the
        exact form of the question the heuristic filters approximate — *what did this change
        break?* — so when a base is available they are not also applied. Drift a previous merge
        introduced is the previous author's, and drift whose cause the patch does not mention is
        still this change's if it was not there before.

        **The filters are applied before ``limit``**, and that ordering is the whole point of
        having them here rather than at the call site: capping first and filtering after would
        return nothing for a caller's own documents as soon as a repository carried 20 unrelated
        drift claims — the feature would go quiet on exactly the repositories that need it, and
        look like a clean result while doing so.
        """
        from orchestrator.pkg.doc_link import doc_drift, symbolish_drift

        wanted_files = set(files) if files is not None else None
        findings: list[GroundingFinding] = []
        for f in doc_drift(self._batch, root):
            if not symbolish_drift(f.mention):
                continue
            file = f.source_file or f.page_title.partition("#")[0] or None
            if exclude is not None:
                if (f.page_title, f.mention) in exclude:
                    continue
            elif wanted_files is not None or mentions is not None:
                by_file = file is not None and wanted_files is not None and file in wanted_files
                by_mention = mentions is not None and (
                    f.mention in mentions or f.mention.rsplit(".", 1)[-1] in mentions
                )
                if not (by_file or by_mention):
                    continue
            findings.append(
                GroundingFinding(
                    rule="doc_drift",
                    message=(
                        f"Documentation drift: `{f.page_title}` references `{f.mention}`, "
                        "but the knowledge graph has no such symbol (renamed, removed, or never "
                        "existed). Update the doc or the code."
                    ),
                    file=file,
                    line=f.line,
                )
            )
            if len(findings) >= limit:
                break
        return findings

    def drift_keys(self, root: Path | str) -> set[tuple[str, str]]:
        """Every symbol-shaped drift claim in ``root``, keyed ``(page_title, mention)``.

        The baseline half of the delta: run this on a base checkout, hand the result to the
        head's :meth:`doc_findings` as ``exclude``, and what survives is the drift this change
        introduced. Keyed on the claim rather than the finding's rendered message so the two
        sides compare even if the wording changes.
        """
        from orchestrator.pkg.doc_link import doc_drift, symbolish_drift

        return {(f.page_title, f.mention) for f in doc_drift(self._batch, root) if symbolish_drift(f.mention)}

    # ---- 2. freshness against current source -------------------------------

    def stale_findings(self, root: Path | str, files: list[str] | None = None) -> list[GroundingFinding]:
        """Facts asserting symbols the current source no longer defines.

        Re-extracts each (changed) file under ``root`` **with the front-end that owns its
        suffix** and reports every grounded fact whose symbol id is absent from the fresh
        extraction. Files that vanished entirely make all their facts stale.

        A file no front-end claims is **skipped**, not judged: it lands in
        :attr:`skipped_freshness` and is absent from the result. Silence is the only
        honest answer there — the alternative, and what this did until 2026-08-31, is to
        parse it as Python, fail, and report every fact in it as stale.

        A file whose *own* front-end cannot parse it is still treated as wholly stale.
        That is unchanged and deliberate: the source is genuinely unverifiable, and the
        aggressive direction is the safe one when the file itself is broken.
        """
        root_path = Path(root)
        by_file: dict[str, list[Node]] = {}
        for n in self._batch.nodes:
            if n.grounded and n.provenance is not None:
                by_file.setdefault(n.provenance.file, []).append(n)

        targets = files if files is not None else sorted(by_file)
        findings: list[GroundingFinding] = []
        for rel in targets:
            recorded = by_file.get(rel)
            if not recorded:
                continue
            path = root_path / rel
            extractor = self._extractor_for(path.suffix)
            if extractor is None and path.exists():
                # No front-end for this suffix in this install. Say nothing about the file
                # rather than something false about it.
                self.skipped_freshness.append(rel)
                logger.debug(
                    "freshness skipped: no front-end claims %s",
                    path.suffix or rel,
                    extra={"event": "pkg.freshness_skipped", "file": rel, "suffix": path.suffix},
                )
                continue
            fresh_ids: set[str] = set()
            if path.exists() and extractor is not None:
                try:
                    # `module_name` too, not just `extract`: each front-end owns its notion
                    # of a module — Go's is the package directory, not the file — and the
                    # Python-shaped `module_qualname` this used to hardcode would rename
                    # every symbol it re-derived, which is the same false-stale by another
                    # route.
                    fresh = extractor.extract(
                        path=path, module=extractor.module_name(path, root_path), rel=rel
                    )
                    fresh_ids = {n.id for n in fresh.nodes}
                except (SyntaxError, UnicodeDecodeError, ValueError):
                    pass  # unparseable now → everything recorded for it is stale
            for node in recorded:
                if node.id not in fresh_ids:
                    prov = node.provenance
                    findings.append(
                        GroundingFinding(
                            rule="stale_fact",
                            message=(
                                f"Knowledge graph is stale: `{node.id}` is recorded at "
                                f"{prov} but the current source no longer defines it. "
                                "Re-extract before trusting answers about this file."
                            ),
                            file=prov.file if prov else None,
                            line=prov.line if prov else None,
                            symbol_id=node.id,
                        )
                    )
        return findings


__all__ = ["GroundingFinding", "GroundingVerifier"]
