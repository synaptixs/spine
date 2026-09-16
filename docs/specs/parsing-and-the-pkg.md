# How Spine parses code, and what lands in the PKG

**Audience:** engineering · **Written 2026-08-16 against 3.18.1 · §3 and §6 updated 2026-08-24**
**Read alongside:** [`knowledge-graph-architecture.md`](knowledge-graph-architecture.md) (the
graph itself), [`../../KNOWLEDGE_GRAPH.md`](../../KNOWLEDGE_GRAPH.md) (user-facing).

**The one-sentence version:** every fact in the PKG comes from a real parser — CPython's own
`ast`, a tree-sitter grammar, or `sqlglot` — walked as a typed tree and recorded with
`file:line` provenance. Nothing is inferred from a filename, a naming convention, or a regular
expression.

---

## 1. AST vs CST, and why Spine uses both

The distinction matters because it determines what the front-end can *see*, and where each one
breaks.

**An AST — Abstract Syntax Tree** — is what a compiler keeps after it has thrown away
everything that does not affect meaning. Parentheses, whitespace, comments and the exact token
sequence are gone; what remains is structure. Python's `ast` module is the canonical example:
`a + (b)` and `a+b` produce an identical tree.

**A CST — Concrete Syntax Tree** — keeps everything, including every token and its byte range.
You can reconstruct the source character-for-character from it. tree-sitter produces one.

Spine uses whichever is the *authoritative* parser for the language:

| Front-end | Parser | Tree | Why this one |
|---|---|---|---|
| `python` | CPython `ast` (stdlib) | AST | It is the same parser that *runs* the code. If Python can execute a file, we read it identically — there is no second implementation to disagree with. |
| `java` `csharp` `c` `cpp` `go` `typescript` | tree-sitter + that language's official grammar | CST | No stdlib parser is available to us. tree-sitter grammars are maintained by the language communities, are fast, and are **error-tolerant** — see §4. |
| `sql` | `sqlglot` | AST | Pure-Python, dialect-aware (postgres / mysql / tsql / oracle …), and understands SQL as a language rather than as text. |

**Why not one parser for everything?** Because a second implementation of a language is a
second opinion about what the language means, and the two will diverge on the hard cases. Using
CPython's own parser for Python removes that risk entirely for our largest front-end.

---

## 2. What actually happens to a file

```
repo → walk → per-suffix dispatch → parse → typed tree walk → FactBatch → merge → (post-passes)
```

**Dispatch is by suffix** (`LanguageExtractor.suffixes`, `.py` → `PythonExtractor`, and so on),
so adding a language is a new front-end plus a suffix registration — nothing else changes.
Directories starting with `.` are skipped by both walkers, which is why the accuracy corpus
lives under `corpus/*/.repo/` and why that leading dot is load-bearing.

**Python.** `ast.parse()`, then a walk over the node types that carry the facts we record:

| `ast` node | Becomes |
|---|---|
| `Module` | a `Module` node |
| `ClassDef` | a `Type` node, plus `CONTAINS` from its module, plus `IMPLEMENTS` per base |
| `FunctionDef` / `AsyncFunctionDef` | a `Function` node, plus `CONTAINS` from module or class |
| `AnnAssign` / `Assign` | a `Field` node (annotated attributes and class-level assignments) |
| `Import` / `ImportFrom` | an `IMPORTS` edge |
| `Call` | a `CALLS` edge — **if the callee resolves**, see §3 |
| `If` / `For` / `While` / `Try` / `With` / `AsyncWith` | walked *through*, so a function defined or called inside a conditional is not missed |

That last row is easy to overlook and is the difference between reading a file and reading the
top level of a file.

**tree-sitter front-ends** do the same job against named CST node types. Go, for example, keys
on `function_declaration`, `method_declaration`, `type_declaration`, `field_declaration`,
`import_declaration`, `call_expression`, `selector_expression`. The node names come from the
grammar, so they are the language's own vocabulary rather than ours.

