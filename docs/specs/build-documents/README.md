# Build documents

The reviewable artifact a ticket produces *before* any code — see
[`../build-document.md`](../build-document.md) for why it exists and what each
section is made of.

**`SSPN-49-build.md` is the template.** It was assembled by hand, and its twelve
numbered sections are the fixed shape every future build document takes, in the
same order. A renderer can only be built against a stable shape, and a reviewer
should read the same page every time — so a section is added or reordered for
*all* tickets or not at all.

| File | What it is |
|---|---|
| `SSPN-49-build.md` | The canonical build document — the template |
| `SSPN-49-plan.md` | The earlier pre-code plan, kept because it records the reconciliation between the filed criteria and the code |
| `SSPN-49.json` | The `FeatureSpec` it was built from — the input shape |
| `PKG-ACC-1-build.md` | Phase 1 of the PKG accuracy roadmap — a labelled corpus and the two numbers (precision, recall). **Shipped** |
| `PKG-ACC-2-build.md` | Phase 2 — the runtime oracle: `CALLS` recall from a repo's own test suite, no labelling. **Shipped** |
| `PKG-ACC-3-build.md` | Phase 3 — per-construct parity: declared routes/tables against the graph, per file. **Shipped** |
| `PKG-ACC-4-build.md` | Phase 4 — invention: CALLS edges to names bound in the caller's own scope, plus a sampler. **Shipped** |

These are tracked on purpose. `understand` ingests markdown from disk whether or
not git tracks it, so a build document that exists locally but not in the repo
makes `episteme/` describe `Doc` nodes CI cannot see.
