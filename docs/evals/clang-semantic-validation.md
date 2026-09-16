# Clang semantic pass validation

This track adds an optional semantic post-pass to the existing C/C++ CST front-ends.
Decisions: repository-only synthesised flags; no compilation database; edges only
between grounded nodes; `.h` routing is in scope; `clang` joins `all`, not `languages`;
parse only translation units with unresolved CST call sites.

## P0 — 2026-09-15, base `94f106a`

The supplied OpenCV-fork baseline is preserved, not re-measured:
`https://github.com/synaptixs/aiopencv`, branch `4.13.0-python`:

| Measure | Recorded baseline |
|---|---|
| Total graph | 81,479 nodes / 383,412 edges / 19,390 dangling; 33 seconds |
| C/C++ graph | 69,999 nodes, 66,634 grounded / 359,703 edges |
| C/C++ CALLS with ungrounded targets | 175,385 / 239,211 (73.3%) |
| Types from `.h` | 352 struct/union/enum nodes; zero classes |
| C++ instance-call corpus | 4 expected / 3 emitted / 3 matched |

### Wheel receipt

`GET https://pypi.org/pypi/libclang/18.1.1/json` returned these wheel filenames:

```text
libclang-18.1.1-py2.py3-none-manylinux2010_x86_64.whl
libclang-18.1.1-py2.py3-none-manylinux2014_aarch64.whl
libclang-18.1.1-py2.py3-none-manylinux2014_armv7l.whl
libclang-18.1.1-py2.py3-none-win_amd64.whl
libclang-18.1.1-py2.py3-none-win_arm64.whl
```

### Smaller validation repository