**SQL** parses with `sqlglot` into expression trees; the dialect is auto-detected from
distinctive syntax and can be overridden with `--dialect`.

---

## 3. Resolution — the only place a front-end may be wrong

Parsing is not where accuracy is lost. **Every node kind and every edge kind except `CALLS`
scores 1.00 precision and 1.00 recall on the corpus, in all 10 languages.** Structure is
either in the tree or it is not.

> **Read that sentence as the conditional it is: 1.00 *on the corpus*.** It held at 1.00 for
> four front-ends that were fabricating `CALLS` edges, for the whole time they were, because no
> fixture carried the shape — see the shadowed-callee section below. A corpus score bounds what
> the corpus contains and nothing else.

`CALLS` is different because it needs *resolution*: the tree tells you a call happened and what
it was spelled, not which definition it reaches. That is a judgement, and judgements can be
wrong.

**The rule is: skip rather than guess.**

```python
# extractor.py, _resolve_call
if func.id in imports:   return imports[func.id]
if func.id in names:     return names[func.id]
return None              # was: f"py:{func.id}"
```

That one-line change shipped in 3.18.0. The previous version invented an id for any unresolved
bare name — every parameter, local and nested function — which produced **497 fabricated edges
on this repo alone, 3.16% of the call graph**. Measured on external repositories the rate was
higher still: **14.8% of unique call relationships in Flask, 7.4% in httpx.**

Two properties of that bug are worth carrying:

- **It was self-consistent.** The inventor created the phantom *node* as well as the edge, so
  `pkg verify`'s dangling-edge check reported **0** the whole time. A graph can be internally
  perfect and externally false.
- **It corrupted real nodes.** The invented id collided with legitimate ungrounded `Type`
  nodes, so `py:Exception` — a `Type` named `Exception` — became a `Function` named literally
  `"py:Exception"`.

C could not take the same fix. An unresolved callee in C is usually a function declared in a
header and linked from another translation unit, so skipping those would silence every
cross-TU call in every C repository. The C test is therefore *"did this function bind the
name"* (a function-pointer parameter) rather than *"can we resolve it"*.

### The same class in four more front-ends — found 2026-08-24

**The story above was told as one Python bug, closed. It was one instance of a class, and the
other four front-ends had it the whole time.** When a parameter or local **shadows** a
resolvable name, TypeScript, Go, C++ and C# each resolved it anyway:

```ts
export function send(x: string): void {}
export function outer(send: (v: string) => void): void { send("hi"); }
// emitted: ts:a.outer -CALLS-> ts:a.send   — `outer` calls its own parameter
```

It hid better than the Python bug did, and for the opposite reason. Python's inventor
manufactured an id *and a phantom node*; these four resolve against their own file-level tables
and land on a **node that genuinely exists**. So there was no dangling edge for `pkg verify` to
report, no phantom node to notice, and no corpus case to fail — three checks agreeing on a case
none of them examined.

Found by widening the invention oracle past Python (`pkg/scope.py` walks five tree-sitter
front-ends), measured at **47 fabricated edges across 23,746 bare calls** on 11 pinned public
repositories, then fixed by giving each front-end **C's test**, which is why it is worth having
written down here: *did this function bind the name*, never *can we resolve it*. **47 edges
removed for 47 fabrications, with no true edge lost.**

Two things the port needed that the C version does not spell out:

- **A binding is not in scope on its own line, in three of the five languages.** Go's spec
  starts a short variable declaration's scope at the *end* of the statement, so
  `cmd := cmd(binaryPath, …)` calls the package-level `cmd` — idiomatic, and it occurs 5 times
  in grpc-go alone. C and C++ read the same way. Each helper therefore records the first line a
  name is in scope, not just the name.
- **Only a bare-identifier call can be shadowed.** `this.Handle()` in C# and `this.method()` in
  TypeScript are explicit member accesses that name the member whatever else is in scope, so
  the bare form is tracked separately. Skipping both would have dropped real edges.

