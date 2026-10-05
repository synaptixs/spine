# Core Rust release evidence — 2026-10-05

**Result:** P0–P10 satisfy the reviewed roadmap's Core Rust gate for supported host environments. The supported claim is **Rust comprehension + Cargo-aware codegen/build/test**. This record closes the two release gates left open when the codegen implementation merged: the installed grammar's four-line parse error and Synaptreesitter's strict lint baseline.

## Merged implementation and validation anchors

| Change | Merge or validation ref | Result |
|---|---|---|
| Spine comprehension | [PR #509](https://github.com/synaptixs/spine/pull/509) | P0–P5 implementation, pinned Cargo and graph evidence, corpus and full Spine CI. |
| Spine codegen | [PR #511](https://github.com/synaptixs/spine/pull/511) | P6–P8 implementation and P9 live greenfield/brownfield runs; full CI passed. |
| Installed Rust grammar | [PR #512](https://github.com/synaptixs/spine/pull/512), merge `498cf159fc58eb2ffb29f67d7687711031165648` | Pins `tree-sitter-rust-orchard==0.16.8`; source-only installation checks passed on macOS and Windows, with Linux CI green. |
| Synaptreesitter strict lint baseline | [PR #3](https://github.com/synaptixs/Synaptreesitter/pull/3), merge `24d4bc18e58df2ddded3af32e51cf816a0bc8e75` | Function-scoped expectations record 18 existing complexity warnings. All 21 CI checks passed; one additional check was skipped. |

The original Synaptreesitter codegen anchor was explicit `master` commit `867aa6d14418163560bf89f12c48107098b1ec8f`, not its stale default branch. The merged lint-fix commit above has the same Git tree as PR #3 head `a83d1eb4be122ff9149d8df79b4ecd1387a4fbcd`, on which the generated-code replays ran. This makes the replayed source and the merged source identical.

## Final P9 checks

| Gate | Result on 2026-10-05 |
|---|---|
| Installed Orchard parser, clean checkout of merged Synaptreesitter | `parse-census.py tree_sitter_rust_orchard ... --suffix .rs --json`: **109/109 tracked Rust files**, **0 files with `ERROR`**, **0/64,016 lines in error spans**. The original pinned source also parsed 109/109 cleanly after PR #512. |
| Rust-only extraction on the same merged checkout | **4,703 nodes, 8,186 edges**; `verify_batch` returned **0 errors and 0 warnings**, matching the original pinned-source graph counts. |
| Rust corpus | `orchestrator pkg accuracy --check`: **0 gated regressions**. The three declared cross-module/workspace `CALLS` recall gaps remain. |
| Synaptreesitter repository baseline at merged PR #3 commit | `make lint` passed locked Cargo update, Rustfmt, and workspace/all-target Clippy with `-D warnings`. `make test` passed. Both used Rust 1.90.0 on macOS with Xcode 26.2 SDK; Node and copied pinned grammar fixtures were available for integration tests. |
| Spine-generated custom-path edit | Replayed the model-generated `Config::config_value` diff and **9 tests** in an isolated worktree at the PR #3 head. Repository `make lint` and `make test` both passed. The earlier affected-package run selected `tree-sitter-config`, `tree-sitter-cli`, and `xtask` and passed locked build/test. |
| Spine-generated shared-crate edit | Replayed the model-generated `contains_highlight_name` diff and **12 tests** in a second isolated worktree at the same tree. Repository `make lint` and `make test` both passed. The earlier affected-package run selected `tree-sitter-highlight`, `tree-sitter-cli`, `tree-sitter-loader`, and `xtask` and passed locked build/test. |
| Failure detection and clean reruns | The [codegen validation](rust-codegen-validation.md) records deliberate compile-red/test-red cases, changed-package and reverse-dependent selection, greenfield scaffold/idempotency, and clean-checkout reruns. The two final generated diffs were reapplied independently to clean worktrees. |

The generated additions are validation artifacts and were not merged into Synaptreesitter. PR #3 records existing Clippy complexity debt with scoped expectations; it does not simplify the affected functions or relax the repository-wide lint gate.

## Release boundary

Core Rust support is available for supported host environments. The Orchard Python package is source-only, so installing Spine's `[rust]` extra needs a C compiler. Rust extraction intentionally skips ambiguous `cfg` variants, macro-generated declarations, `include!` expansion, and receiver type inference. The three measured cross-module/workspace call gaps remain in the corpus. These are stated limits of the Core claim, not unmeasured successes.

P11–P14 (the separately estimated Extensive Rust tranche), rust-analyzer enrichment, and specialized embedded or cross-target execution require a separate go/no-go. This sign-off does not claim universal Cargo workspace or Rust ecosystem coverage.

## Evidence records

- [Comprehension validation](rust-comprehension-validation.md): Cargo topology, Rust graph, corpus precision/recall, profiling, consumer integration, and original pinned source.
- [Codegen validation](rust-codegen-validation.md): P6–P8, model-generated code, affected-package selection, false-green checks, and original P9 runs.
- [Orchard grammar validation](rust-orchard-grammar-validation.md): installed grammar, parser census, corpus, and installation checks.
