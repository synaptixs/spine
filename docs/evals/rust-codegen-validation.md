# Rust codegen validation — 2026-10-05

**Branch:** `codex/rust-codegen`, merged as [PR #511](https://github.com/synaptixs/spine/pull/511) after [PR #509](https://github.com/synaptixs/spine/pull/509). This records the original Part B validation. The later [Core release evidence](rust-core-signoff.md) records resolution of its upstream gates.

## Implemented path

| Phase | Implementation | Evidence |
|---|---|---|
| P6 | `TOOLCHAINS["rust"]`, Cargo environment, package and target aware layout, library or binary scaffold, Rustfmt and Clippy preflight | Greenfield crate builds and tests with Rust 1.90.0; real preflight passes. The scaffold is idempotent. The selected Synaptreesitter custom target is `tree-sitter-cli` / `lib` / `crates/cli/src/tree_sitter_cli.rs`; Cargo metadata confirms it. |
| P7 | Cargo build before test; changed package plus transitive reverse path dependents; workspace escalation for root manifest, lockfile, toolchain and `.cargo` configuration | A two-package workspace selects both `core` and dependent `app` when `core` changes or a file is renamed across them. Deliberate compile and test failures, including no test report, return red. A manifest edit permits lock update on build and fixes the lock on final test. |
| P8 | Rust implement/test/refine prompts, Cargo layout and edition/MSRV guidance, `rust-conventions` catalog and native skill | A scripted response passes the actual codegen file guards and real Cargo verification. A live run with the configured `claude-opus-5` model generated a greenfield addition feature and nine passing tests; Cargo build/test, Rustfmt and Clippy passed. |
| P9 | Pinned brownfield Cargo execution | Two configured-model codegen runs, affected-package build/test, repository `make test`, and clean checkout rerun are recorded below. The strict lint baseline was resolved and both generated diffs passed lint and test in the [post-merge replay](rust-core-signoff.md). |

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

## Gate resolution after PR #511

- At PR #511 merge, Synaptreesitter's strict `make lint` failed on existing Clippy complexity findings; the first run stopped after two. A warning-only scan found 18 affected functions. [Synaptreesitter PR #3](https://github.com/synaptixs/Synaptreesitter/pull/3) recorded those with function-scoped expectations and merged as `24d4bc18e58df2ddded3af32e51cf816a0bc8e75`. Strict lint now passes. This records existing complexity debt without claiming the functions were simplified.
- At PR #511 merge, `[rust]` still installed `tree-sitter-rust 0.24.2`, which produced the four-line parse error. [Spine PR #512](https://github.com/synaptixs/spine/pull/512) pinned the Orchard grammar and merged. The [Orchard validation](rust-orchard-grammar-validation.md) records the clean census and corpus.
- The model-generated config and highlight diffs were replayed independently on the exact tree merged by Synaptreesitter PR #3. Both passed repository `make lint` and `make test`; see [Core release evidence](rust-core-signoff.md). P9's previously open repository lint gate is closed.

## Spine gates

The full Python suite passed before the two final runner unit cases were added: **5,484 passed, 13 skipped, 51 deselected**. The focused Rust suite passed **18 tests** with Rust 1.90.0, including four real Cargo integration cases. Ruff, mypy, docs audit, state numbers, roadmap status, architecture rendering, and package accuracy checks passed. The dedicated Rust CI job and the full main check passed on the final commit of PR #511.