The guard is a corpus case per front-end (`corpus/*/shadowed_calls`), each **written and scored
before the fix** and each failing at `CALLS` precision 0.50 — a fixture written afterwards only
proves the fix is self-consistent. Full record:
[`invention-oracle-cross-language.md`](invention-oracle-cross-language.md).

---

## 4. Failure modes, by design

**A file that will not parse is skipped, never guessed at.** `SyntaxError`,
`UnicodeDecodeError` and `ValueError` are caught per file and the path is recorded in
`skipped`. One unparseable file costs you that file, not the run.

**tree-sitter recovers; `ast` does not.** A CST parser produces a tree with `ERROR` nodes
around the damaged region and keeps going, so a file with one bad line still yields facts for
the rest. CPython's `ast` raises on the first syntax error and the file is skipped whole. That
asymmetry is a real difference in behaviour between our Python front-end and the other six, and
it favours the tree-sitter side on messy real-world code.

**C/C++ parse pre-preprocessor.** We never run `cpp`, so heavy macro use yields partial facts.
A macro-generated function does not exist as far as the tree is concerned.

---

## 5. What lands in the graph

Eight node kinds and eleven edge kinds — a deliberately small vocabulary that every front-end
maps onto, so a query works the same way across languages.

**Nodes:** `Module` · `Type` · `Function` · `Field` · `Endpoint` · `Entity` · `Doc` · `Intent`

**Edges:** `IMPORTS` · `CONTAINS` · `CALLS` · `IMPLEMENTS` · `READS` · `WRITES` · `EXPOSES` ·
`CONSUMES` · `REFERENCES` · `MENTIONS` · `SERVES`

Two of those are not artefacts you can point at in a file. `Doc`/`MENTIONS` come from the
documentation tier, and `Intent`/`SERVES` from git history — the ticket a symbol was last
changed for. `Intent` is the only node kind that is a *reason* rather than a thing, which is
why it carries no provenance.

### Every fact is a `Node` or an `Edge`, and both carry provenance

```python
@dataclass(frozen=True)
class Node:
    id: str                      # "py:pkg.mod.Cls" — language-prefixed, stable
    kind: NodeKind
    name: str
    language: str = ""
    provenance: Provenance | None = None    # file + line + end_line
    external: bool = False

    @property
    def grounded(self) -> bool:
        return self.provenance is not None and not self.external
```

**`grounded` is the load-bearing property.** A node is grounded when we can point at the line
that produced it *and* it is not an external reference. `stdlib` and third-party symbols appear
as ungrounded nodes so edges have somewhere to land, and they are excluded from every count
that claims to describe your code.

**Ids are language-prefixed and stable** — `py:orchestrator.pkg.stats.GraphStats`,
`go:svc.Handler.Run`, `c:uv__stream_eof`. Note that C/C++ ids are *symbols*, not locations, so
grouping by id makes every function its own component; group by the owning module by walking
`CONTAINS` upward instead.

### Post-passes

Some facts cannot be derived from one file. A front-end may expose `finalize` for a whole-repo
pass — Go's `IMPLEMENTS`, computed by matching a concrete type's method set (name + arity,
value **and** pointer receivers) against every in-repo interface, is the clearest example.
Doc-linking (`Doc` + `MENTIONS`) and the optional intent scan run as post-passes for the same
reason.

---

### Optional clang semantic pass

C/C++ nodes still come from tree-sitter. After the CST front-ends finalize their
facts, `pkg/clang_link.py` can add `CALLS` between already-grounded functions.
It runs before `link_imports`; `FRONT_ENDS` and graph IDs remain unchanged.
Headers ending in `.h` reached by literal C++ includes are routed to the C++ CST,
independently of whether clang is installed.

The `[clang]` extra supplies a bundled libclang library; `[all]` includes it and
`[languages]` does not. The cache fingerprint includes availability and wheel
version. Flags are synthesized from repository header directories, a fixed target
and C11/C++17 modes, with system includes disabled. Compilation databases and host
SDKs are never consulted.

