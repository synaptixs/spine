# Extensive Cargo-based Rust validation — 2026-10-06

**Scope:** P11–P14 after the [Core Rust sign-off](rust-core-signoff.md). The implementation was branched from fetched Spine `origin/develop` at `bca338f86b82189521fcb4bfdc80de2385a945b6`; the Rust hardening and corpus commit is `dbc5602b9df6f513bbab879eb335973d5b14734b`. This record supports Cargo workspace, async, macro-boundary, feature-gated, and `no_std` source patterns on supported hosts. It does not claim macro expansion, type-aware dispatch, firmware compilation, or every feature combination.

## P11 — coverage census and ownership

Synaptreesitter remains the workspace and native-build anchor. Three small public repositories fill distinct gaps; no fourth repository was needed. The corpus is the precision oracle. The table assigns every gap in roadmap §24A.1 to evidence or a later maturity level.
For all three targeted repositories, the static `CargoIndex` workspace-member and package counts and every target's package, kind, and source path matched `cargo metadata --format-version 1 --no-deps --offline` exactly (1/1/11, 1/1/4, and 8/8/8 members/packages/targets respectively).

| Risk | Owner and validation boundary |
|---|---|
| Cargo workspace, path dependencies, custom target paths, build scripts/native C, Rust 2024/MSRV | [Synaptreesitter Core sign-off](rust-core-signoff.md), pinned `master`; P11 clean tracked-source rerun below. |
| Traits/generics, imports/reexports/aliases, `cfg` ambiguity | Rust corpus, Synaptreesitter; precision-first `CALLS` and three labelled cross-module recall gaps remain. |
| Declarative and procedural macros | `macro_include`, `cfg_macro`, and new `proc_macro_boundary` corpus cases; pinned `async-trait` source and native tests below. Macro expansion remains outside the source graph. |
| Async/Tokio, channels, spawn, `select!`, integration tests | New `async_runtime` corpus case and pinned `mini-redis` including an isolated async feature run below. |
| `no_std`, `core`/`alloc`, feature-gated source | New `no_std_features` corpus case and pinned `embedded-hal` below. Source comprehension remains configuration agnostic. |
| One non-default feature configuration | `embedded-hal` package, `--no-default-features --features defmt-03`; host `cargo check` below. A full feature matrix is R6. |
| Embedded/custom target and linker | P13 target and linker capability checks, with a live missing-target probe below. Cross compilation and firmware execution are R6. |
| Heavy FFI/bindgen | Synaptreesitter's `cc` build-script path is the native baseline. `libclang`, CMake, system libraries, and bindgen remain project-specific R6 prerequisites when present. |
| Axum/Actix/Tonic routes | Framework endpoint enrichment is a separate follow-on, as decided in roadmap D13. |
| Nightly-only Rust and non-Cargo build systems | Explicitly outside P11–P14. The nightly-only `async-trait` UI test remains ignored by its own suite. |
| Type-aware receiver dispatch, derive output, MIR/HIR | R5 rust-analyzer enrichment; tree-sitter-only facts remain source-grounded. |

## P12 — async and macro boundary

