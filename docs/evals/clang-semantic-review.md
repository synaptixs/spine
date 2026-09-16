# Clang semantic pass: P6 review

P6 was authorized before returning to P5 on 2026-09-15. This is a semantic
post-pass; language front-end registration rows deliberately do not apply.
The release decision remains separate from passing code and documentation checks.

## Manual documentation matrix walk

| Trigger | Review receipt |
|---|---|
| User-visible behavior | CHANGELOG Unreleased entry links parser design and validation evidence. |
| New front-end | No new front-end; ten remain. README, USER_GUIDE, KNOWLEDGE_GRAPH, AGENT_GUIDE, CLI_REFERENCE and SETUP describe the new pass and header routing. EXAMPLE, BENCHMARK and plugin language lists remain accurate because the language roster is unchanged. Corpus README carries both new cases. |
| Optional extra | pyproject `clang` and `all`, CI sync, doctor probe, persistence fingerprint and mypy override inspected; `languages` excludes clang. SETUP states size, grammar prerequisites and header limits. |
| CLI behavior | CLI_REFERENCE describes the single-repo text summaries; command count and flags are unchanged. USER_GUIDE and AGENT_GUIDE explain coverage. |
| MCP tools | No tool changes; generated inventory check passes. |
| Node/edge kinds | None added; capabilities use conditional SHARED_PASSES, not FRONT_ENDS. |
| New design spec | No new spec; the untracked roadmap stays excluded. Existing parsing design, SPEC-INDEX description and STATE-OF-SPINE are updated. |
| Changed spec status | No unrelated track status changes; clang measurements and pending release decision are explicit in the validation record. |
| Registry/UI | No changes. |
| Deploy/env/config | Optional extra only; no new environment variable or operational service. SETUP owns installation. |
| Contribution process | CONTRIBUTING explains census and real-repo validation commands. PR template remains accurate. |
| Release cut | No version bump; changelog is Unreleased. |
| Maintainer tooling | CONTRIBUTING documents the census and validation script. Front-end checklist now distinguishes semantic passes. |
| Removed surfaces | None. |

## Manual implementation/checklist review

- D1: one suffix owner remains in `default_extractors`/`FRONT_ENDS`; clang runs
  after CST finalization and before `link_imports`.
- D2: wheel-local library selection, fixed target/C11/C++17 modes, sorted in-repo
  include directories, no compilation-database reader or host SDK search.
- D3: no kind/ID changes; eligible in-repo declarations must map to grounded
  functions, as must callers. Templates/local/anonymous declaration identities
  remain refused; overloads retain the existing name-based ID; virtual dispatch
  uses the static target. Full byte ranges separate nested calls. Conflicts drop.
- D4: literal include routing is transitive, cycle-safe and limited to admitted
  files. C-only and C++ header corpus cases pass. Review caught a profiler mismatch
  at nested checkout boundaries; a failing regression test now proves `.git`
  directories and submodule `.git` files cannot reclassify a parent header.
- D5: libclang availability/version changes the fingerprint. Physically absent
  and present environments each pass the fingerprint tests.
- D6: source TUs need reachable pending sites before parsing; reports retain all
  pending sites, diagnostics and failures. Miss counts partition distinct sites.
- Lazy imports: base installations import neither clang nor tree-sitter eagerly.
  The optional pass returns the CST batch when its wheel is unavailable.
- Existing frontend/scope/import/doc/insight registries and codegen toolchains
  remain applicable. No new codegen language or compiler requirement is introduced.
- Both new corpus roots use `.repo/`; physical absent/present runs cover 53 roots
  with identical nodes, additive edges and grounded new endpoints.
- Cross-checkout determinism and compile-database refusal tests pass. The
  synthesized flags and candidate sorting retain deterministic behavior.

## Reviewed implementation locations

- `src/orchestrator/pkg/clang_link.py:32`: USR projection and refusal boundary.
- `src/orchestrator/pkg/clang_link.py:126`: grounded-only enrichment, TU selection,
  wheel-local library and synthesized flags.
- `src/orchestrator/pkg/extractor.py:719`: post-pass wiring.
- `src/orchestrator/pkg/c_extractor.py:533` and
  `src/orchestrator/catalog/profile.py:102`: header reachability and profile boundary.