The CST side channel identifies pending calls by file and full byte range. Only
source TUs with pending sites in their reachable headers/source are parsed. Clang
must resolve an eligible declaration in an admitted repository file, whose USR
maps to an existing grounded function ID. The enclosing clang function must also
map to the CST caller ID. Its source file must agree with the grounded caller,
or the grounded overload must lie within its class declaration in a header.
This refuses scope-stripped callers, macro test bodies, destructor/constructor
collisions and unrelated program entrypoints without changing nodes or IDs.
Caller mapping also accepts file-static C++ functions, exact destructor names
and `operator()` when the same identity and grounding checks succeed. Unsupported
caller shapes remain refused. Ordinary parameter and method qualifiers collapse
to the existing name-based identity. Template/local/anonymous declaration identities
and operator targets remain refused. Virtual calls use the static declaration;
conflicting candidates are refused. The pass never changes the node set.

The report states recovered sites, total pending sites, parsed/total TUs,
diagnostic/failed TUs, and a partition of unresolved sites by the furthest stage
observed. These are coverage observations, not proof of complete resolution or
attribution of every miss to missing headers. See the
[Step 3b evaluation](../evals/clang-semantic-step3b.md) for the expanded five-repository
comparison and the [validation record](../evals/clang-semantic-validation.md) for
historical results. Literal include suffixes can supply additional roots from
admitted repository headers. Existing search precedence is retained; conflicting
new resolutions are refused. Caller and target projections must also agree with
clang's actual namespace/record parents; a local lambda or class cannot borrow
its enclosing function's ID through a USR parameter suffix. Path canonicalization
is cached per extraction to avoid repeating filesystem work for header cursors
in many translation units. Within each parsed TU, functions outside the wanted
files and their actual clang include ancestors are skipped; unknown paths remain
conservative. This changes traversal cost, not the source-TU selection.

#### Step 3b — repository include roots and representative validation

**Status: complete; implementation, evaluation and local gates passed.** Documented 2026-09-15 at the
user's request. This is a follow-up to confidence Step 3, not a replacement for
P0–P6. See the [completed measurements and source audit](../evals/clang-semantic-step3b.md). The implementation
baseline is `4950899`; [Step 3 evidence](../evals/clang-semantic-recovery.md) remains
historical. Execution authorized by the user; the evaluation manifest is frozen before candidate implementation.

**Objective:** determine whether better repository-local include resolution adds
correct, useful semantic relationships at an acceptable cost, and describe which
repository profiles benefit from the optional extra.

**Hypothesis:** the current flags add directories containing headers, which can
miss the root required by a prefixed include. For example, an admitted header at
`modules/core/include/opencv2/core.hpp` and an include of `<opencv2/core.hpp>`
require `-I modules/core/include`. Adding `modules/core/include/opencv2` alone
does not supply that root. This is a testable gap in synthesized flags; it does
not establish the cause of every unresolved expression. Missing standard or
generated headers may continue to limit recovery after this change.

##### Boundaries — preserve D1–D6

- **D1:** clang remains an optional post-pass beside import linking; CST suffix
  ownership and frontend registration stay unchanged.
- **D2:** derive candidate include roots only from admitted repository files and
  literal includes. Keep fixed target/language modes, wheel-bundled libclang and
  disabled system includes. Do not read compilation databases, use host SDKs,
  infer build-specific defines, or synthesize missing headers/types.
- **D3:** add only edges between existing grounded functions. Preserve nodes,
  IDs, kinds, caller identity/source checks and target validation. CST macro/scope
  repair, new identities and broader USR support are outside this follow-up.
- **D4:** preserve existing `.h` routing; improve only the semantic pass's inputs.
- **D5:** keep `[clang]` in `[all]` and outside `[languages]`.
- **D6:** retain the existing selection of source translation units with reachable
  pending sites. Diagnostic header-only parses do not count as recovered sites or
  production TUs. Report distinct sites and TUs honestly.

##### Work sequence and deliverables

