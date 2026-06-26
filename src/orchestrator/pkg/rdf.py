"""Facts → RDF data graph, in the vocabulary the ontomesh round-trip infers.

The exporter's kind-per-table projection makes ontomesh emit ``:Module``/
``:Type``/``:Function`` OWL classes and SHACL shapes over them — but no
per-symbol individuals (the verified A-box gap). This module fills that gap on
our side: it materialises each grounded fact as an individual in the *same
namespace*, so the ontomesh-generated shapes can validate our actual graph.

Triples per node:   <sym> a :Function ; :name "…" ; :file "…" ; :line N .
Triples per edge:   <caller> :calls <callee> .   <module> :imports <module> .
"""

from __future__ import annotations

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

# Ontomesh's toolkit emits its enterprise namespace by default; using the same
# one means its generated shapes target our individuals with zero rewriting.
DEFAULT_NAMESPACE = "https://ontology.example.com/enterprise/"

_CLASS_BY_KIND = {
    NodeKind.MODULE: "Module",
    NodeKind.TYPE: "Type",
    NodeKind.FUNCTION: "Function",
    NodeKind.FIELD: "Field",
    NodeKind.ENDPOINT: "Endpoint",
    NodeKind.ENTITY: "Entity",
}

_PROPERTY_BY_EDGE = {
    EdgeKind.CALLS: "calls",
    EdgeKind.IMPORTS: "imports",
    EdgeKind.CONTAINS: "contains",
    EdgeKind.IMPLEMENTS: "implements",
    EdgeKind.READS: "reads",
    EdgeKind.WRITES: "writes",
    EdgeKind.EXPOSES: "exposes",
    EdgeKind.REFERENCES: "references",
}


def symbol_iri(ns: Namespace, node_id: str) -> URIRef:
    """A stable IRI for a fact node (``py:a.b.C`` → ``<ns>sym/py%3Aa.b.C``)."""
    from urllib.parse import quote

    return URIRef(f"{ns}sym/{quote(node_id, safe='')}")


def facts_to_graph(batch: FactBatch, *, namespace: str = DEFAULT_NAMESPACE) -> Graph:
    """Materialise the batch as an RDF data graph (grounded nodes as individuals)."""
    ns = Namespace(namespace)
    g = Graph()
    g.bind("", ns)

    # Datatype-property names follow ontomesh's column→property convention
    # (lowerCamel "has*"), so its generated shapes validate our individuals.
    for n in batch.nodes:
        iri = symbol_iri(ns, n.id)
        g.add((iri, RDF.type, ns[_CLASS_BY_KIND[n.kind]]))
        g.add((iri, ns["hasName"], Literal(n.name)))
        g.add((iri, ns["hasExternal"], Literal(int(n.external), datatype=XSD.integer)))
        # Governance default, mirroring the exporter's ontology_metadata tier.
        g.add((iri, ns["sensitivityTier"], ns["Internal"]))
        if n.language:
            g.add((iri, ns["hasLanguage"], Literal(n.language)))
        if n.provenance is not None:
            g.add((iri, ns["hasFile"], Literal(n.provenance.file)))
            g.add((iri, ns["hasLine"], Literal(n.provenance.line, datatype=XSD.integer)))
            if n.provenance.end_line is not None:
                g.add((iri, ns["hasEndLine"], Literal(n.provenance.end_line, datatype=XSD.integer)))

    for e in batch.edges:
        g.add((symbol_iri(ns, e.src), ns[_PROPERTY_BY_EDGE[e.kind]], symbol_iri(ns, e.dst)))

    return g


__all__ = ["DEFAULT_NAMESPACE", "facts_to_graph", "symbol_iri"]