- `src/orchestrator/pkg/persistence.py:175` / `:215`: parser presence and version digest.
- `pyproject.toml:72` / `:191` / `:350`, `.github/workflows/ci.yml:61`, and
  `src/orchestrator/doctor.py:147`: extra, all meta-extra, typing, CI and doctor.

## Evidence

- Fetched `origin/develop` = `94f106a`; rebase reported the branch already current.
- Pipeline shapes: all four hold (single, monorepo, multirepo, submodule).
- Spine graph verification: `OK — 0 error(s), 1 warning(s)`; warning lists seven
  phantom-module candidates, separately from the failing real-repo P5 checks.
- `understand .`: successfully wrote 91 files; generated episteme is restored
  before committing and is excluded from the MR.
- Physical environments: clang unavailable / available; 53 corpus roots each;
  identical nodes, additive edges and grounded new endpoints in every root.
- Fingerprint tests: absent `5 passed, 10 deselected in 0.61s`; present
  `5 passed, 10 deselected in 0.56s`.
- Focused review tests: `78 passed, 30 warnings in 0.45s`.

The full-suite/phase receipts and the final MR link are recorded in the roadmap
and MR. P5's low recovery and incomplete large-repo state smoke are explicit in
[the validation record](clang-semantic-validation.md); a draft MR must not imply
release approval or a clean external-repository verification result.


Final P6 receipt (workspace unchanged throughout the test run):

```text
3754 passed, 4 skipped, 51 deselected, 182 warnings in 211.56s (0:03:31)
```

Mypy, lint, formatting, all generated-artifact checks, the MCP inventory and
accuracy gate pass (zero gated regressions). All four repository shapes were
rechecked after the profiler fix. Final Spine verification again reports zero
errors and one warning. Spine extraction contains no corpus fixture source nodes.
The first suite attempt's single documentation-count mismatch occurred while
documentation was being edited between its two snapshots; the stable full rerun
above passes without changing that test.


P5 follow-up review: `stats.summarise_store` now counts incoming calls in its
existing edge pass, preserving the caller-exists and function-target conditions,
repeated call-site counts, missing-endpoint handling and deterministic tie order.
The regression test compares against `FactStore.callers_of`, bounds edge visits
and verifies unchanged nodes/edges. Full validation now completes both state
stages; see the final output in the validation record. README's three new links
were converted to absolute URLs after CI reported its PyPI-link check.
Final full suite: `3755 passed, 4 skipped, 51 deselected, 182 warnings in 198.13s (0:03:18)`.


Confidence step 2 review: caller existence alone was insufficient. The walk now
checks the actual enclosing function's mapped identity and source provenance,
with an explicit class/header overload exception. The five negative cases fail
on the old implementation; the positive overload case and existing semantic
suite pass. The source audit records 200 fixed judgments and retains their
failures instead of replacing them with easier examples. Current recovery claims
in README, SETUP, KNOWLEDGE_GRAPH, STATE and parser design were updated; the
Unreleased changelog names the guard. No extra/CLI/MCP/schema/registry surface or
new specification is introduced. Full findings and limitations are in
[the correctness audit](clang-semantic-correctness-audit.md).

Step 2 final full suite: `3761 passed, 4 skipped, 51 deselected, 182 warnings in 221.76s (0:03:41)`. Accuracy: zero regressions. All four shapes pass; Spine verification: zero errors, one warning.

## Confidence step 3 review

The caller projection extension retains full named scope and destructor spelling;
file-prefixed identities must match the declaration basename, then pass the
existing full source-file grounding check. Target mapping, synthesized flags,
TU selection, routing, nodes, IDs, packaging and cache inputs remain unchanged.
The source implementation is already part of the cache fingerprint.

Walked the documentation matrix and semantic-pass checklist again: CHANGELOG,
README, SETUP, KNOWLEDGE_GRAPH, parser design, STATE-OF-SPINE and validation evidence
carry the revised behavior or measurements. No new frontend, CLI/MCP surface,
configuration, schema, spec, dependency, release or maintainer command was added.
The 27 added edges received source review; the fixed Step 2 audit is preserved
and all 32 incorrect/ambiguous relationships remain absent. See
[the recovery report](clang-semantic-recovery.md) for per-edge evidence and gates.