| Work item | Planned action | Completion evidence |
|---|---|---|
| **3b.1 — Freeze evaluation inputs** | Record the baseline implementation hash, environment and repository pins. Retain OpenCV and TinyXML-2; select three additional repositories covering self-contained C++, template/STL-heavy code and macro/generated-header-heavy code. Record names, commit hashes, selection reasons and input manifests before observing candidate results. | Committed evaluation manifest; all five repositories selected before tuning. Selection and pins are recorded in [the manifest](../evals/clang-semantic-step3b-manifest.json). |
| **3b.2 — Prove the include-root gap** | Add minimal failing fixtures under pytest temporary directories or `.repo/`. Cover prefixed angle/quoted includes, transitive includes and headers whose parent directory is insufficient. Record the current failure before changing production flags. | Four prefixed-include cases failed before the change; all pass afterward (`test_prefixed_include_root_recovers_grounded_call`). |
| **3b.3 — Implement bounded root synthesis** | Derive roots from exact literal include suffixes matched to admitted repository header paths. Define deterministic precedence and ambiguity handling; verify the complete search-path list cannot silently shadow another header. Preserve existing successful relative-include behavior and repository boundaries. | `clang_includes.py`; 94 focused tests pass. Algorithm, performance adjustments and source-audit-driven P1 restriction recorded in the evaluation report. |
| **3b.4 — Measure contribution and cost** | Run the three configurations below on each pinned repository, in fresh processes, with three runs per configuration and a recorded interleaved order. Keep fixtures, environment and source inputs fixed; separate diagnostic collection from timing. | All 45 runs complete; `clang-semantic-step3b-results.json` records flags, timings, graph hashes and passing preservation/repeatability assertions. |
| **3b.5 — Review correctness** | Recheck the fixed Step 2 audit and Step 3 additions; inspect every removed/retargeted semantic edge. Review new additions from source using the bounded audit rule below. | Fixed 200 OpenCV additions: 198 retained, 2 wrong lambda calls fixed/refused; all 54 GoogleTest additions correct. All removals reviewed; five known correct OpenCV losses disclosed. Old negative cases remain absent; all 27 Step 3 additions retained. |
| **3b.6 — Define supported use cases** | Complete applicable repository gates and summarize results by repository profile. State limitations and the cost/benefit of opt-in support separately from any future default-enablement proposal. | Evaluation report recommends profile-dependent optional use; no general coverage claim. Required local gates passed; MR #379 remains draft. |

Required regression coverage for 3b.3:

- Correct nested include roots and transitive resolution; existing local includes
  retain precedence.
- Duplicate basenames, duplicate full include suffixes and interacting added
  roots cannot select an arbitrary declaration. Alphabetical order alone is not
  evidence that a header is correct; unresolved ambiguity must retain a refusal.
- Ignored files, nested checkouts and symlinks escaping the repository cannot
  supply inferred roots or grounded targets.
- Different checkout locations and file enumeration order yield identical
  repository-relative roots and graph facts.
- Existing caller guards, grounded-target checks, corpus additivity and bounded
  TU selection remain intact.

##### Measurement and audit contract

Compare **A: baseline clang off**, **B: baseline clang on** and **C: candidate
clang on**. B versus A measures the existing contribution; C versus B isolates
the complete candidate, including documented performance fixes and the stricter
refusal of local declaration scopes discovered during the source audit. These
fixes preserve D1–D6 and do not broaden P1's accepted identities. Rerun these current comparisons, not the original
roadmap's already-recorded probes or baseline. Use the existing A/B harness as
the starting point and record the extension for the third configuration.

For each repository report:

- Recovered sites / the **unchanged original pending denominator**, plus the
  unresolved-stage partition. Preserve casts and inactive sites in the historical
  denominator for comparability; explain them separately rather than improving
  the percentage by filtering them away.
- Added, removed and retargeted CALLS edges; node equality; grounded endpoints;
  parsed/total TUs; diagnostic/failed TUs; complete verification findings.
