"""Product Knowledge Graph (PKG) — Layer 1: the grounded code extractor.

Turns any repository into universal, provenance-carrying facts (``facts``),
via per-language front-ends (``extractor``), queryable for grounded retrieval
(``store``), cacheable per commit (``persistence``). Language-agnostic schema,
pluggable parsing — Python first.
"""

from __future__ import annotations

from orchestrator.pkg.doc_link import doc_drift, link_docs
from orchestrator.pkg.docs import (
    DocBinding,
    DocDriftFinding,
    DocMention,
    DocPage,
    DocReconciler,
)
from orchestrator.pkg.export import export_sqlite
from orchestrator.pkg.extractor import (
    LanguageExtractor,
    PythonExtractor,
    RepoCodeExtractor,
    module_qualname,
)
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.java_extractor import JavaExtractor
from orchestrator.pkg.persistence import (
    FactCacheError,
    load_facts,
    load_or_extract,
    repo_state,
    save_facts,
)
from orchestrator.pkg.rdf import facts_to_graph
from orchestrator.pkg.retrieval import GroundedRetriever, SymbolImpact
from orchestrator.pkg.store import CallSite, FactStore
from orchestrator.pkg.verifier import GroundingFinding, GroundingVerifier

__all__ = [
    "CallSite",
    "DocBinding",
    "doc_drift",
    "link_docs",
    "DocDriftFinding",
    "DocMention",
    "DocPage",
    "DocReconciler",
    "Edge",
    "EdgeKind",
    "FactBatch",
    "FactCacheError",
    "FactStore",
    "GroundedRetriever",
    "GroundingFinding",
    "GroundingVerifier",
    "LanguageExtractor",
    "Node",
    "NodeKind",
    "Provenance",
    "JavaExtractor",
    "PythonExtractor",
    "RepoCodeExtractor",
    "SymbolImpact",
    "export_sqlite",
    "facts_to_graph",
    "load_facts",
    "load_or_extract",
    "module_qualname",
    "repo_state",
    "save_facts",
]
