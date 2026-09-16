# Clang Step 3b — include roots and representative validation

Status: complete; implementation, five-repository evaluation and local gates passed. This follows the
[Step 3b contract](../specs/parsing-and-the-pkg.md#step-3b--repository-include-roots-and-representative-validation).
The [manifest](clang-semantic-step3b-manifest.json) was committed as `c4401a4`
before production changes. Baseline implementation: `4950899`.

## Decision

Retain clang as **optional, profile-dependent enrichment**. The include-root
hypothesis is supported on OpenCV and GoogleTest, not across every C++ profile.
This is not evidence for broad semantic coverage or default enablement. D1–D6
remain intact; merging or releasing still requires the existing review process.

- **TinyXML-2:** useful existing contribution (416 sites, 410 graph edges), with
  0.300 s median total extraction and all 50 frozen supported labels retained.
  No extra include roots were needed; Step 3b adds no relationships here.
- **OpenCV:** 1,844 new relationships over the previous pass, with five known
  correct-edge losses and five removals of previously wrong identities. The
  optional pass contributes 2,520 relationships over CST alone. Its 300.296 s
  median total extraction is 241.914 s above the old pass and 270.795 s above
  clang-off. Use for batch analysis when that cost is acceptable; this does not
  support a low-latency or high-coverage claim. Native parsing alone takes about
  168 s after more headers become available; traversal optimization cannot remove
  that cost without another design change.
- **GoogleTest:** 54 added relationships, all source-reviewed correct at existing
  name-ID granularity, for 4.548 additional seconds versus B. Useful narrow
  contributions, but only 2/50 frozen supported labels are present; most calls
  remain unavailable under the fixed flags.
- **pugixml:** zero recovered sites and 0/6 labels, despite 1.703 s extraction.
  This evaluation does not justify installing clang for this profile alone.
- **fmt:** all 24 remaining semantic relationships belong to bundled GoogleTest;
  **zero fmt-owned semantic contribution**. Seven old wrong-scope relationships
  are correctly refused, but the candidate adds 3.612 s and no new edges. This
  does not justify the extra for fmt's formatting API.

The supported-case labels expose important limits: include roots alone do not
make even source-inspectable receiver families broadly recoverable. Missing
standard/generated headers, partial AST recovery and existing CST identity gaps
remain. Any next track should separately measure those causes before proposing
changes to D2 or D3; no SDK, compilation database or node repair was added here.

The next documented step is [Step 4 — release readiness](../specs/parsing-and-the-pkg.md#step-4--release-readiness):
support contract, explicit loss/cost dispositions, final-candidate checks and a
maintainer decision. Its scope is documented; execution has not started.

## Final five-repository measurements

All 45 fresh-process extractions completed in the frozen order. Each A/B/C
configuration produced identical graphs and reports across its three repeats.
The [complete measurements](clang-semantic-step3b-results.json) include graph hashes,
input hashes, full flags/directories, phase times and complete verification findings.

A = baseline clang off; B = baseline clang on; C = candidate clang on. Recovery
counts distinct pending byte ranges. Edge additions count distinct graph relationships.

| Repository | B recovered / pending | C recovered / pending | C recovery | Edges added / removed vs B | C edges over A |
|---|---:|---:|---:|---:|---:|
| fmt | 31 / 3,440 | 24 / 3,440 | 0.6977% | +0 / −7 | 24 |
| googletest | 0 / 5,599 | 54 / 5,599 | 0.9645% | +54 / −0 | 54 |
| opencv | 696 / 135,633 | 2,597 / 135,633 | 1.9147% | +1,844 / −10 | 2,520 |
| pugixml | 0 / 1,637 | 0 / 1,637 | 0.0000% | +0 / −0 | 0 |
| tinyxml2 | 416 / 1,379 | 416 / 1,379 | 30.1668% | +0 / −0 | 410 |

### Extraction time

Seconds: median [minimum–maximum], with hashing, verification and serialization
outside the timer. Input hashing pre-reads sources; these are fresh interpreters,
not guaranteed cold filesystem-cache measurements.

| Repository | A | B | C | C − B | C / B | C − A | C / A |
|---|---:|---:|---:|---:|---:|---:|---:|
| fmt | 0.826 [0.825–0.833] | 1.185 [1.178–1.190] | 4.797 [4.790–4.822] | +3.612 | 4.05× | +3.971 | 5.81× |
| googletest | 1.148 [1.137–1.153] | 1.736 [1.716–2.364] | 6.284 [6.256–6.304] | +4.548 | 3.62× | +5.136 | 5.47× |
| opencv | 29.501 [29.012–29.565] | 58.381 [58.167–60.171] | 300.296 [292.208–304.315] | +241.914 | 5.14× | +270.795 | 10.18× |
| pugixml | 0.236 [0.234–0.240] | 2.798 [2.797–2.849] | 1.703 [1.691–1.709] | -1.096 | 0.61× | +1.467 | 7.22× |
| tinyxml2 | 0.107 [0.106–0.112] | 0.335 [0.331–0.348] | 0.300 [0.297–0.310] | -0.035 | 0.89× | +0.193 | 2.81× |

### TU bounds, graph preservation and verification

Nodes, routed-header hashes, pending-site hashes and source-TU selection are equal
across A/B/C. All CST edges remain; all semantic additions join existing grounded
functions. Verification records are identical, including the pre-existing errors
below. No potential line-level retargets are hidden; they are listed in the JSON.

| Repository | Nodes | Parsed / total TUs (B = C) | Diagnostic TUs B → C | Failed TUs B → C | Existing verify errors / warnings | Added roots |
|---|---:|---:|---:|---:|---:|---:|
| fmt | 3,485 | 39 / 46 | 39 → 39 | 0 → 0 | 1 / 1 | 2 |
| googletest | 3,616 | 72 / 106 | 72 → 72 | 0 → 0 | 2 / 1 | 4 |
| opencv | 87,181 | 1,981 / 2,468 | 1,950 → 1,950 | 0 → 0 | 3 / 2 | 25 |
| pugixml | 382 | 55 / 69 | 54 → 54 | 0 → 0 | 2 / 0 | 0 |
| tinyxml2 | 425 | 3 / 3 | 3 → 3 | 0 → 0 | 1 / 1 | 0 |

### Frozen supported-case relationships

These labels are independent of candidate success, but intentionally cover a
bounded set of inspectable receiver families. They are not whole-repository recall.
The [observations](clang-semantic-step3b-observations.jsonl) retain every label.

| Repository | Labels | A present | B present | C present |
|---|---:|---:|---:|---:|
| fmt | 50 | 0 | 1 | 1 |
| googletest | 50 | 0 | 0 | 2 |
| opencv | 50 | 0 | 50 | 48 |
| pugixml | 6 | 0 | 0 | 0 |
| tinyxml2 | 50 | 0 | 50 | 50 |

### Remaining-site observations

Each unresolved site is assigned the furthest stage observed in any selected TU.
A stage is not a definitive diagnosis of missing headers or unsupported syntax.

| Repository | Stage | Sites |
|---|---|---:|
| fmt | `caller_identity_mismatch` | 164 |
| fmt | `indirect_or_unsupported_target` | 824 |
| fmt | `no_matching_call` | 2,394 |
| fmt | `ungrounded_target` | 24 |
| fmt | `unsupported_usr` | 10 |
| googletest | `caller_identity_mismatch` | 131 |
| googletest | `indirect_or_unsupported_target` | 1,261 |
| googletest | `no_matching_call` | 4,086 |
| googletest | `ungrounded_target` | 39 |
| googletest | `unsupported_usr` | 28 |
| opencv | `caller_identity_mismatch` | 6,921 |
| opencv | `indirect_or_unsupported_target` | 19,912 |
| opencv | `no_matching_call` | 101,140 |
| opencv | `ungrounded_caller` | 5 |
| opencv | `ungrounded_target` | 4,427 |
| opencv | `unsupported_usr` | 631 |
| pugixml | `caller_identity_mismatch` | 352 |
| pugixml | `indirect_or_unsupported_target` | 31 |
| pugixml | `no_matching_call` | 1,216 |
| pugixml | `ungrounded_target` | 38 |
| tinyxml2 | `caller_identity_mismatch` | 30 |
| tinyxml2 | `indirect_or_unsupported_target` | 168 |
| tinyxml2 | `no_matching_call` | 413 |
| tinyxml2 | `ungrounded_target` | 338 |
| tinyxml2 | `unsupported_usr` | 14 |

## Algorithm frozen before broad evaluation

Read literal includes from the installed C/C++ CST grammar and match complete
include suffixes to admitted repository headers. Propose only uniquely matching
roots. Append sorted repository-relative roots after existing directories; keep
quoted-local and existing search-path resolutions. Check all proposed roots
against every previously missing literal and reject every provider of a conflicting
or unadmitted resolution. Custom exclusions, hidden/ignored directories, nested
checkouts and escaping/aliasing symlinks cannot supply inference inputs. Computed
includes do not infer roots. This is conservative about textual literals, including
inactive branches; it does not model arbitrary macro-expanded include spellings.
Existing baseline search behavior is unchanged.

All four prefixed-include fixtures (quoted/angle, direct/transitive) failed before
the implementation and pass afterward. Other regressions cover ambiguity,
interacting roots, local precedence, excluded inputs and checkout determinism.
Node construction, header routing and TU selection remain unchanged. The source
audit required a mapper restriction, documented below, to refuse local lambdas.

## Supported-call labels frozen before candidate answers

[Gold labels](clang-semantic-step3b-supported.jsonl) contain 206 source-labelled
call sites: 50 OpenCV, 50 TinyXML-2, 6 pugixml, 50 fmt and 50 GoogleTest. This is a
bounded supported-case diagnostic, **not repository-wide recall or precision**.
The label identities and selection rules were frozen before any C extraction.

- OpenCV/TinyXML-2: reuse previously source-reviewed correct, retained Step 2
  labels, sorted by SHA-256 of `step3b-supported-v1:` plus audit ID; take 50 each.
  These are deliberately known-positive regression samples and favor previously
  recoverable shapes. The complete 200-edge old audit is separately retained.
- New repositories: enumerate pending member expressions with existing grounded
  C++ caller/target names, matching caller file and no template/operator/TEST
  name shape. Rank by SHA-256 of `step3b-supported-v1:` plus repository name and
  `(file, start byte, end byte)` representation. See the
  [candidate enumerator](clang-semantic-step3b-label-candidates.txt) and
  [source-family selector](clang-semantic-step3b-select-gold.txt).
- pugixml: inspect all 12 candidates. Six concrete local writer calls qualify;
  the other six use dependent receivers or lack the correct grounded target.
  The 44-call shortfall is explicit. This is exhaustive within the declared
  candidate population, not an assertion that no other eligible expression shape
  exists in the repository. Most of its implementation lacks usable existing
  function identities under the current CST macro handling.
- fmt/GoogleTest: select the first 50 source-confirmed calls in concrete UnitTest,
  UnitTestImpl and TestResult receiver families: typed callback parameter, factory
  return, implementation pointer and chained result pointer. Exclude the known
  Windows-only caller and function-template InitGoogleTestImpl. Inspect caller
  declarations, receiver return types and target declarations before freezing.
  GoogleTest includes header and source sites; fmt's sampled implementation is
  amalgamated. IDs collapse overloads, so labels are at the existing name-ID level.
- All 50 fmt labels belong to **bundled GoogleTest**, not fmt's formatting API.
  fmt's macro-defined namespaces and dependent template receivers limit eligible
  grounded identities. This overlap limits the independence and breadth of the
  five-repository evaluation; report fmt-owned recovery separately.

Selection favors inspectable supported shapes and does not cover all possible
receivers. It is independent of candidate success. Do not refill misses after
measurement. Source and header sites count in the original pending denominator;
header sites require reachable selected TUs to be recovered by production clang.

## Reproduction

Install the frozen environment and materialize gitless snapshots at the manifest
roots from the recorded repository commits. Run
`python docs/evals/clang-semantic-step3b-run.txt` from the Spine checkout with the
C/C++ and clang extras installed. The runner verifies input manifests, starts a
fresh interpreter for each A/B/C extraction, and keeps the order
`A B C C B A B A C` per repository. A/B load the baseline semantic module from
`git show 4950899:src/orchestrator/pkg/clang_link.py`; the adapter drops the new
admission keyword, with the rest of the extractor pipeline held constant.
This measures the complete candidate, including its documented precision and
performance fixes, without changing source inputs.

Hashing, snapshot serialization and verification are outside extraction timing;
input hashing pre-reads each repository. No tests or parallel benchmarks run
during timing. Run `python docs/evals/clang-semantic-step3b-compare.txt` afterward.
Assertions cover identical nodes, header routing and pending inputs, fixed TU
selection, CST edge preservation, grounded additions, complete verification
findings and repeatable reports/graphs. Existing source manifests and frozen
implementation/label hashes are checked throughout.

Graph evidence records a relationship by caller, target, file and line. The
supported-label results therefore report **relationship presence at name-ID and
line granularity**, not proof that every byte-distinct occurrence was recovered.
Production report totals independently count distinct full-range pending sites.

## Review scope

The documentation matrix requires changelog and changed behavior/spec links;
no CLI, MCP, extra, vocabulary, frontend or codegen registration changed in this
follow-up. The semantic-pass checklist applies: wiring, optional grammar imports,
cache invalidation (all `pkg/*.py` bytes are already hashed), deterministic flags,
TU bounds, corpus additivity and real-repository comparisons. Frontend census and
registration/codegen rows do not apply to this include-input change. D1–D6 stand.

## Exploratory performance correction

The first OpenCV candidate extraction was stopped after more than five minutes;
it produced no completed candidate graph or recovery result. Its baseline runs
were 30.339 s (off) and 58.956 s (on). These pilot timings are not pooled with the
final fixed-order comparison. The gold labels and repository selection were not
changed or refilled.

Separate untimed cProfile diagnostics measured root inference at 19.68 s with 25
roots. A partial clang profile through 520 TUs recorded 283,162 `_repo_file`
invocations taking 31.43 s cumulatively, versus 11.47 s in native parsing.
Repeated header traversal was repeating filesystem path canonicalization. The
candidate now memoizes canonical path mappings **within each extraction**, and
only retrieves call ranges for files in that TU's wanted-site set. Traversal,
caller/target checks, flags and pending/TU inputs remain unchanged. A regression
with 100 irrelevant header calls bounds repeated path-resolution work while
checking that the wanted call is still recovered. No persistent filesystem cache
was added.

This is a performance-only extension to 3b.3, recorded before restarting all 45
measurements. The harness now records include-root, native-parse and total-semantic
phase times as well as whole-extraction time. These few timer calls are inside
the measured extraction; profiling and per-TU progress logging are absent from
the final comparison.

The complete traversal with path caching finished OpenCV in **498.567 s**:
2,609/135,633 sites, 1,852 additions and six removals over B. Native parsing alone
cost 167.918 s; root inference cost 12.585 s. Its saved graph/report is the
reference for the next optimization, not a final repeat. All old negative audit
cases remained absent. The controller paused between runs for inspection; this
series was then retired, preserving its evidence separately.

A second performance-only adjustment skips function subtrees whose files cannot
contain any wanted site. It takes reverse reachability over **that parsed TU's
actual clang include relationships**, including computed includes. This does not
change the CST include graph used to select TUs or pending sites. Functions in
wanted files, their possible including files and unknown/outside files remain
traversable; unknown paths conservatively merge rather than disappearing.
Namespaces/classes continue to be traversed. A nested local-class fixture uses a
computed include inside a wrapper function and checks that its wanted header
site is still visited and refused for caller identity, not silently skipped.
The final comparison checks the saved complete-traversal graph, accounting for
the precision corrections described below and reviewing every further difference.

## Source audit and required precision correction

The fixed stratified sample contains 200 of the 1,852 pre-fix additions, plus
14 separately selected header/macro/special-caller checks. The full population,
strata, hashes and allocation are in the
[audit population](clang-semantic-step3b-audit-population.jsonl) and
[selection receipt](clang-semantic-step3b-audit-selection.json).
The [per-edge source audit](clang-semantic-step3b-audit.jsonl) retains failed
examples; it is never refilled after a fix.

Strata use source/header location, free/scoped/special caller, receiver expression
shape and an uppercase-call marker in the surrounding source. That macro marker
is a sampling heuristic, not proof of preprocessor expansion. Template identities
remain refused. The same selection rule for the other repositories is executable
in [the addition selector](clang-semantic-step3b-select-additions.txt).

Two of the 200 sampled additions were wrong: `run_lo(...)` and `swapF(...)` are
local lambda calls, but their USRs were incorrectly projected to the enclosing
function, creating false self-calls. The signature parser had treated a new
`@...` declaration scope after the first `#` as an opaque parameter. The verified
shape `c:@S@Runner@F@run#@Sa@F@operator()#1` demonstrated the bug. Six regression
cases failed before the fix: three pure USRs, two local-callable self-calls and
a local-class body borrowing its enclosing caller. The candidate now rejects
these signature segments and checks actual declaration-parent names for both
caller and target. Linkage blocks remain transparent; local/function/lambda
contexts cannot masquerade as namespace/record scopes. Qualified parameter types
such as `*$@N@api@S@Arg` remain supported. No IDs or grounded nodes are repaired.
This tightens P1 within D1–D6; it does not broaden accepted identities.

The targeted follow-up checks **all 20 semantic self-edges** in the complete
OpenCV candidate, not just the sampled two. Eight are genuine static virtual,
recursive or overload calls. Twelve are lambda mistakes: eight new additions and
four existing baseline edges outside the earlier 200-edge audit. The latter use
`removeDependentPoints` and `update_generator`. Seventeen checks are additional
to the initial sample/supplement. Preserve the genuine self-edges; a blanket
self-edge ban would be wrong. The final comparison must demonstrate refusal of
all twelve mistaken relationships. The fixed new-edge sample initially scored
198 correct / 2 incorrect; this is not a population precision estimate.

The six original removals were also source-reviewed:

- Two correct getter edges (old audit **O023/O024**) are lost because `MAX` now
  expands. Clang reports two zero-width getter ranges at each macro's start,
  rather than the CST's original getter range. Exact-range matching refuses them.
- Three correct source calls (`setConvolution`, `setCrop`, `compute`) have no
  matching CALL_EXPR in the partial AST with the new includes. Their deeper
  parse-recovery cause is not isolated. These remain explicit recovery losses;
  no build headers, SDKs or broad range matching were invented to hide them.
- The old `readTorchBlob → TorchImporter::readObject` edge omits the namespace
  introduced by `CV__DNN_INLINE_NS_BEGIN`. With the repository version header,
  actual caller and target contain `dnn4_v20251223`. Refusal is a precision
  correction, not a correctly identified edge to preserve.

The [B](clang-semantic-step3b-loss-B.json) and
[C](clang-semantic-step3b-loss-C.json) six-site diagnostics use four TUs outside
timing; [cursor evidence](clang-semantic-step3b-loss-cursors.jsonl) records the
macro extents and versioned namespace. The saved complete-traversal graph must
be preserved except for demonstrated precision corrections; every further
changed edge requires review. Pruning fixtures preserve nested computed includes.
The interrupted series is excluded and all final timings restart after this fix.

## Final source-review outcome

All 54 GoogleTest additions were reviewed, including header sites, chained
receivers, exact destructor callers and the genuine recursive FilePath self-call.
Caller declarations, explicit receiver types and target declarations agree. The
[other-repository audit](clang-semantic-step3b-other-audit.jsonl),
[population](clang-semantic-step3b-other-population.jsonl) and
[selection receipt](clang-semantic-step3b-other-selection.json) retain the evidence.
TinyXML-2 and pugixml have no changed semantic relationships to audit.

All seven fmt removals omit `fmt::v11` from the old caller and target IDs.
`test/scan.h:15` enters `FMT_BEGIN_NAMESPACE`; `include/fmt/base.h:253–255`
defines the missing scopes. The [native cursor evidence](clang-semantic-step3b-fmt-cursors.json)
confirms them at all seven removed sites; [its script](clang-semantic-step3b-fmt-cursors.txt)
uses the measured candidate flags outside timing. These are precision corrections.

The final OpenCV graph is exactly the saved complete-traversal graph after
removing the twelve reviewed lambda mistakes: **zero other edge differences**.
The [reference receipt](clang-semantic-step3b-traversal-reference.json) records
both the pre-fix graph hash and the expected post-refusal graph hash. The
[audit checker](clang-semantic-step3b-check-audit.txt) reproduces the fixed sample,
checks that graph hash and verifies every reviewed relationship's final presence.
The OpenCV audit ends with 219 retained correct relationships, 13 refused wrong
relationships and five known correct losses. The 219 includes one unchanged
baseline self-edge from the targeted check; it is not a count of all new edges.
The original fixed 200-addition sample remains **198 retained / 2 refused**.
All 14 initial supplemental checks remain correct. No failed example was refilled.

Across the old fixed Step 2 sample, **164 of the previous 166 correct relationships
remain** (81 OpenCV, 83 TinyXML-2); O023/O024 are the documented MAX-range losses.
All 31 previously incorrect and one ambiguous relationship remain absent.
All 27 Step 3 additions remain. There are **no line-level retarget candidates**
in any repository. These reviews are neither a blinded independent second review
nor a population-wide precision estimate.

## Validation receipts

- Four prefixed include fixtures failed before implementation:
  [red output](clang-semantic-step3b-includes-red.txt).
- Six local-scope regression cases failed before the precision fix:
  [red output](clang-semantic-step3b-local-red.txt).
- Focused semantic suite: **94 passed**, including ambiguity, repository boundaries,
  optional grammar behavior, checkout determinism and nested computed includes:
  [focused output](clang-semantic-step3b-focused.txt).
- All 45 measured runs and all comparison assertions passed. Production code,
  gold labels and harness hashes stayed frozen throughout those runs.
- Full pytest: **3798 passed, 4 skipped, 51 deselected, 182 warnings in 211.78s (0:03:31)**. Workspace files were frozen throughout the run.
- mypy, ruff, generated-artifact, MCP inventory and roadmap checks passed.
  Accuracy: zero gated regressions. All four repository shapes pass.
  Self-verification: zero errors, one existing warning.
  [Local gate receipt](clang-semantic-step3b-gates.txt).
- [45-run output](clang-semantic-step3b-runs-output.txt) and
  [comparison assertions](clang-semantic-step3b-compare-output.txt).
  Post-commit documentation audit and remote CI receipts are recorded on draft MR #379.