- Median, minimum and maximum extraction times, absolute added seconds and time
  ratios. A large ratio on a subsecond repository has a different practical cost
  from tens of additional seconds on a large repository.
- A preselected, source-labelled sample of 50 supported calls per repository
  (all eligible calls if fewer), spread across available source/header and
  receiver shapes. Record expected caller and target identities before inspecting
  candidate answers, with sample shortfalls and selection rules stated. Use it to
  measure supported-case correctness/coverage; never define eligibility by
  whether clang happened to resolve the call.

Retain the Step 2 fixed 200-edge sample and all 27 Step 3 increment verdicts.
All 31 incorrect and one ambiguous Step 2 relationship must remain absent.
Account explicitly for any loss among the 166 retained correct sample edges or
27 Step 3 additions; an explained source-level correction is preferable to
preserving a demonstrated error. D3 requires preservation of the CST baseline
edges; it does not require retaining every previous semantic addition blindly.

Audit every new edge when a repository has at most 200 additions. Above that,
select 200 by a recorded fixed hash within source/header, macro/template and
receiver-shape strata; supplement with newly affected high-risk shapes and report
those checks separately. Record the full population, selection rule, reviewed
count and correct/incorrect/ambiguous verdicts. Do not refill a failed sample or
claim population-wide precision from a sample. Any discovered incorrect or
ambiguous addition requires a fix/refusal and re-evaluation before acceptance.

##### Exit criteria and decision

Step 3b implementation is complete when:

1. The demonstrated include-root fixture recovers the expected call, and the
   ambiguity, boundary and determinism regressions pass.
2. All five preselected repository comparisons finish with reproducible graph
   and report results, identical nodes/routing/pending inputs, preserved CST
   edges and grounded semantic additions. Verification changes and every lost
   semantic edge are explained; introduced defects are fixed.
3. The existing negative audit cases remain refused and the new-edge audit has
   no unresolved incorrect or ambiguous reviewed additions.
4. Required phase checks, focused tests, full pytest, accuracy gate, repository
   shapes, self-verification and documentation review pass. Keep workspace files
   frozen during full pytest; exclude `episteme/` and the working root roadmap
   from commits; use the existing draft MR #379.
5. The report states where opt-in clang provides useful correct relationships,
   its runtime cost and its remaining limits, including profiles with little or
   no benefit. Keep implementation completion separate from release approval.

There is no universal recovery-percentage threshold and no OpenCV-only release
veto. No real-repository gain is also a valid measurement outcome: record it and
recommend whether the added complexity is justified. Do not relax correctness
checks, alter denominators, or cross D1–D6 to force an improvement. If repository
include roots do not materially address the observed misses, use the evidence to
scope the next decision rather than expanding this implementation silently.

#### Step 4 — release readiness

