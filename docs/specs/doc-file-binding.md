# Binding a document to the module it cites

**Status:** **Scoped 2026-09-01 against 3.27.0.** Not built. Approved to build as Option A
(§4).
**Owner:** _unassigned_

`docs/specs/doc-binding-walkthrough.md` closes on a number: **55% of `Doc` sections bind to
nothing at all.** This spec is the first deterministic move against it, and — measured — very
nearly the only one.

---

## 1. The fact that is thrown away

A section citing `src/orchestrator/pkg/store.py` resolves that path against the repository,
finds it, and then draws **no edge**. `DocReconciler.bind` records the hit in `anchor_files`;
`link_docs` emits an edge only for `anchor_ids`. The path is matched and then discarded.

That path maps to exactly one `Module` node — `py:orchestrator.pkg.store`. **This is not a
guess and not a heuristic**: it is the same provenance the extractor already wrote. The
document names the module; the graph declines to say so.

The walkthrough currently defends this:

> The file-only mentions are a third category and mostly correct behaviour: prose citing
> `src/orchestrator/pkg/store.py` is naming a path, not a symbol, and there is no symbol edge
> to draw.

**That reasoning is wrong.** A `Module` *is* a node. The premise — that a path names nothing in
the graph — is false for every path the extractor produced a module from.

## 2. Measured, on this repository at 3.27.0

> Measured on `develop` **before this document existed**. Writing a spec about the binding
> figures changes the binding figures — this page is itself ingested, and its own backticked
> symbols become mentions. The live values are re-derived by `scripts/state-numbers.py` and
> carried in the walkthrough; these are the ones the decision in §4 was actually made on.

| File-only mentions | | |
|---|---|---|
| map to **exactly one** `Module` | **938** | become edges |
| map to **more than one** | **0** | would be skipped, same rule as always |
| map to **no** `Module` | 440 | assets, configs, `.md` — correctly stay unbound |
| **total** | **1,378** | |

938 mentions collapse to **~926 distinct `(section, module)` edges** after per-section
de-duplication.

### What it does to the gap

| | before | after |
|---|---|---|
| `Doc` sections binding to nothing | **874** | **766** |
| as a share of all 1,583 sections | 55% | **48%** |
| `MENTIONS` edges | 2,453 | ~3,379 (**+38%**) |

**The 55% is itself overstated, and this spec is the reason to say so.** Of the 874 unbound
sections, **367 contain no symbol-shaped mention at all** — a heading, a table of links, a
paragraph of prose. There is nothing to bind and no failure to fix. The real failure population
is **507 sections**, and 108 of them are reached here.

## 3. What is *not* reachable, which is the more important half

The walkthrough's "what would count as progress" recommends resolving the 1,966 ambiguous
mentions first, calling them *"the tractable half, and the half a deterministic rule might still
reach"*. **Measured 2026-09-01, that is false**, and it is corrected as part of this work:

| Of 507 failing sections | reached by |
|---|---|
| 108 | this spec — file → `Module` |
| **5** | resolving ambiguity, taking every mention whose anchors share one owning module |
| **0** | using this spec's module bindings as context to disambiguate the rest |
| **394** | nothing deterministic |

Only **48 of 1,966** ambiguous mentions (2%) have all their candidate anchors in a single owning
module. The compounding idea — bind the file first, then use that module as context to pick
among a mention's candidates — sounded right and rescues **nothing**.

**So after this ships the gap is 48%, and no further deterministic rule is known to move it.**
That is the honest end state, and it should be written down before anyone proposes a model.

## 4. Decision

| | Option | |
|---|---|---|
| **A** | **Bind every file-only mention whose path owns exactly one module** | **Chosen.** +926 edges |
| B | Bind only in sections that would otherwise have no edge | +~150 edges, same 55%→48% |
| C | Do not bind; redefine the metric | rejected — that is moving the goalposts |

**A, because the fact does not depend on what else the section says.** Under B, whether the
graph records "this section mentions `orchestrator.pkg.store`" would depend on whether some
*other* sentence in the same section happened to name a symbol. Two documents making the same
claim would be recorded differently. That is not a property a source of truth should have.

**The cost is real and is accepted.** `PKGCodegenGrounder._doc_block` attaches a linked
section's prose to a symbol's source in the codegen context. A section listing thirty file paths
in a table now attaches to thirty modules, and a 38% rise in `MENTIONS` edges lands on every
consumer that treats one as a signal — including `insights.py`, where a `MENTIONS` edge counts
as a *use* and therefore suppresses dead-code findings. **Measure that after, do not assume it.**

## 5. Invariants

- **Exactly one, or nothing.** A path owning two or more modules — C/C++ headers are the case
  that exists — draws no edge. Same *skip rather than guess* rule that holds `CALLS` precision
  at 1.00.
- **No new node or edge kind.** `Doc --MENTIONS--> Module` is the existing shape; only the
  anchor changes.
- **Deterministic and model-free.** The mapping is `provenance.file`, already in the graph.
- **The extractor is not consulted twice.** Resolution reads the batch `DocReconciler` was built
  from, so binding still cannot be influenced by the documents being bound.

## 6. Non-goals

- **Binding a path to a symbol *inside* the file.** The document named the file; the module is
  the honest granularity.
- **Reaching the 394.** They need meaning rather than string equality, which needs a model,
  which collides with the determinism that makes `understand --check` a gate.
- **Raising the number by relaxing the one-anchor rule** anywhere else.

## 7. Open questions

1. **Does the 38% edge growth degrade codegen grounding?** `_doc_block` is the consumer most
   exposed. Unmeasured, and the reason §4 says measure after.
2. **How many of the 394 cannot bind by construction?** The walkthrough estimates "about a
   tenth" of drift; nothing has counted it for this population. That number should exist before
   a second tier is designed, not after.
3. **Should a file-derived `MENTIONS` edge be distinguishable from a symbol-derived one?** A
   consumer wanting the higher-precision signal currently cannot tell them apart. Deferred: it
   changes the edge shape, and nothing has asked for it yet.