[TinyXML-2](https://github.com/leethomason/tinyxml2), commit
`8224e427b655b83dae5e2298f1e6919523a78737`, has in-repository classes in `tinyxml2.h`
and three C++ source files. The baseline extraction takes 0.127 seconds:
311 nodes / 1,395 edges; 1,002 C/C++ CALLS, 408 with ungrounded targets;
one Type from `.h`. Verification already fails with 608 dangling edges, including
CONTAINS edges from missing `.h` classes. There are 14 phantom-module warnings.
This is useful evidence for the routing phase, not a clean verification baseline.

The new census adapter was run with:

```sh
uv run --with libclang==18.1.1 python scripts/parse-census.py clang /tmp/spine-clang-tinyxml2 --suffix .cpp --json
```

Result: 3 TUs scanned; 3 with errors, each reporting `'cctype' file not found`;
1,873 CALL_EXPR and 210 CXX_METHOD cursors in source files. Diagnostics measure
parse coverage, not member-call recall. No system include paths are supplied.

### Quality and impact receipts

CI extras were synced exactly as prescribed. P0 gate output:

```text
Success: no issues found in 719 source files
All checks passed!
756 files already formatted
state-numbers --check: OK — 14 gated claim(s) match; 9 trended.
46 capability rows
21 where Spine stands alone
3 where Spine is ❌
3 at 🟡
assets/spine-architecture.svg is current
assets/knowledge-foundation.svg is current, and its layout checks pass
```

The supplied roadmap leaves the full-suite, accuracy and Spine-verification baseline
receipts empty. They are not re-measured here, following the execution request;
those checks remain mandatory before the MR.

The planned `orchestrator blast-radius` command returns `No such command` on this
revision. The real method is `RepoCodeExtractor.extract`, not `extract_repo`.
Using `FactStore.callers_of` and `impact_of` (default depth 4): extraction reports
0 callers / 0 impact nodes, fingerprint 8 / 43, and resolve-or-drop 4 / 6. The zero
for extraction is a limit of the existing Python instance-call graph, not evidence
that no consumers exist; `understand`, `state`, exports and grounding consume it.

## P1–P3 — mapper, packaging, wired pass

P1 (`3afa37a`): 30 mapper cases passed, including refusal cases. P2 (`6b8e593`):
89 tests passed across mapper, persistence and doctor; `doctor` lists `clang`.
The fingerprint changes when the wheel is present and when its version changes.

P3 targeted result: `80 passed, 31 warnings in 1.92s`. The only corpus changes
against the pre-clang scoreboard are C++ CALLS emitted **3 → 4** and matched
**3 → 4**, with expected **4** unchanged. All other corpus cells are unchanged;
all populated cells retain precision 1.00. `pkg accuracy --check` reports
`OK — 0 gated regression(s), 0 improvement(s)` against the regenerated scoreboard.

`test_corpus_is_additive_only` compares every fixture root with the optional pass
disabled/enabled: identical node sequences, a superset of edges, and both endpoints
of every additional edge grounded. The remaining tests cover reference/pointer
receivers, static virtual dispatch, multiple sites on a line, unavailable headers,
external targets, function pointers, header call sites, parse failure, extractor
reuse, different checkout paths, ignored `.cu`/`.mm`, and ignored compilation DBs.

The instance-call fixture prints:

```text
clang: resolved 1 of 1 unresolved call sites in 1 of 1 TUs; 0 with diagnostics, 0 failed
```

All phase quality gates pass. Impact API check before the routing phase:
extract 0 callers / 0 impact nodes (existing instance-call limitation);
fingerprint 11 / 44; resolve-or-drop 5 / 8.

## P4 — header routing

Routing tests plus the C, C++, profile and semantic tests:
`79 passed, 30 warnings in 0.46s`. Both new corpus cases have empty `missing` and
`unlabelled` lists and precision/recall 1.00 on all their populated cells:
`cpp/header_classes` and `c/header_unaffected`. Aggregate C++ CALLS becomes 5/5/5
because the new header fixture adds one independently labelled call.

Routing follows CST literal includes from `.cpp`, `.cc` and `.cxx`, transitively,
including uniquely resolved in-repo angle includes. Cycles terminate; ambiguous
header basenames are refused. Headers not reached remain C. Tests prove routing
works without clang, does not enter nested repositories/hidden fixture directories,
and keeps a C-only header's original ids. Both new fixture roots are `.repo/`.

P4's mypy, lint, format, four artifact checks and roadmap checks all pass.

## P5 — initial shipping stop

**Do not treat the optional extra as ready to ship.** On the requested OpenCV fork,
the pass recovered **121 of 130,001 pending call sites (0.0931%)**. TinyXML-2
recovered **13 of 1,178 (1.1036%)**. The execution request explicitly requires a
user decision when the measured result is too weak to justify the extra; no
resolution changes were made before that stop. The authorized revision below supersedes
this result for the shipping decision.

These are **recovery fractions of CST-unresolved sites**, not independently labelled
whole-repository recall. The real repositories have no gold call graph. Missing
system headers, unsupported USRs, indirect calls, macros and absent grounded
symbols all remain in the denominator; none have been exempted. The diagnostics
count does not isolate how many misses are attributable to system headers.

The target clone is branch `4.13.0-python`, commit
`b4c5ec4042f097e2a5b386b9d413ec7333d0a184`. TinyXML-2 remains at the P0 commit.
Both were copied without `.git` before running:

```sh
uv run --frozen python -u scripts/validate-frontend.py cpp /tmp/spine-clang-tinyxml2-validation /tmp/spine-clang-aiopencv-validation
```

### OpenCV fork — supplied baseline versus P5

| Measure | Supplied baseline | P5 |
|---|---|---|
| Nodes | 81,479 | 87,181 (80,422 grounded, 6,759 external) |
| Edges | 383,412 | 397,290 |
| C/C++ nodes | 69,999 (66,634 grounded) | 77,682 (72,101 grounded) |
| C/C++ edges | 359,703 | 374,747 |
| C/C++ CALLS with ungrounded targets | 175,385 / 239,211 (73.3%) | 177,532 / 243,181 (73.0041%) |
| Types sourced from `.h` | 352, all struct/union/enum | 1,517 |
| Extraction time | 33 s | 71.716 s (+38.716 s; 2.173×) |

The original baseline says “19,390 dangling” without defining its counting unit.
P5 reports **19,982 unique missing ids** and **190,485 edges with missing endpoints**
separately; these are different measures and should not be conflated.
The node and edge deltas include P4's CST header routing, not just clang enrichment.
The full Python test suite was running concurrently with P5; the observed timing
is end-to-end extraction under that load, not an isolated libclang overhead estimate.

```text
clang: resolved 121 of 130001 unresolved call sites in 1981 of 2468 TUs; 1950 with diagnostics, 0 failed
```

D6 skipped **487 of 2,468 TUs (19.7%)**. Diagnostics occurred in **1,950 of
1,981 parsed TUs (98.4%)**. `.cu` and `.mm` produced **zero C/C++ source nodes**
and neither suffix entered the TU set. Verification reports three errors
(dangling edges, Java orphan rate, Python orphan rate) and two warnings
(phantom modules and eight scope-bound CALLS). This is not a passing real-repo
verification result. The provided baseline did not record those verification
categories, so no claim is made that every category is pre-existing.

### TinyXML-2

| Measure | P0 | P5 |
|---|---|---|
| Nodes / edges | 311 / 1,395 | 425 / 1,598 |
| C/C++ CALLS with ungrounded targets | 408 / 1,002 | 412 / 1,068 |
| Types sourced from `.h` | 1 | 6 |
| Extraction time | 0.127 s | 0.405 s (+0.278 s) |

```text
clang: resolved 13 of 1178 unresolved call sites in 3 of 3 TUs; 3 with diagnostics, 0 failed
```

P5 still has one verification error and one warning: 581 dangling edges and
phantom modules. P0 already failed with 608 dangling edges. The state stack is
`cpp`, `javascript`, `python`; a call graph is available.

### P5 checks

The validation-script tests report `6 passed in 0.31s`. They check that recovery
and TU denominators stay distinct. P5 mypy, lint, format, all four artifact checks,
and the roadmap check pass. The documentation audit reports
`0 STALE/MISSING, 39 INFO`; final P6 documentation and review work is pending.

### Interrupted work at the decision point

The OpenCV extraction and verification above completed. The subsequent `state`
summary was interrupted after the shipping stop condition was established. Its
trace was inside `stats.summarise_store` → `store.callers_of`, after extraction,
so the **71.716-second extraction measurement is complete**. The entire smoke
script did not finish; no successful end-to-end state result is claimed for this
repository. [Captured output](clang-semantic-p5-output.txt) includes the interruption.

The broader pre-MR pytest run was also interrupted, with this **partial** summary:

```text
22 failed, 3308 passed, 4 skipped, 51 deselected, 182 warnings in 317.39s (0:05:17)
```

Failures include denied writes to the Spine and Go caches and a Jira DNS lookup.
This run used the ordinary sandbox, unlike the approved phase gates. It does not
establish a code regression, and it is not a green full-suite receipt. A complete
run with appropriate test-environment permissions remains required before an MR.


## P5 revision — authorized diagnosis and fixes (2026-09-15)

The user authorized diagnosis, mapper-policy revision and re-measurement while
preserving D1–D6. The original §10 baseline and §1 probes were not re-run.

### Diagnosis and fixes

The original mapper rejected ordinary argument encodings and `const`, static and
reference qualifiers. Those are valid identities for functions the CST already
keys by qualified name, including overloads. The revised mapper validates the
namespace/class/function prefix separately and projects the signature onto that
existing identity. Signature types remain opaque; this consumes clang-generated
USRs, not arbitrary user strings. Template declarations/instantiations, local and
anonymous declarations, operators and destructors remain refused. The encoding
boundary follows LLVM 18's
[USR generator](https://github.com/llvm/llvm-project/blob/llvmorg-18.1.1/clang/lib/Index/USRGeneration.cpp#L209-L276).

Before the fix, 676 TinyXML-2 sites and 2,951 OpenCV sites stopped at the mapper.
OpenCV additionally had 99,204 sites without a matching clang `CALL_EXPR`, 27,388
with an indirect/unsupported target and 337 with an ungrounded target. These
categories partition distinct sites by the furthest stage reached across TUs.
They do **not** identify the cause of missing AST expressions: preprocessing,
unavailable headers and parse recovery can all contribute.

A second bug appeared when the mapper admitted more targets: nested calls such as
`a.first(1).second(2)` share a start offset. Indexing only that offset collapsed two
pending sites into one and could conflate their targets. The side channel now
records the full byte range, and matches both range endpoints. No graph IDs or
nodes change. `test_nested_member_calls_with_same_start_have_separate_sites`
failed before the fix and now passes. The corrected denominator therefore counts
more sites; neither old nor new recovery is a labelled whole-repository recall.

Pending sites are also indexed by file so selecting a TU's reachable pending
sites no longer scans the entire repository's pending list for every TU. The set
of selected TUs and the synthesized flags are unchanged.

`ClangReport.unresolved_reasons`, printed in the validation script's semantic
metrics, accounts for every unresolved distinct site. `no_matching_call` includes
unparsed/failed TUs; `indirect_or_unsupported_target` includes call cursors without
an eligible function/method declaration; `outside_repository` includes targets
outside the admitted CST files. Later stages distinguish refused identities,
ungrounded callers/targets and conflicts between candidate IDs. Repeated header
observations count once, and resolved sites do not appear in the miss buckets.

The diagnosis also reproduced a pre-existing CST limitation: an inline method
returning `A&` was absent from the grounded function set, whereas its `A` and `A*`
variants were present. This revision leaves that node-production behavior intact;
such targets remain in the ungrounded bucket under D3.


### Validation environment

The earlier Jira failure was reproduced as a test-isolation error:
`test_unconfigured_adapter_raises` constructed `JiraConfig()` and therefore read
`.env`, even though the shared fixture had removed process credentials. Its
configuration now passes `_env_file=None`, consistent with other unconfigured
adapter tests. Application behavior and the user's `.env` are unchanged.
The full-suite rerun uses approved cache access. It also exposed a second
isolation gap: `test_sdlc_feature_accepts_go_language` let `run_feature` reload
`.env` and wait for a live source fetch. That fetch was interrupted; `CliRunner`
caught the interrupt, so the run's eventual passing summary was not accepted as
an uninterrupted receipt. The test now runs in an empty temporary directory and
also asserts the expected unconfigured-source exit code. Both isolation cases
pass together (`2 passed in 0.25s`); a fresh full run follows those fixes.


### Final re-measurement and shipping recommendation

| Measure | Initial P5 | Revised P5 |
|---|---|---|
| OpenCV recovered sites | 121 / 130,001 (0.0931%) | **949 / 135,633 (0.6997%)** |
| TinyXML-2 recovered sites | 13 / 1,178 (1.1036%) | **434 / 1,379 (31.4721%)** |
| OpenCV extraction time | 71.716 s (concurrent suite) | **63.160 s** |
| TinyXML-2 extraction time | 0.405 s | **0.300 s** |
| OpenCV total nodes / edges | 87,181 / 397,290 | **87,181 / 398,001** |
| TinyXML-2 total nodes / edges | 425 / 1,598 | **425 / 2,013** |
| OpenCV C/C++ nodes / grounded / edges | 77,682 / 72,101 / 374,747 | **77,682 / 72,101 / 375,458** |
| OpenCV C/C++ ungrounded CALLS / all CALLS | 177,532 / 243,181 | **177,532 / 243,892 (72.79%)** |
| TinyXML-2 C/C++ ungrounded CALLS / all CALLS | 412 / 1,068 | **412 / 1,483** |

The full-range fix adds 5,632 previously collapsed OpenCV sites and 201 TinyXML-2
sites to the denominator. The intermediate mapper-only run recovered 944/130,001
and 383/1,178 respectively; the final numbers above include **both** fixes.

The final OpenCV miss partition is **104,040 no matching call**, **28,426
indirect/unsupported targets**, **145 refused USRs**, **1 ungrounded caller**, and
**2,072 ungrounded targets**. These sum with 949 recovered sites to 135,633. Thus
further relaxing the USR mapper alone cannot address most misses. TinyXML-2 has
413 no matching calls, 168 indirect/unsupported targets, 14 refused USRs and 350
ungrounded targets; those plus 434 recovered sum to 1,379. Neither final run has
conflicting target IDs.

OpenCV still parses **1,981 / 2,468 TUs** (487 skipped), with **1,950 diagnostic
TUs and zero failed TUs**. TinyXML-2 still parses 3/3, all with diagnostics and zero
failures. Header Type counts stay 1,517 and 6 respectively; `.cu`/`.mm` still
contribute zero C/C++ nodes. OpenCV has 19,982 missing IDs and 190,485 dangling
edges; TinyXML-2 has 91 and 581. Real-repo verification still reports the same
categories/counts as initial P5: OpenCV three errors/two warnings, TinyXML-2 one
error/one warning. These fixes do not manufacture nodes to repair those errors.

Both final runs assert identical node sequences around the semantic
pass, preservation of every existing edge, and grounded endpoints for every added
edge: **827 OpenCV edges and 428 TinyXML-2 edges**. Edge counts differ from recovered
site counts because graph edges coalesce repeated relationships/provenance.

Measurement uses the same pinned commits documented above, copied without `.git`.
It runs `RepoCodeExtractor.extract` followed by `verify_batch`, with diagnostic counters and assertions
around `link_clang`; their overhead is included in the
reported extraction time. No full suite or other benchmark ran concurrently with
final extraction. The downstream `state` stage was deliberately not part of this
diagnostic run; it has not gained a successful completion receipt. These are
single-run observations, not statistically controlled performance estimates.
Relative to the supplied 33-second original baseline, final OpenCV extraction is
30.16 seconds longer (1.91×); that includes header routing as well as the pass.

[Machine-readable measurements](clang-semantic-p5-revision.json) preserve the
before-mapper, mapper-only and final runs.
[Final captured output](clang-semantic-p5-revision-output.txt) includes both D3
assertions and verification findings. The saved
[measurement harness](clang-semantic-p5-harness.txt) is executable Python kept as a
text receipt so the repository's own source walker does not ingest diagnostic
code as product code. Reproduce each final run with:

```sh
.venv/bin/python -u docs/evals/clang-semantic-p5-harness.txt /path/to/gitless-copy result-label
```

The root must contain the recorded commit, with the project's CI extras and
`clang` installed in `.venv`; the harness writes metrics under `/tmp` and prints
verification findings. It does not run the downstream state summarizer.

**Recommendation: hold shipment for the OpenCV target.** The mapper defect is fixed
and the smaller repository benefits, but 0.70% pending-site recovery with roughly
double the supplied extraction time remains weak evidence for the proposed extra.
P6 and the MR remain paused under the user's explicit P5 shipping stop rule.
D1–D6 are preserved; changing the compile environment or adding node-producing
fallbacks has not been attempted.


### Final checks for this revision

```text
98 passed, 30 warnings in 0.67s (semantic, C/C++, header routing, profile and validation tests)
2 passed in 0.25s (both local-environment isolation cases)
3753 passed, 4 skipped, 51 deselected, 182 warnings in 195.27s (0:03:15)
pkg accuracy --check: OK — 0 gated regression(s), 0 improvement(s).
Success: no issues found in 721 source files
All checks passed!
758 files already formatted
state-numbers --check: OK — 14 gated claim(s) match; 9 trended.
46 capability rows; 21 alone; 3 no; 3 partial
Both generated SVG checks pass.
roadmap-status --check: OK — 3 phase table(s) checked, 7 checks each.
```

The full pytest receipt is from the uninterrupted rerun after both isolation
fixes. The four skips require E2B credentials, pytesseract, PHP/Composer or the
opt-in PostgreSQL integration environment. The repository's default pytest
selection deselects integration/real-LLM tests; it was not narrowed for this run.
The corpus comparison still checks identical nodes, additive edges and grounded
new endpoints with the optional pass disabled/enabled. Corpus scores did not
change, so the scoreboard was not regenerated.

All four validation clone/copy directories and the scratch CST probe were removed.
The roadmap remains excluded and untracked; `episteme/` is not part of this revision.


## P6 — authorized before returning to P5

The user requested P6 completion before revisiting P5. Documentation now explains
installation, header routing, grounded-only enrichment, bounded reports and the
measured limits. The [manual review record](clang-semantic-review.md) walks the
full documentation matrix and relevant implementation/checklist sites.

The review fixed a mismatch in profiling: a C++ TU inside a nested checkout could
reclassify its parent repository's header. Profiling now observes the same `.git`
file/directory boundary as extraction. The regression test failed before the fix
and passes afterwards. No semantic graph identities or compile rules changed.

All four pipeline shapes hold, Spine verification reports 0 errors/1 warning,
and `understand .` builds 91 files. Physical libclang absent/present environments
produce identical nodes across all 53 corpus roots, preserve every existing edge,
and add edges only between grounded endpoints. The final release decision remains
separate from completing these checks; the MR is prepared as a draft while P5's
large-repository state smoke is revisited.

P6 full suite: `3754 passed, 4 skipped, 51 deselected, 182 warnings in 211.56s (0:03:31)`. Phase gates and MCP inventory pass; accuracy reports zero gated regressions.


## P5 return after P6 — complete state smoke

P6 completed in draft [MR #379](https://github.com/synaptixs/spine/pull/379), based
on commit `a604472`. Returning to the incomplete `state` stage exposed the existing
cost in `stats.summarise_store`: `callers_of` scanned all edges once per function.
A regression fixture with 87 edges visited **7,134 edges** while preserving the
right counts. Counting incoming calls during the existing edge-count pass visits
those 87 edges once. The test compares the result with `callers_of`, including
missing endpoints, external function targets, non-function callers/targets,
repeated call sites, and deterministic ties, and verifies no graph mutation.

This changes summary aggregation only. It does not change clang, compile flags,
CST nodes, IDs, graph edges, or the recovery denominator. The full validation
script is rerun on the same pinned commits and `.git`-less copies, including its
previously unfinished downstream state stage.


### Completed end-to-end results

The original `scripts/validate-frontend.py cpp <tinyxml2-copy> <opencv-copy>`
completed extraction, verification, state rendering and unresolved-import reporting
for **both** repositories. [Complete captured output](clang-semantic-p5-complete-output.txt)
ends with `validate-frontend: FAILED — 2 repo(s) checked` because the known graph
verification errors remain; the process was not interrupted and neither state
stage failed.

| Repository | Extraction | Semantic recovery | Completed state |
|---|---|---|---|
| TinyXML-2 (`8224e42`) | 0.282 s; 425 nodes / 2,013 edges | 434/1,379 (31.4721%); 3/3 TUs | 5 modules, 8 types, 243 functions, 76 fields, 319 docs; call graph available |
| OpenCV fork (`b4c5ec4`) | 56.368 s; 87,181 nodes / 398,001 edges | 949/135,633 (0.6997%); 1,981/2,468 TUs | 4,752 modules, 7,156 types, 42,621 functions, 25,893 fields, 1,981 docs; call graph available |

All extraction counts and miss categories match the prior revised P5 measurement.
Timing varies between runs; the summary optimization occurs after extraction and
is **not** the cause of the 63.160 → 56.368-second extraction-time difference.
The full regression suite started only after the timed extraction had finished;
it overlapped the later state stage. No controlled state-speedup ratio is claimed.
The generated state contains docs in addition to extracted source facts.

Verification remains: TinyXML-2 **1 error/1 warning** (dangling edges and phantom
modules); OpenCV **3 errors/2 warnings** (dangling edges, Java/Python orphan rates,
phantom modules and scope-bound call warnings). This is completed validation with
reported limitations, not a clean external graph or a release recommendation.
All temporary clone/copy directories and the isolated no-clang environment were
removed after validation.

### Final regression receipt

```text
48 passed in 3.47s (statistics and state tests)
3755 passed, 4 skipped, 51 deselected, 182 warnings in 198.13s (0:03:18)
pkg accuracy --check: OK — 0 gated regression(s), 0 improvement(s).
```

Mypy, lint, format, generated artifacts and roadmap checks pass. CI's README
absolute-link requirement exposed three new relative links; commit `f5a3004`
corrects them without changing application behavior. P5 validation and P6 review
are complete; the draft MR retains the measured recovery/verification limits for
the release decision.


## Confidence step 1 — isolate the current clang contribution

**Result: passed.** On Spine `5d3b2fdb20a0083c54f3dd81f112719218ccf40a`,
three clang-off and three clang-on extractions per repository preserved identical
nodes, every existing edge, identical header routing and identical pending-site
inputs. Every added edge is a CALLS edge between two previously grounded Function
nodes. The **complete verification issue records are identical** with clang off
and on, across all six runs on each repository. The observed verification
failures therefore pre-exist the clang pass in this revision; this comparison
does not attribute them between header routing and other extraction stages.

This is a comparison of the **current revision against itself**, with D4 enabled
in both modes. It is not a rerun of the original roadmap baseline.

### Graph and verification results

| Measurement | OpenCV | TinyXML-2 |
|---|---:|---:|
| Nodes, off = on | 87,181 | 425 |
| Edges, off → on | 397,174 → 398,001 | 1,585 → 2,013 |
| Added CALLS edges | 827 | 428 |
| Removed edges | 0 | 0 |
| Recovered pending sites | 949 / 135,633 (0.6997%) | 434 / 1,379 (31.4721%) |
| Verification, off = on | 3 errors / 2 warnings | 1 error / 1 warning |

OpenCV's existing findings are dangling edges, Java/Python orphan rates, phantom
modules and scope-bound-call warnings. TinyXML-2's are dangling edges and phantom
modules. No new finding or changed finding message appears with clang enabled.
Node and edge records repeat exactly within each mode across all three runs;
clang reports also repeat exactly. Nodes additionally retain the same order.
The precise header-routing set (421 OpenCV headers) and pending-site records have
identical hashes across modes and repetitions. Input-file manifests match before
and after the experiment for both repositories.

### Repeated extraction timing

| Repository | Off: three runs (seconds) | On: three runs (seconds) | Median off → on | Added median time | Ratio |
|---|---|---|---|---|---|
| OpenCV | 32.676, 31.785, 31.235 | 62.051, 62.750, 61.338 | 31.785 → 62.051 s | +30.267 s | 1.95× |
| TinyXML-2 | 0.129, 0.104, 0.109 | 0.411, 0.307, 0.295 | 0.109 → 0.307 s | +0.197 s | 2.81× |

Each extraction uses a fresh Python process, on the same machine and same source
root, in the order **off, on, on, off, off, on**. The middle pair reverses order.
Clang-off forces only `clang_available()` to return false; the installed wheel,
CST front-ends, `.h` routing and remaining passes are unchanged. The on-path uses
the real installed `libclang 18.1.1` wheel. Both modes call the normal extraction
pipeline directly, without a saved extraction cache. This isolates the optional
pass; it is not a physical wheel-uninstallation test (P6 recorded that separately).

The timer covers extractor construction and `extract()`. Hashing, graph
comparison, serialization and `verify_batch()` are outside the timer. Source
manifest hashing reads every input before the first run, so these are repeated
runs with pre-read source files, not a cold-disk benchmark. Library loading remains
part of extraction. No other benchmark or test suite was launched concurrently.
The host is macOS 26.6.2 arm64, Python 3.12.7. These are three observations per
mode on one machine, not cross-platform performance guarantees. The first small
repository on-run is slower; all observations are retained.

The paired measurements isolate roughly 30 seconds of added OpenCV extraction
cost from clang while retaining D4 in the off-path. They replace comparisons to
the original 33-second baseline when discussing **clang-only overhead**. They do
not measure the state renderer or change the earlier end-to-end completion receipt.

### Reproduction and evidence

- [Machine-readable results](clang-semantic-ab-results.json): environment, exact
  commit pins, input hashes, every timing, graph hashes, full verification issues,
  clang reports and acceptance checks.
- [All 1,255 added edges](clang-semantic-ab-added-edges.jsonl): caller/target IDs,
  call-site provenance and both declaration locations, grouped by repository.
- [Captured output](clang-semantic-ab-output.txt).
- [Executed harness](clang-semantic-ab-harness.txt): executable Python retained as
  text so Spine does not ingest measurement code as product source.

At the recorded Spine commit and with its CI extras including clang installed,
prepare `.git`-less archives of OpenCV `b4c5ec4042f097e2a5b386b9d413ec7333d0a184`
and TinyXML-2 `8224e427b655b83dae5e2298f1e6919523a78737` at
`/tmp/spine-clang-ab-opencv` and `/tmp/spine-clang-ab-tinyxml2`, respectively. Then:

```sh
.venv/bin/python -u docs/evals/clang-semantic-ab-harness.txt all /tmp/spine-clang-ab-repeat
```

The output directory must be new; the harness creates one subdirectory per repo.
Run from the Spine checkout. Only trusted, locally generated graph snapshots are
loaded by the comparator. Raw snapshots and validation archives are temporary;
the committed receipts preserve the reproducible measurements and added edges.

**Step 1 is complete.** Structural safety and reproducibility passed on these
inputs. Grounded endpoints and invariant checks do not prove that a recovered
call points to the semantically correct target. That remains the independent
source-level correctness audit in step 2. The draft MR and release decision are
unchanged; no production code or D1–D6 decision changed for this experiment.


## Confidence step 2 — independent source audit and caller guard

The [200-edge source audit](clang-semantic-correctness-audit.md) found 31 incorrect
caller assignments and one ambiguous assignment in the original added-edge sample.
The enclosing-function guard now refuses all 32, retaining 157 reviewed-correct
edges and conservatively refusing 11 additional reviewed-correct edges. The
sample was fixed before the fix and was not refilled after removals. No new edge
outside the previous clang-on graph is introduced by the fix.

Post-fix extraction recovers **670/135,633 sites (0.4940%)** and adds **660 edges**
on OpenCV; TinyXML-2 recovers **415/1,379 (30.0943%)** and adds **409 edges**.
Nodes, header routing, pending sites, existing CST edges and verification issue
records are unchanged. The prior measurements above are historical; current
recovery is lower because unsafe or unsupported caller identities are refused.
The independent source audit does not establish population-wide precision.
Full evidence, acceptance checks, tradeoffs and regression receipts are in the
audit report. Release remains pending in draft MR #379.

## Confidence step 3 — diagnose misses and recover supported callers

The [recovery diagnosis](clang-semantic-recovery.md) records a fixed 100-site miss
sample and classifies all Step 2 caller rejections. Caller-only mapping now admits
file-static C++ functions, exact destructors and `operator()` while retaining the
identity and source-grounding guards. D1–D6 are unchanged.

Recovery rises from 670 to **696 / 135,633** sites in OpenCV and from 415 to
**416 / 1,379** in TinyXML-2. All **27 new edges** passed source review; all Step 2
edges remain. The original audit now retains 166 reviewed-correct edges and still
refuses every one of its 31 incorrect and one ambiguous relationships. The two
remaining correct losses are unsupported template callers.

The dominant miss buckets remain absent/unresolved clang expressions and missing
grounded identities. This is a bounded recovery improvement; release remains
pending. Current repeated timing, graph invariants and regression receipts are
in the recovery report and its linked machine-readable evidence.

Step 3 final local suite: `3769 passed, 4 skipped, 51 deselected, 182 warnings in 220.58s (0:03:40)`. Phase gates, zero-regression accuracy, all four shapes and self-verification (0 errors, 1 warning) passed.

## Confidence step 3b — measured

The [Step 3b report](clang-semantic-step3b.md) contains all 45 completed comparisons
on five pinned repositories, source audits, precision corrections and support
recommendations. OpenCV recovers 2,597/135,633 sites at 300.296 s median; the five
profiles show different benefits and costs. D1–D6 remain unchanged. The prior
Step 3 measurements above are historical. Required local gates passed; see the Step 3b receipt.

## Confidence step 4 — release readiness planned

The [Step 4 plan](../specs/parsing-and-the-pkg.md#step-4--release-readiness) defines
the support contract, review of known correct-edge losses and runtime cost,
final-candidate validation, and the maintainer decision. Status: **planned;
execution not started**. Step 3b supplies the evidence baseline; readiness and
merge/release approval are separate decisions. D1–D6 remain unchanged.