**Status: planned; scope documented, execution not started.** Defined at the
user's request after Step 3b. This confidence step follows the completed P0–P6
implementation track; it is distinct from the original P4 header-routing phase.
The starting candidate is `3b0eea8`, with the
[Step 3b evaluation](../evals/clang-semantic-step3b.md) as its evidence baseline
and [draft MR #379](https://github.com/synaptixs/spine/pull/379) as the delivery vehicle.

**Objective:** determine whether the current optional clang support is ready for
maintainer merge and release review, with an explicit support contract, accepted
limitations and evidence tied to the candidate being reviewed. Completing this
plan does not itself authorize merging the MR or publishing a release.

##### Scope and boundaries

- Preserve **D1–D6** as recorded above. This is a release-readiness review, not a
  new recovery-expansion track. Compilation databases, host SDKs, generated
  stubs, CST identity repair and broader USR support remain outside scope.
- Keep `[clang]` optional, included in `[all]` and excluded from `[languages]`.
  Explain that installing it enables the pass for eligible C/C++ extraction;
  users of `[all]` also incur its cost. Verify the documented installation and
  omission paths rather than assuming an unimplemented enable/disable flag.
- Describe support as **repository-dependent enrichment between existing
  grounded functions**. The measured pending-site fraction and selected label
  coverage are not whole-repository recall or population-wide precision.
- Reuse the frozen Step 3b repositories, labels, source audits and timing
  methodology. A documentation-only change does not require another 45-run
  benchmark. If fixes change semantic behavior or cost, rerun the affected
  comparisons and audit their differences before using the old conclusions;
  shared mapper, include-root or traversal changes affect all five repositories.
- Keep `episteme/` and the working root roadmap out of commits. Continue on the
  existing MR; this plan introduces no new release, version bump or promotion PR.

##### Work sequence and deliverables

All work items below are **planned**. Prior Step 3b checks are inputs to this
review, not evidence that Step 4 has already been executed.

| Work item | Planned action | Required completion evidence |
|---|---|---|
| **4.1 — Define the support contract** | Reconcile README, SETUP, USER_GUIDE and parser documentation around optional activation, supported identity shapes, repository profiles, fixed flags, platform evidence and coverage limits. Distinguish wheel availability from a successful runtime test. | A support matrix linking each claim to an existing test or measured result; installation/omission guidance that matches actual behavior, including `[all]`. Untested platforms and unsupported shapes are stated explicitly. |
| **4.2 — Review correctness and known losses** | Review the five correct OpenCV losses individually, the lambda and namespace corrections, the fixed negative cases and retained Step 3 additions. Record what is diagnosed and what remains unexplained. | A source-linked disposition for each correct loss: accept as a documented limitation, fix and revalidate, or hold release. The three partial-AST losses must not acquire an invented root-cause explanation. No unresolved reviewed incorrect or ambiguous additions; retained/refused audit relationships remain accounted for. |
| **4.3 — Review operational cost** | Assess the measured cost for each supported use case, including automatic activation through `[all]`. Review existing cache behavior and invalidation if cached operation is used to justify usability. | Explicit disposition of OpenCV's 29.501 s clang-off, 58.381 s previous-clang and 300.296 s candidate medians, with the recorded ranges and single-host limits. State when batch use is acceptable and when the extra offers insufficient benefit. Any cache-hit claim has a measured hit/miss and invalidation receipt separate from the fresh-extraction benchmark. |
| **4.4 — Validate the final candidate** | Pin the candidate commit and reconcile its code with the measured hashes. Review packaging and run absent/present-extra smoke checks in isolated environments. Complete applicable CONTRIBUTING gates, the documentation matrix and semantic-pass checklist; inspect CI on the final revision. | A candidate-specific validation record: focused regressions, full pytest summary, mypy/ruff, generated checks, accuracy, repository shapes, self-verification, documentation audit and CI links. Record skips and existing warnings. Keep workspace files frozen during full pytest. Explain any reused measurements and any changes since `3b0eea8`; resolve new failures before readiness. |
| **4.5 — Record the maintainer decision** | Present the support contract, correctness dispositions, cost assessment and candidate checks for final review on MR #379. | A dated decision identifying the reviewed commit and reviewer, accepted limitations, remaining blockers and follow-up ownership. Record **ready for merge/release review**, **hold for specified fixes**, or **defer support**. Merge/publish actions require the subsequent maintainer authorization and normal release process. |

The planned output is `docs/evals/clang-semantic-release-readiness.md`, linked
from this section and the existing MR when created. It must contain the support
matrix, per-loss and per-profile decisions, validation receipts, and final
decision. Update this status, SPEC-INDEX, STATE-OF-SPINE and the MR together as
work completes; do not mark readiness from a checklist with pending evidence.

##### Decision inputs that must remain visible

- **Correctness:** five correct OpenCV relationships were lost; two have
  MAX-expansion range evidence and three lack matching calls in the partial AST
  without a fully isolated cause. The fixed old audit retains 164 of its previous
  166 correct relationships; all 32 old negative/ambiguous cases remain absent,
  and all 27 Step 3 additions remain. The new fixed OpenCV sample retains 198
  correct additions and refuses two wrong ones; all 54 GoogleTest additions were
  source-reviewed. These are bounded reviews, not an independent population audit.
- **Benefit and cost:** OpenCV gains 1,844 relationships over the previous pass
  at +241.914 s median extraction cost. TinyXML-2 retains useful existing
  contribution with no new edges; GoogleTest gains 54. pugixml recovers zero;
  fmt contributes only 24 bundled-test relationships and no fmt-owned ones.
  Include all five profiles in the decision, including those with no useful gain.
- **Claim limits:** frozen supported-label presence is 48/50 OpenCV, 50/50
  TinyXML-2, 0/6 pugixml, 1/50 fmt and 2/50 GoogleTest. The fmt labels come from
  bundled GoogleTest and the pugixml sample is short of 50. Keep those selection
  limits, unchanged denominators and the complete timing ranges attached to claims.

##### Exit criteria

Step 4 is complete when the readiness record and final MR review identify an
explicit decision for the pinned candidate, with every correctness/cost concern
either accepted with evidence or assigned a concrete blocking disposition.
**Completion may result in a hold or defer decision; it does not imply shipment.**

A **ready** recommendation additionally requires passing applicable local and
remote gates, a verified support contract, no unresolved reviewed wrong/ambiguous
additions, and explicit acceptance of the remaining correct-edge losses and
runtime cost by the decision owner. An unresolved concern produces a hold;
metrics must not be improved by changing labels, denominators or D1–D6.
There is no universal recovery threshold and no automatic OpenCV-only veto.

## 6. What this buys, measured

The parser choice is not an aesthetic preference. It is what makes the accuracy claim possible:

| | Result |
|---|---|
| Precision | **1.00** on every node kind and every edge kind, all 10 languages — on the corpus, which now includes the shadowed-callee shape (§3) |
| Recall | 1.00 on every kind except `CALLS` |
| `CALLS` recall | 1.00 (c, sql) · 0.89 (perl) · 0.86 (typescript) · 1.00 (cpp with clang) · 0.75 (csharp, go, php) · 0.73 (python) · 0.67 (java) |
| Invention | **0** on this repo, and **0** across 11 pinned public repos in 6 front-ends (2026-08-24). Java and SQL are recorded *not-applicable* with reasons rather than scored 0 |
| Invention gate | **`strict`, zero per language** — the one metric gated on an absolute value rather than against the baseline, because it is the one with a correct value |

**The failure mode is silence, not fiction** — held, rather than assumed. It was untrue for
four front-ends until 2026-08-24, and what made it true again was a detector plus a fixture,
not a claim. Everything the graph asserts exists; what it misses, it misses quietly. For an agent reasoning over the graph those are not equally bad — a
missing edge makes it search, a fabricated one makes it confidently follow a call into a
function that was never written.

And the graph pays for itself downstream: across 260 runs on two frontier models, **47 of 68**
new modules integrated correctly with the graph in context versus **3 of 68** without, while
tickets that named their target file scored **122 of 124 either way** — the control that rules
out "more context helps".

---

## 7. Practical notes for anyone touching a front-end

- **Extend `facts.py`, not a renderer.** Comprehension surfaces render facts; they never
  re-derive them from paths or filenames. If you need a new fact, the vocabulary is the place.
- **`--language` is not validated in `cli.py`.** An unsupported value silently scaffolds a
  *Python* project — detection and extraction are independent systems, and a language can be
  detected while yielding zero graph nodes.
- **Changing a `Protocol`? Update its test fakes.** The gate runs `mypy src tests`; typing
  `src` alone passes locally and fails CI.
- **`pkg extract --json` omits edges** — nodes plus a summary. Use `pkg export --format json`
  for the whole graph.
- **Adding a language** is a `LanguageExtractor` (suffixes + `extract`), a `pyproject` extra
  for its tree-sitter grammar, and corpus cases. The universal schema does not change — which
  is why Go and four others landed without reworking the model.
