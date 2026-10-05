# Rust comprehension validation — 2026-10-04; grammar recheck 2026-10-05

**Branch:** `codex/rust-comprehension` (Part A, before codegen). This is an implementation and validation record, not a release sign-off. Rust codegen, Cargo execution, build and test selection are outside this branch.

## Pinned repositories and grammar

| Repository | Ref and commit | Rust files | Stock 0.24.2 errors | Patched errors |
|---|---|---:|---:|---:|
| [Synaptreesitter](https://github.com/synaptixs/Synaptreesitter) | explicit `master`, `867aa6d14418163560bf89f12c48107098b1ec8f` | 109 | 1 file, 4 of 63,981 source lines | 0 files, 0 lines |
| [itoa](https://github.com/dtolnay/itoa) | explicit `master`, `1577ed901354d0d7448ac162328f9dbf5183124c` | 5 | 0 of 630 source lines | 0 files, 0 lines |

At the time of PR #509, the installed `tree-sitter-rust 0.24.2` had an error in `crates/generate/src/generate.rs`, around lines 517–520, where a `#[cfg(feature = "load")]` attribute occurs on a struct destructuring field. Version 0.24.1 has the same error. The stock parser emits a partial tree, so facts in that span cannot be claimed complete. A source patch parsed both pinned repositories cleanly, but the optional `[rust]` extra still installed the stock PyPI wheel. The later [Orchard grammar validation](rust-orchard-grammar-validation.md) records the installed fix.

## Grammar patch — tested 2026-10-05

The latest published [upstream release](https://github.com/tree-sitter/tree-sitter-rust/releases) is v0.24.2, commit `77a3747266f4d621d0757825e6b11edcbf991ca5`. The [local patch](../../patches/tree-sitter-rust-0.24.2-field-pattern-attributes.patch) adds `repeat($.attribute_item)` at the start of `field_pattern` and an upstream-style regression case. This admits Rust attributes on struct pattern fields, which the 0.24.2 grammar omitted. The full upstream grammar corpus passes `tree-sitter test` (152/152, including the new case). The generated parser was built into an isolated Python binding and loaded ahead of the stock wheel via `PYTHONPATH`; Spine's dependency and lockfile were not changed by this experiment.

The fix was proposed upstream in [tree-sitter-rust PR #319](https://github.com/tree-sitter/tree-sitter-rust/pull/319), with regenerated parser files. The `[rust]` extra remained on the stock release at PR #509 merge. The PR discussion subsequently identified the published Orchard fork as another path to a clean installed grammar.

Reproduce from an upstream v0.24.2 source checkout, with Tree-sitter CLI 0.26.7 and a C compiler:

```bash
git apply /absolute/path/to/spine/patches/tree-sitter-rust-0.24.2-field-pattern-attributes.patch
tree-sitter generate
tree-sitter test --file-name attributes_on_struct_patterns.txt
python setup.py build_ext --inplace
```

On this macOS host, the Command Line Tools SDK linker rejected `arm64e.x1`. The successful Python build used Homebrew LLVM `clang` with `LDFLAGS=-isysroot` pointing to the Xcode 26.2 SDK. The patched binding was loaded from `bindings/python` in the checkout. With that binding on `PYTHONPATH`, `parse-census.py tree_sitter_rust ... --suffix .rs --json` reported 109/109 clean files and 0/63,981 error lines for Synaptreesitter, and 5/5 clean files and 0/630 error lines for itoa.

The eleven Rust corpus cases still have 1.00 precision for every emitted node and edge kind; `CALLS` recall remains 4/7 with the same three declared gaps. The full `pkg accuracy --check` reports **0 gated regressions**. After the Cargo-index correction, Rust-only extraction on pinned Synaptreesitter has 4,703 nodes, 8,186 edges, and zero `verify_batch` errors or warnings. This proves the syntax fix against the pinned source and current corpus; it does not make the stock 0.24.2 installation parse that source cleanly.

Rust 1.90.0 was installed into temporary directories for the Cargo baseline. `cargo metadata --no-deps --locked --offline` found an implicit tenth Synaptreesitter workspace member (the CLI test proc-macro crate), which the static index had listed as a package but not a member. It also identified that crate's target as `proc-macro`, not `lib`. Both static-index differences were fixed and covered by a fixture test. Cargo and Spine now agree on all 10 members, the one default member, all 10 packages, and all 16 target kinds, names, and paths. The itoa comparison also agrees on its one package and three targets.

At the same pinned Synaptreesitter SHA, `make test` passed with Rust 1.90.0 and the Xcode 26.2 SDK. `make lint` passed its locked update and formatting steps, then failed Clippy on two existing `cognitive_complexity` warnings: `crates/tags/src/tags.rs:345` (36/25) and `crates/highlight/src/highlight.rs:892` (33/25). These are upstream baseline findings; no Synaptreesitter source was changed for this validation.

## Part A evidence

| Area | Result |
|---|---|
| Cargo topology | 9 explicit and 1 implicit Synaptreesitter workspace members, one default member (`tree-sitter-cli`), 10 packages, and 16 targets, all matched against `cargo metadata`. Custom library paths `crates/cli/src/tree_sitter_cli.rs` and `lib/binding_rust/lib.rs`, plus `lib/binding_rust/build.rs`, are indexed. |
| Target and module identity | Labelled fixtures cover lib/bin/test separation, inline and outlined modules, `foo.rs` and `foo/mod.rs`, `#[path]`, workspace aliases, and noncolliding symbol IDs. |
| Graph verification | Rust-only extraction: Synaptreesitter 4,703 nodes / 8,186 edges; itoa 44 nodes / 60 edges. `verify_batch` reports zero errors and warnings on each. |
| Accuracy | Eleven Rust corpus cases; every emitted node and edge kind has 1.00 precision. `CALLS` recall is 4/7; three expected cross-module/workspace qualified calls remain labelled `known_gaps`. |
| Precision boundary | Local exact calls, direct trait implementations, supertraits, aliases and reexports are implemented. Macro-generated declarations, `include!` expansion, receiver type inference and ambiguous `cfg` variants are skipped. |
| Profiling and consumers | `.rs` and Cargo detection, `cargo` test-runner profiling, axum/actix-web/rocket manifest signals, language fences, visibility, scope and graph registrations have tests. Tokio alone is not classified as a web framework. |
| Local gates | `ruff check`, `ruff format --check`, `mypy` (822 source files), `docs_audit.py --strict` (0 stale/missing), `state-numbers.py --check` (14 gated claims), `render_architecture_svg.py --check`, and offline `uv lock --check` pass. The full Python suite passed 5,472 tests, with 9 skipped and 51 deselected by project configuration. |

The mixed-language `orchestrator pkg verify` on Synaptreesitter currently fails three checks from C/C++ dangling call edges and JavaScript import joins. Its Rust-only graph passes. The full mixed-repo result must not be used as a Rust pass claim.

The full Python suite passed in one run with a writable temporary home/cache, an offline npm cache primed for the real Vitest integration cases, and permission for Temporal's local ephemeral test server: **5,472 passed, 9 skipped, 51 deselected, 0 failed**. Live autorun publish tests now mock Jira worklogs, so they never contact an issue tracker. A real worklog HTTP transport failure is also non-fatal to an otherwise successful run and has a regression test.

## Reproduction

```bash
.venv/bin/python scripts/parse-census.py tree_sitter_rust /path/to/Synaptreesitter --suffix .rs --json
.venv/bin/python scripts/parse-census.py tree_sitter_rust /path/to/itoa --suffix .rs --json
.venv/bin/orchestrator pkg accuracy --scoreboard
.venv/bin/pytest -q tests/pkg/test_rust_extractor.py tests/pkg/test_default_extractors.py tests/catalog/test_profile.py
```

The public repositories must be checked out at the exact SHAs above; do not follow Synaptreesitter's default branch implicitly. The Rust-only graph check uses `RepoCodeExtractor([RustExtractor()]).extract(root)` followed by `verify_batch(batch, root)`.

## Post-merge release gate

Spine [PR #509](https://github.com/synaptixs/spine/pull/509) and codegen [PR #511](https://github.com/synaptixs/spine/pull/511) merged on 2026-10-05. At that point the installed-parser release gate remained open: the stock grammar still had the four-line error. [PR #512](https://github.com/synaptixs/spine/pull/512) later pinned the published Orchard grammar and closed that gate; see the [Orchard validation](rust-orchard-grammar-validation.md). This source-only package requires a C compiler during installation.

The Cargo metadata comparison, pinned `make test` baseline, and full Spine suite were complete at PR #509 merge. Synaptreesitter's two initially reported Clippy warnings above were an upstream lint baseline, not a Spine parser failure. A later scan found 18 existing complexity warnings, recorded in merged [Synaptreesitter PR #3](https://github.com/synaptixs/Synaptreesitter/pull/3); the [Core release evidence](rust-core-signoff.md) records the now-green `make lint` gate.

P2X's cross-module/workspace `CALLS` experiment is deferred per the roadmap's bounded fallback. The three missing corpus edges are counted as recall gaps, not silently excluded.
