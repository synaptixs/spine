# Clang recovery diagnosis — confidence step 3

## Scope and method

This follows the [Step 2 caller audit](clang-semantic-correctness-audit.md).
The baseline is `4e368e1`; repository pins and the bundled libclang environment
are unchanged from [Step 1](clang-semantic-ab-results.json). D1–D6 remain in force.

The [diagnostic harness](clang-semantic-recovery-diagnosis.txt) captures the existing
pass's final per-site stage and the rejected enclosing caller's USR, kind and
source grounding. It does not alter production behavior. The diagnostic extraction
reproduced the Step 2 graph and report on both repositories. Diagnostic timing is
excluded from the performance comparison.

A fixed sample contains **50 unresolved sites per repository**: the first 20
`no_matching_call`, 10 `indirect_or_unsupported_target`, 10
`caller_identity_mismatch` and 10 `ungrounded_target` sites after sorting each
bucket by SHA-256 of `clang-misses-v1:` plus Python's representation of
`(file, start_offset, end_offset)`. Selection preceded the recovery measurement.
The [site records](clang-semantic-recovery-misses.jsonl) preserve the hashes,
expressions, source context, observations and diagnoses. This stratified sample
explains failure modes; it does not estimate population recall or precision.

For closer inspection, sampled source files and headers were parsed individually
with the same synthesized flags. These observations are labeled separately:
a header parsed alone can expose a call absent from the pass's selected TUs.
They do not count as production recovery or additional parsed TUs.

## What the misses show

| Step 2 unresolved stage | OpenCV | TinyXML-2 |
|---|---:|---:|
| No matching clang call | 104,040 | 413 |
| Indirect or unsupported target | 28,426 | 168 |
| Unsupported target USR | 145 | 14 |
| Ungrounded caller | 1 | 0 |
| Caller identity mismatch | 1,519 | 31 |
| Ungrounded target | 832 | 338 |

The dominant bucket is not evidence that the USR mapper is too strict. Examples:

- TinyXML-2's sampled casts are cast expressions, not function calls (TM013,
  TM015, TM017, TM018). Debug-only calls are inactive under these flags (TM014,
  TM020). Both remain in the existing CST pending denominator; it was not changed
  to improve the recovery percentage.
- Other TinyXML-2 expressions lack a resolved call/target in clang's recovered
  AST. Their parse reports missing `cctype`. OpenCV samples report missing
  standard headers, generated `cvconfig.h`/`opencv_modules.hpp`, and repository
  include paths that the current header-directory synthesis does not resolve.
  These are observed limitations, not proof that one missing include explains
  every downstream miss.
- OM009 is inside `HAVE_WEBP`; OM002/OM003/OM005 expose calls when their headers
  are parsed directly, but not in the production pass's selected TU context.
- Target-stage samples include scope lost around exported classes, generated
  protobuf helpers, macro-expanded C names and header-only declarations without
  grounded definitions. A plausible name does not permit inventing a node or
  attaching to a different existing ID.

The full caller-mismatch population was also classified using the captured
identity and file observations:

| Caller observation | OpenCV | TinyXML-2 |
|---|---:|---:|
| Existing mapper produces a different identity | 1,107 | 16 |
| Existing mapper agrees, grounding is in another file | 61 | 1 |
| Unsupported identity or disagreement after the extension | 324 | 13 |
| Additional caller shape agrees with identity and source file | 27 | 1 |

These counts identify candidates, not accepted edges. The normal target,
grounding and conflict checks still determine recovery.

## Targeted change

Caller projection now recognizes C++ file-static functions, exact destructor
names and `operator()`. It retains named namespace/class scope and checks any
USR filename against the declaration's basename. The enclosing caller must still
match the CST ID and pass the existing source-grounding guard. Callee projection
is unchanged. Templates, local/anonymous identities and other unsupported shapes
remain refused; no flags, node IDs, node kinds, routing or denominators changed.

Two integration regressions failed before the change and pass afterward:
`test_file_static_callers_preserve_scope_and_source_file` and
`test_exact_destructor_and_call_operator_callers`. Six negative mapper cases
cover wrong filenames, anonymous/local/template identities and other operators.
All Step 2 negative caller regressions remain in the focused suite.

## Recovery and validation

| Repository | Step 2 recovered sites | Step 3 recovered sites | Added edges vs Step 2 | Total added edges vs clang off |
|---|---:|---:|---:|---:|
| OpenCV | 670 / 135,633 | **696 / 135,633 (0.5131%)** | 26 | 686 |
| TinyXML-2 | 415 / 1,379 | **416 / 1,379 (30.1668%)** | 1 | 410 |

One of the 27 OpenCV caller candidates still lacks a grounded target and remains
unresolved. The denominator, routing, node set and all Step 2 edges are preserved.
All additions are calls between pre-existing grounded functions. Existing graph
verification findings are unchanged: OpenCV 3 errors/2 warnings, TinyXML-2
1 error/1 warning.

All **27 newly added edges** received source review of the containing function,
receiver type and target definition. The [edge audit](clang-semantic-recovery-added-edges.jsonl)
records each relationship and rationale; all are correct at the existing name-ID
granularity. This is a complete audit of this small increment, not a whole-graph
precision claim or an independent second-reviewer study.

The fixed 200-edge Step 2 sample now retains **166 reviewed-correct edges**, up
from 157. All **31 incorrect and one ambiguous edge remain refused**. The two
remaining reviewed-correct losses are calls inside the unsupported
`medianBlur_SortNet` template (O039/O040). Existing name-based overload collapse
and conditional-implementation provenance limits remain as documented in Step 2.

Three fresh-process runs per mode use the unchanged A/B harness, with identical
source inputs and header routing. Timing and final gate receipts follow below.


| Current implementation timing | Clang off median | Clang on median | Ratio |
|---|---:|---:|---:|
| opencv | 32.431 s | 65.617 s | 2.02× |
| tinyxml2 | 0.100 s | 0.336 s | 3.36× |

All repeated graph/report checks passed. Complete source hashes, environment,
measurements, Step 2 comparisons and fixed-sample retention are in the
[results](clang-semantic-recovery-results.json); the
[run output](clang-semantic-recovery-output.txt) records the commands' summaries.
These timings compare the current implementation with clang off/on; they do not
claim a speed change from Step 2's single diagnostic measurement.

The small increase in coverage does not change the release recommendation:
keep general shipment pending. A release decision must accept the measured cost,
very low OpenCV recovery and existing verification limits. Any wider recovery
work should separately address synthesized repository include roots or CST
macro/scope grounding with its own precision evidence; it must preserve D1–D6.

## Final local gates

```text
3769 passed, 4 skipped, 51 deselected, 182 warnings in 220.58s (0:03:40)
```

All 65 focused semantic tests passed. `mypy src tests` checks 721 files; ruff lint
and format pass. State numbers, matrix counts, both generated SVGs, MCP inventory
and roadmap currency pass. Accuracy reports zero gated regressions and zero
improvements. All four SDLC repository shapes pass; Spine self-verification reports
zero errors and one warning. The retained diagnostic harness was rerun on
TinyXML-2 and reproduced the recorded baseline graph/report. Workspace files were
frozen throughout the full suite. No release or merge is authorized by these checks.

## Follow-up — Step 3b measured

The [Step 3b evaluation](clang-semantic-step3b.md) completes the five-repository
include-root comparison and source audit. It retains all 27 Step 3 additions,
fixes local-lambda identity errors, and records five correct OpenCV edge losses.
The measurements above remain Step 3 history. Required Step 3b local gates passed;
optional usefulness is assessed by repository profile, with no OpenCV-only veto.
