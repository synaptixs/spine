# PKG Path Query

## Status

Draft implementation for maintainer review. The command is read-only and deterministic; it does not infer runtime relationships or call an LLM.

## Contract

```text
orchestrator pkg path SOURCE TARGET [--path REPO] [OPTIONS]
```

The command resolves exact node IDs first. A short name is accepted only when it matches one node uniquely; ambiguous names fail and list exact IDs. Traversal is bounded by `--max-hops` and uses stable neighbour ordering so equal-length results do not depend on parser or dictionary insertion order.

The result contains source and target metadata, selected edge kinds, distance, every hop, provenance, and a static-analysis caveat. JSON marks reverse traversal with `reversed: true` without rewriting the underlying fact.

## Edge policy

The default set contains semantic code, API, data, and type-hierarchy relations: `CALLS`, `EXPOSES`, `CONSUMES`, `READS`, `WRITES`, `REFERENCES`, and `IMPLEMENTS`. `MENTIONS` is explicit-only because document hubs can create unrelated two-hop paths. `CONTAINS` and `IMPORTS` are structural and require `--include-structural` when `--kind` is not supplied. `SERVES` is rejected in v1 because Intent nodes do not carry source-file provenance.

When `--kind` is supplied, it is the complete allow-list. `--include-structural` therefore emits a warning and is ignored rather than silently changing the query.

## Direction and rendering

`forward` follows `edge.src → edge.dst`; `reverse` follows `edge.dst → edge.src`; `both` permits both. The underlying `Edge` is never rewritten. Human-readable output always renders `edge.src --KIND--> edge.dst`; a reverse traversal adds `(traversed reverse)`. JSON exposes the same distinction through `reversed`.

This prevents a serious epistemic error: if the extracted fact is `caller --CALLS--> helper`, a reverse query from `helper` to `caller` must not print `helper --CALLS--> caller` as if that opposite fact had been extracted.

## Cache and refresh

The command uses the shared `load_or_extract` path. On a clean Git repository it reuses the commit-keyed fact cache; dirty trees, non-Git directories, stale entries, and corrupt entries fall back to extraction under the existing cache contract. `--refresh` explicitly bypasses reuse. Document linking is applied deterministically in memory so document-to-code paths are available without changing the persisted code-fact cache.

## Negative results and non-goals

“No extracted path” means no path was found within the selected edge set and hop bound. It does not mean that no runtime relationship exists. The feature does not infer runtime reachability, replace graph export/visualization, add an LLM, or claim that a missing static path proves two components are unrelated.

## Acceptance criteria

1. Exact IDs and unique names resolve; ambiguous names fail without guessing.
2. Forward, reverse, and both traversal return stable shortest paths and terminate on cycles.
3. Reverse human output preserves the extracted arrow and adds a traversal note.
4. JSON and human output agree on nodes, edge kinds, provenance, direction, and caveat.
5. `MENTIONS` is explicit-only by default; `IMPLEMENTS` is included by default; `SERVES` is rejected.
6. Structural edges are opt-in and the ignored-flag case emits a warning.
7. Dangling edges are skipped defensively rather than materializing nodes.
8. Cache reuse and `--refresh` follow sibling PKG commands.
9. Unit, CLI, documentation, lint, type, graph-verification, accuracy, and security checks pass, with inherited base failures reported separately.
