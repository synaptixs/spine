# Clang correctness audit — confidence step 2

## Outcome

The original 200-edge source audit **did not pass**: 168 relationships were correct
at Spine's documented name-based ID granularity, 31 had incorrect caller identities,
and one had ambiguous caller grounding. The defect was accepting any existing
caller ID without checking the function that actually contained the call.

The caller guard now refuses all 31 incorrect and the one ambiguous sampled edge.
Of the fixed sample, **157 reviewed-correct edges remain**; 11 reviewed-correct
edges are also conservatively refused. The sample was not refilled after filtering.
No new edges appear outside the previous clang-on graph. This completes the audit
and the demonstrated defect fixes, not a claim of perfect population-wide precision.
Draft [MR #379](https://github.com/synaptixs/spine/pull/379) remains unreleased.

| Fixed sample | Original correct | Original incorrect | Ambiguous | Correct retained after guard |
|---|---:|---:|---:|---:|
| OpenCV | 85 | 14 | 1 | 74 |
| TinyXML-2 | 83 | 17 | 0 | 83 |
| Total | 168 | 31 | 1 | 157 |

## Population, selection and review method

The population is the 827 OpenCV and 428 TinyXML-2 added graph edges from
[step 1](clang-semantic-validation.md#confidence-step-1--isolate-the-current-clang-contribution),
using Spine implementation `5d3b2fd` and the evidence at `eccefbc`. Repository pins
remain OpenCV `b4c5ec4042f097e2a5b386b9d413ec7333d0a184` and TinyXML-2
`8224e427b655b83dae5e2298f1e6919523a78737`.

An edge includes call-site line provenance. Multiple calls sharing a relationship
and line may coalesce; the audit unit is therefore a graph edge, not every recovered
byte-range site. We selected 100 edges per repository by fixed SHA-256 ranking,
seed `clang-correctness-v1`, within these strata:

| Repository | Stratum | Selected / available |
|---|---|---:|
| OpenCV | Header call sites | 10 / 28 |
| OpenCV | Third-party implementation call sites | 20 / 50 |
| OpenCV | Application/sample call sites | 20 / 88 |
| OpenCV | Remaining implementation/test call sites, including HAL | 50 / 661 |
| TinyXML-2 | Header call sites | 16 / 16 |
| TinyXML-2 | `tinyxml2.cpp` | 30 / 105 |
| TinyXML-2 | `xmltest.cpp` | 53 / 306 |
| TinyXML-2 | Contributed printer | 1 / 1 |

Selection preceded the fix and verdicts. One additional destructor example was
noticed during initial inspection and is reported separately below, not inserted
into the fixed sample. Equal repository allocation and enriched small strata mean
these counts are **not an estimate of whole-repository precision**.

The source review inspected call expressions, receiver declarations/casts, inherited
members, enclosing functions/classes/namespaces, macro definitions and target
member definitions. It did not use clang's own target answer as an independent
oracle. Source excerpts were grouped by file for inspection; each of the 200
verdicts has a rationale and source locations in the saved sample. This is one
reviewer's source audit, not a blinded second-reviewer study.

Examples of the covered cases:

- Nested expressions: O005/O006, O029, O096, T016–T020, T068 and T090–T095.
- Overloads: O035, O039/O040, O061, T002, T014, T031 and T042–T047. Correctness is
  assessed at the existing name ID; it does not claim overload disambiguation.
- Inheritance: T002, T013, T017–T020, T029, T031 and T069–T072.
- Virtual declarations: O027/O028/O030; the expected target is the static receiver
  declaration (`boost.h:44`, `old_ml.hpp:669,687`), not the dynamic override.
- Header bodies and export/namespace macros: O007, O082, O086–O093 and T032–T047.
- Pointer, reference, cast and temporary receivers: O001/O002, O027/O029,
  O068–O075, O088/O092 and O100.

## Defects and ambiguous cases

| Cases | Source evidence | Verdict |
|---|---|---|
| O003–O009 | OpenEXR's `OPENEXR_IMF_INTERNAL_NAMESPACE_*_ENTER` macros expand to namespace blocks (`ImfNamespace.h:108,111`); callers omit that namespace. O007 additionally lies in an anonymous namespace. | Incorrect caller scope; target `half` member is plausible but the complete edge is wrong. |
| O048, O076–O079 | Distinct `TEST(...)` bodies attach to `cpp:opencv_test::TEST`, grounded in a different test file. GTest's macro defines distinct test bodies (`ts_gtest.h:22179–22187`). | Incorrect caller identity. |
| O098/O099, T001 | Separate executable entrypoints attach calls to the first grounded `cpp:main` from another program. | Incorrect caller grounding. |
| T032–T047 | Inline bodies belong to `tinyxml2::XMLNode` or `tinyxml2::XMLElement`; exported class syntax lost CST scope, producing global caller IDs. | Incorrect caller scope. |
| O081 | A WinRT API body maps to a node whose source locator is a no-GUI stub. The logical API name matches, but the selected implementation is ambiguous. | Ambiguous; refused, not counted correct. |

O086/O087 remain correct only at name-ID granularity: the conditional definitions
of `ImplMutex::Impl::init/destroy` share IDs, and the stored locator identifies the
Windows branch. This graph cannot identify a platform-specific implementation.
That limitation is explicitly preserved rather than claiming precise body provenance.

Supplemental source checks, outside the 200-edge denominator:

- `cap_ffmpeg_impl.hpp:897`: `mutex->unlock()` is in `~AutoLock()`, but its edge
  used the constructor ID `cpp:AutoLock::AutoLock`. The guard refuses it.
- `xmltest.cpp:2560`: `doc.LoadFile()` is inside the local class method
  `TestUtil::TestFileLines`, but its edge used outer `cpp:main`. The guard refuses it.
- `tinyxml2.cpp:821`: the correctly scoped `XMLNode::~XMLNode` call to its parent's
  `Unlink` is also refused because destructor USRs are outside the accepted mapper
  shapes. This is an explicit conservative loss, not a discovered false edge.

## Fix and regression proof

The clang walk now tracks the enclosing function. Before accepting a candidate:

1. The enclosing function's USR must map to the exact grounded CST caller ID.
2. Its source file must match the grounded caller's provenance. An out-of-line
   member may use a grounded overload inside its semantic class declaration in a
   header; that exception preserves the documented overload identity.
3. Entering a nested class resets the enclosing function context; a local method
   cannot inherit the outer function's caller identity. Lambdas remain excluded.

Mismatch or unsupported caller identity is reported as `caller_identity_mismatch`.
The mapper's accepted shapes now constrain callers as well as targets. This can
reject otherwise correct static/template/operator/destructor callers. None of the
existing CST nodes, IDs or edges is repaired, renamed or removed; D1–D6 stay intact.

Five negative regression cases cover destructor/constructor collisions, test-macro
bodies, separate program entrypoints, lost caller scope and local class methods.
All five fail on the old implementation. A positive case keeps the out-of-line
constructor/header overload relationship. The focused semantic suite reports:

```text
57 passed, 30 warnings in 0.62s
```

The existing corpus additivity test passes as part of that suite.

## Post-fix repository validation

| Measurement | OpenCV | TinyXML-2 |
|---|---:|---:|
| Nodes, unchanged | 87,181 | 425 |
| Total edges | 397,834 | 1,994 |
| Added edges versus clang off | 660 | 409 |
| Previously added edges now refused | 167 | 19 |
| Recovered pending sites | 670 / 135,633 (0.4940%) | 415 / 1,379 (30.0943%) |
| Parsed / total TUs | 1,981 / 2,468 | 3 / 3 |
| Verification errors / warnings, unchanged | 3 / 2 | 1 / 1 |
| One post-fix extraction observation | 65.597 s | 0.348 s |

Both repositories were re-extracted with the executed step-1 harness. Comparisons
against the saved step-1 clang-off and clang-on snapshots prove unchanged nodes,
header-routing sets, pending-site records, complete verification issue records,
and preservation of every baseline edge. All remaining additions are CALLS
between previously grounded functions and are a subset of the old additions.
Every incorrect or ambiguous sampled edge is absent after the fix.

The guard removed 186 edges overall. They are **not all established false positives**:
11 reviewed-correct sample edges and the supplemental scoped destructor are among
the conservative losses. The remaining unaudited population has no independent
correctness verdict. Further supported-caller work belongs in the next improvement
step and must retain the new negative regression tests.

Timing here is one observation per repository, with no concurrent test suite. It
is not a new repeated off/on performance estimate; step 1's ratios describe the
previous implementation. The current implementation hash is recorded with the
results. The original saved graphs were generated locally by the trusted harness.

## Evidence and reproduction

- [All 200 source verdicts and post-fix retention](clang-semantic-correctness-sample.jsonl).
- [Structured results and implementation hash](clang-semantic-correctness-results.json).
- [Deterministic selection script](clang-semantic-correctness-selection.txt).
- [Post-fix comparison script](clang-semantic-correctness-comparison.txt).
- [Post-fix extraction output](clang-semantic-correctness-output.txt).

The selection script reads the original step-1 added-edge receipt and pinned roots
at `/tmp/spine-clang-ab-{opencv,tinyxml2}`. It writes the selected rows and source
packets under `/tmp`. The comparison script reads the reviewed verdicts and trusted
before/after graph snapshots; it checks retention, not semantic correctness. Source-review
judgments are preserved in the JSONL and are not manufactured by a matcher.
For a rerun, convert the JSONL to `/tmp/spine-audit-reviewed.json` as a JSON array;
the comparator consumes that path and `/tmp/spine-clang-audit-after/{repo}/on.pickle`.
The step-1 harness can regenerate the baseline snapshots at the recorded commit.

**Release decision remains pending.** Step 2 provides stronger correctness evidence
and fixes demonstrated unsafe additions. It does not resolve low OpenCV recovery,
pre-existing graph verification errors or untested platform installation support.


## Final regression receipt

```text
3761 passed, 4 skipped, 51 deselected, 182 warnings in 221.76s (0:03:41)
pkg accuracy --check: OK — 0 gated regression(s), 0 improvement(s).
pkg verify: OK — 0 error(s), 1 warning(s).
All four repository shapes hold.
```

The workspace was unchanged throughout the full pytest run. Required type, lint,
format, generated-artifact and roadmap checks pass. The semantic-only suite
passes all 57 cases. No corpus scoreboard changes were required. The existing
real-repository verification failures remain recorded separately above.
