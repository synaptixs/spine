# Rust codegen validation — 2026-10-05

**Branch:** `codex/rust-codegen`, merged as [PR #511](https://github.com/synaptixs/spine/pull/511) after [PR #509](https://github.com/synaptixs/spine/pull/509). This is validation evidence for Part B of the reviewed Rust roadmap; it is not a Core Rust release sign-off.

## Implemented path

| Phase | Implementation | Evidence |
|---|---|---|
| P6 | `TOOLCHAINS["rust"]`, Cargo environment, package and target aware layout, library or binary scaffold, Rustfmt and Clippy preflight | Greenfield crate builds and tests with Rust 1.90.0; real preflight passes. The scaffold is idempotent. The selected Synaptreesitter custom target is `tree-sitter-cli` / `lib` / `crates/cli/src/tree_sitter_cli.rs`; Cargo metadata confirms it. |
| P7 | Cargo build before test; changed package plus transitive reverse path dependents; workspace escalation for root manifest, lockfile, toolchain and `.cargo` configuration | A two-package workspace selects both `core` and dependent `app` when `core` changes or a file is renamed across them. Deliberate compile and test failures, including no test report, return red. A manifest edit permits lock update on build and fixes the lock on final test. |
| P8 | Rust implement/test/refine prompts, Cargo layout and edition/MSRV guidance, `rust-conventions` catalog and native skill | A scripted response passes the actual codegen file guards and real Cargo verification. A live run with the configured `claude-opus-5` model generated a greenfield addition feature and nine passing tests; Cargo build/test, Rustfmt and Clippy passed. |
| P9 | Pinned brownfield Cargo execution | Two configured-model codegen runs, affected-package build/test, repository `make test`, and clean checkout rerun are recorded below. Strict upstream lint remains red. |

All real Cargo runs used Rust 1.90.0, Rustfmt and Clippy from the temporary toolchain, Xcode 26.2 SDK, and a writable `XDG_CACHE_HOME`. These variables were test-host setup, not new codegen requirements.

## Pinned Synaptreesitter runs

Source is explicit `master` commit `867aa6d14418163560bf89f12c48107098b1ec8f`, the same commit and Cargo topology used for [comprehension validation](rust-comprehension-validation.md). Fixture grammars were copied from the previous passing `make test` baseline into isolated temporary worktrees. No source changes were made in the original validation checkout.

1. **Custom target:** added one isolated test to `crates/cli/src/tree_sitter_cli.rs`. The runner selected `tree-sitter-cli` and its reverse dependent `xtask`. `cargo build` and `cargo test` passed; the CLI library ran **311 passing tests**, including the added test.
2. **Shared crate:** added one isolated test to `lib/binding_rust/lib.rs`. The runner selected `tree-sitter`, `tree-sitter-cli`, `tree-sitter-highlight`, `tree-sitter-loader`, `tree-sitter-tags`, and `xtask`. Locked build and tests passed.
3. **Clean checkout rerun:** reapplied the shared-crate test to a fresh worktree at the same pinned commit, copied the same fixture baseline, and repeated the six-package locked build and test successfully. The repository's `make test` gate also passed on this changed worktree. Its `make lint` gate passed locked update and Rustfmt, then stopped on the same two pre-existing Clippy warnings listed below.

The three proof additions above were deterministic local test edits. The following runs used the configured `claude-opus-5` model through `LLMCodegenAdapter`, including its anchored brownfield edit guard and the `rust-conventions` skill:

4. **Live custom-path target:** from a fresh pinned checkout, the model added `Config::config_value` and nine co-located tests to `crates/config/src/tree_sitter_config.rs` (`tree-sitter-config`, Rust 2024, MSRV 1.90). `CargoTestRunner` selected `tree-sitter-config`, `tree-sitter-cli`, and `xtask`; locked build/test passed, including all nine new tests and **310 CLI tests**. One generated Clippy assertion was corrected by the model's refine step. The package-only strict Clippy check and the repository's `make test` passed.
5. **Live shared crate:** from a separate pinned checkout, the model added `contains_highlight_name` and twelve co-located tests to `crates/highlight/src/highlight.rs`. The runner selected `tree-sitter-highlight`, `tree-sitter-cli`, `tree-sitter-loader`, and `xtask`; locked build/test passed, including all twelve new tests and **310 CLI tests**. The model refined a Rustfmt line wrap and a Clippy `manual_contains` finding. The repository's `make test` passed. Reapplying the exact generated diff to a fresh pinned checkout reproduced the four-package locked build/test pass and twelve test pass.

The generated implementations and tests remain in disposable Synaptreesitter worktrees; this Spine branch contains only the generic codegen support and validation evidence.

## Open gates

- The repository-equivalent Rust preflight respects Synaptreesitter's `cargo clippy --workspace --all-targets -- -D warnings` policy. `make lint` fails on two pre-existing `cognitive_complexity` findings in `crates/tags/src/tags.rs:345` and `crates/highlight/src/highlight.rs:892`, already recorded before codegen. Scoped Clippy can also expose existing complexity findings in `crates/generate`. Rustfmt passes. Generated warnings were corrected in the live runs; the upstream lint baseline is still red.
- At PR #511 merge, the normal `[rust]` installation still received `tree-sitter-rust 0.24.2`, which has the four-line parse error at the pinned source. A [follow-up Orchard grammar validation](rust-orchard-grammar-validation.md) tests a published alternative; it is not part of PR #511.
- PR #511 passed CI and merged, completing its review and delivery work. P9 remains open because the repository's strict lint gate is red at the pinned upstream baseline. Core Rust support is not yet a release claim.

## Spine gates

The full Python suite passed before the two final runner unit cases were added: **5,484 passed, 13 skipped, 51 deselected**. The focused Rust suite passed **18 tests** with Rust 1.90.0, including four real Cargo integration cases. Ruff, mypy, docs audit, state numbers, roadmap status, architecture rendering, and package accuracy checks passed. The dedicated Rust CI job and the full main check passed on the final commit of PR #511.