| Validation target | Explicit branch and pinned SHA | Cargo topology | Orchard parser census | Rust graph and verifier | Native run |
|---|---|---|---|---|---|
| [mini-redis](https://github.com/tokio-rs/mini-redis/tree/3d93b42bc363220f85af4fc9e1bebd35b588a4a3) | `master`, `3d93b42bc363220f85af4fc9e1bebd35b588a4a3` | 1 package, 11 targets; Tokio, channels, spawn, `select!` | 28 `.rs` files; 0 files/lines with `ERROR` | Pinned source: 338 nodes, 587 edges; `verify_batch`: 0 errors/warnings | `cargo test --locked --offline`: 15 integration tests and 11 doctests passed. |
| [async-trait](https://github.com/dtolnay/async-trait/tree/20fffdd704d82bcf0560cc9ab0d1178b30e4691c) | `master`, `20fffdd704d82bcf0560cc9ab0d1178b30e4691c` | 1 package, 4 targets, proc-macro lib | 28 `.rs` files; 1 test file has 2 one-character `ERROR` spans at `tests/test.rs:933,956`, both in `where mac!(Self): Send`; production `src/` parses cleanly | 607 nodes, 830 edges; `verify_batch`: 0 errors/warnings; parser gap recorded | `cargo test`: 4 integration and 5 doctests passed; 1 nightly-only UI test ignored by the repository. |

The corpus now has 14 Rust cases. Every emitted node/edge kind has **1.00 precision**: 84/84 labelled first-party nodes and 87/87 emitted labelled edges. `CALLS` matches 6/9 expected edges; the remaining 3 cross-module/workspace misses are still labelled as Core P2X gaps. In `async_runtime`, local `helper()` and `self.local()` resolve; inferred `worker.run()` and tokens inside `tokio::select!` produce no invented edge. In `proc_macro_boundary`, four source declarations are present, while the labelled derive `IMPLEMENTS` edge and invocation-generated function are absent. This is a **0/2 sampled generated-fact recall boundary**, not a claim that every macro output was enumerated. `include!` remains opaque.

To exercise the actual Spine SDLC path, a disposable mini-redis checkout received [this exact two-file diff](rust-extensive-mini-redis.patch) (`git apply --unidiff-zero` at the pinned SHA): an async `Client::ping_many` method using the repository's Tokio runtime and one integration test. `CargoTestRunner` selected the changed `mini-redis` package, then `cargo build` and `cargo test` passed; `RustPreflightRunner` passed Rustfmt and Clippy. The changed checkout ran 16 integration tests, including the new test, and 11 doctests. The diff is validation evidence and was not merged upstream. The first sandboxed test attempt could not bind a loopback socket; rerunning with local socket access passed.

## P13 — `no_std`, features, and environment

| Validation target | Explicit branch and pinned SHA | Cargo topology | Parser and graph | Build configuration |
|---|---|---|---|---|
| [embedded-hal](https://github.com/rust-embedded/embedded-hal/tree/41f29f6bfced1cae0cbe712ba96ee32c075b3125) | `master`, `41f29f6bfced1cae0cbe712ba96ee32c075b3125` | 8 workspace packages, 8 targets | 56 `.rs` files, 0 parser errors; 834 nodes/1,279 edges; `verify_batch`: 0 errors/warnings | `cargo check -p embedded-hal --no-default-features` and `cargo check -p embedded-hal --no-default-features --features defmt-03` both passed. |

The `no_std_features` corpus case proves `#![no_std]` does not block extraction, `alloc` and `core::fmt` remain external imports, and an imported `fmt::Display` trait resolves to external `core::fmt::Display`. Both `cfg(feature = "fast")` and its inverse are represented as one canonical source identity, so the ambiguous `mode()` call is refused. The copied fixture also passed host Cargo checks with default and explicit `fast` features. This graph is configuration agnostic; the Cargo checks each select one concrete configuration.

Before governed codegen, an explicit `CARGO_BUILD_TARGET` or `.cargo/config[.toml]` target is checked without installing a toolchain or executing repository code. A missing Rust standard library target reports the exact `rustup target add <triple>` prerequisite; an explicitly configured missing linker is named; a custom JSON target reports the need for project-specific `build-std`, linker, and toolchain setup. A live probe with `thumbv7em-none-eabihf` returned the missing-target message. Unit tests cover all three paths. No embedded binary was built or run.

## P14 — final evidence and claim boundary

The clean archive of Synaptreesitter [merged `master` commit](https://github.com/synaptixs/Synaptreesitter/tree/24d4bc18e58df2ddded3af32e51cf816a0bc8e75) has 109 tracked Rust files and 0 Orchard parser errors. Rust-only extraction yields 4,702 nodes and 8,186 edges, with 0 verifier errors/warnings. The one-node change from the Core record's 4,703 is explained by the new external `std`/`core` alias normalization: five shortened external trait placeholders became four fully qualified placeholders because `std::error::Error` was already present. No graph edge was lost. The [Core sign-off](rust-core-signoff.md) retains the repository `make lint`, `make test`, custom-path, reverse-dependent, and false-green evidence.

R1 syntax, R2 Cargo topology, R3 source semantics, and R4 governed SDLC are covered by the Core sign-off and the tranche checks above. R5 type-aware receiver and macro-expanded semantics and R6 embedded/cross/native-specialized execution are explicit limitations. The async-trait test-only parse spans and three cross-module `CALLS` misses also remain measured limits. The supported release wording is: **Extensive Cargo-based Rust support validated across workspace, async, macro-boundary, feature-gated, and `no_std` source patterns on supported hosts.**

Local Spine checks on the locked CI-extra environment: full pytest **5,527 passed, 9 skipped, 51 deselected**; Rust integration tests **4 passed** with Cargo 1.90; Ruff lint/format and mypy clean; `pkg verify` **0 errors** (one existing phantom-module warning); `pkg accuracy --check` **0 regressions**; docs audit **0 stale/missing**, and state-number, roadmap-status, matrix-count, and brief-section gates passed. The first sandboxed full-suite run hit user-cache permission errors; the rerun with normal cache and local socket permissions is the reported result.

Reproduce with the pinned SHAs above, Spine commit `dbc5602b9df6f513bbab879eb335973d5b14734b` or its descendant PR head, Rust 1.90.0 plus Rustfmt/Clippy, and a working host SDK. Run `python scripts/parse-census.py tree_sitter_rust_orchard <repo> --suffix .rs --json`, extract with `RepoCodeExtractor([RustExtractor()])` and `verify_batch`, then run `orchestrator pkg accuracy --check`. The Rust corpus, targeted repository, Ruff, mypy, docs, state, and CI results for the final PR revision complete the release gate; the PR checks are the authoritative platform matrix.
