"""GroundingVerifier v0 — SHACL conformance + fact freshness (Track 1.4).

Two ways the knowledge graph can lie, two checks:

1. **Shape violations** (``shacl_findings``) — the facts break an invariant
   declared in SHACL (ontomesh emits these shapes from our exported
   projection; hand-written shapes work identically). The facts are
   materialised as RDF (``pkg.rdf``) and validated with ``pyshacl``.
2. **Stale facts** (``stale_findings``) — the graph asserts a symbol that the
   *current source* no longer contains: the file is re-extracted and every
   grounded fact for it is re-checked. A stale fact is the one sin the PKG
   must never commit silently.

Both return ``GroundingFinding`` rows carrying ``file:line`` provenance, so
they anchor cleanly as review comments or audit entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import Graph, Namespace

from orchestrator.pkg.extractor import PythonExtractor, module_qualname
from orchestrator.pkg.facts import FactBatch, Node
from orchestrator.pkg.rdf import DEFAULT_NAMESPACE, facts_to_graph, symbol_iri


@dataclass(frozen=True)
class GroundingFinding:
    """One grounding failure, anchored to source where possible."""

    rule: str  # "shacl_violation" | "stale_fact"
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

    # ---- 2. freshness against current source -------------------------------

    def stale_findings(self, root: Path | str, files: list[str] | None = None) -> list[GroundingFinding]:
        """Facts asserting symbols the current source no longer defines.

        Re-extracts each (changed) file under ``root`` and reports every
        grounded fact whose symbol id is absent from the fresh extraction.
        Files that vanished entirely make all their facts stale.
        """
        root_path = Path(root)
        by_file: dict[str, list[Node]] = {}
        for n in self._batch.nodes:
            if n.grounded and n.provenance is not None:
                by_file.setdefault(n.provenance.file, []).append(n)

        targets = files if files is not None else sorted(by_file)
        extractor = PythonExtractor()
        findings: list[GroundingFinding] = []
        for rel in targets:
            recorded = by_file.get(rel)
            if not recorded:
                continue
            path = root_path / rel
            fresh_ids: set[str] = set()
            if path.exists():
                try:
                    fresh = extractor.extract(path=path, module=module_qualname(path, root_path), rel=rel)
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
