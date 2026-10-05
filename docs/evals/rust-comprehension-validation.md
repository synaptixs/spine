# Rust comprehension validation — 2026-10-04; grammar recheck 2026-10-05

**Branch:** `codex/rust-comprehension` (Part A, before codegen). This is an implementation and validation record, not a release sign-off. Rust codegen, Cargo execution, build and test selection are outside this branch.

## Pinned repositories and grammar

| Repository | Ref and commit | Rust files | Stock 0.24.2 errors | Patched errors |
|---|---|---:|---:|---:|
| [Synaptreesitter](https://github.com/synaptixs/Synaptreesitter) | explicit `master`, `867aa6d14418163560bf89f12c48107098b1ec8f` | 109 | 1 file, 4 of 63,981 source lines | 0 files, 0 lines |
| [itoa](https://github.com/dtolnay/itoa) | explicit `master`, `1577ed901354d0d7448ac162328f9dbf5183124c` | 5 | 0 of 630 source lines | 0 files, 0 lines |

The installed `tree-sitter-rust 0.24.2` has an error in `crates/generate/src/generate.rs`, around lines 517–520, where a `#[cfg(feature = "load")]` attribute occurs on a struct destructuring field. Version 0.24.1 has the same error. The stock parser emits a partial tree, so facts in that span cannot be claimed complete. A source patch now parses both pinned repositories cleanly, but the optional `[rust]` extra still installs the stock PyPI wheel; P0 stays open until a patched wheel or upstream release is pinned.

## Grammar patch — tested 2026-10-05

The latest published [upstream release](https://github.com/tree-sitter/tree-sitter-rust/releases) is v0.24.2, commit `77a3747266f4d621d0757825e6b11edcbf991ca5`. The [local patch](../../patches/tree-sitter-rust-0.24.2-field-pattern-attributes.patch) adds `repeat($.attribute_item)` at the start of `field_pattern` and an upstream-style regression case. This admits Rust attributes on struct pattern fields, which the 0.24.2 grammar omitted. The full upstream grammar corpus passes `tree-sitter test` (152/152, including the new case). The generated parser was built into an isolated Python binding and loaded ahead of the stock wheel via `PYTHONPATH`; Spine's dependency and lockfile were not changed by this experiment.

Reproduce from an upstream v0.24.2 source checkout, with Tree-sitter CLI 0.26.7 and a C compiler:

```bash
git apply /absolute/path/to/spine/patches/tree-sitter-rust-0.24.2-field-pattern-attributes.patch
tree-sitter generate
tree-sitter test --file-name attributes_on_struct_patterns.txt
python setup.py build_ext --inplace
```

On this macOS host, the Command Line Tools SDK linker rejected `arm64e.x1`. The successful Python build used Homebrew LLVM `clang` with `LDFLAGS=-isysroot` pointing to the Xcode 26.2 SDK. The patched binding was loaded from `bindings/python` in the checkout. With that binding on `PYTHONPATH`, `parse-census.py tree_sitter_rust ... --suffix .rs --json` reported 109/109 clean files and 0/63,981 error lines for Synaptreesitter, and 5/5 clean files and 0/630 error lines for itoa.

The eleven Rust corpus cases still have 1.00 precision for every emitted node and edge kind; `CALLS` recall remains 4/7 with the same three declared gaps. The full `pkg accuracy --check` reports **0 gated regressions**. Rust-only extraction on pinned Synaptreesitter remains 4,701 nodes, 8,186 edges, and zero `verify_batch` errors or warnings. This proves the syntax fix against the pinned source and current corpus; it does not make the stock 0.24.2 installation parse that source cleanly.

The host has no `cargo` or `rustc`. The static Cargo index was hand-checked against manifests, but `cargo metadata`, Synaptreesitter's `make lint` and `make test`, and the roadmap's toolchain baseline have not run. No build or test result is implied by extraction.

## Part A evidence

| Area | Result |
|---|---|
| Cargo topology | 9 explicit Synaptreesitter workspace members, one default member (`tree-sitter-cli`), 10 packages including a local path dependency, and 16 targets. Custom library paths `crates/cli/src/tree_sitter_cli.rs` and `lib/binding_rust/lib.rs`, plus `lib/binding_rust/build.rs`, are indexed. These are static results pending `cargo metadata` comparison. |
| Target and module identity | Labelled fixtures cover lib/bin/test separation, inline and outlined modules, `foo.rs` and `foo/mod.rs`, `#[path]`, workspace aliases, and noncolliding symbol IDs. |
| Graph verification | Rust-only extraction: Synaptreesitter 4,701 nodes / 8,186 edges; itoa 44 nodes / 60 edges. `verify_batch` reports zero errors and warnings on each. |
| Accuracy | Eleven Rust corpus cases; every emitted node and edge kind has 1.00 precision. `CALLS` recall is 4/7; three expected cross-module/workspace qualified calls remain labelled `known_gaps`. |
| Precision boundary | Local exact calls, direct trait implementations, supertraits, aliases and reexports are implemented. Macro-generated declarations, `include!` expansion, receiver type inference and ambiguous `cfg` variants are skipped. |
| Profiling and consumers | `.rs` and Cargo detection, `cargo` test-runner profiling, axum/actix-web/rocket manifest signals, language fences, visibility, scope and graph registrations have tests. Tokio alone is not classified as a web framework. |
| Local gates | `ruff check`, `ruff format --check`, `mypy` (822 source files), `docs_audit.py --strict` (0 stale/missing), `state-numbers.py --check` (14 gated claims), `render_architecture_svg.py --check`, and offline `uv lock --check` pass. The focused Rust/graph/profile/grounding/build-document run passed 314 tests. |

The mixed-language `orchestrator pkg verify` on Synaptreesitter currently fails three checks from C/C++ dangling call edges and JavaScript import joins. Its Rust-only graph passes. The full mixed-repo result must not be used as a Rust pass claim.

An initial whole-suite run reached 3,810 passed tests and was interrupted after 33 failures. Three failures were tests still using Rust as an unmeasured-language placeholder; these now use Ruby and pass. Other observed failures attempted to write the default cache under the sandbox-blocked home directory or make Jira calls in integration tests while network access was unavailable. The whole suite has not yet passed in this environment.

## Reproduction

```bash
.venv/bin/python scripts/parse-census.py tree_sitter_rust /path/to/Synaptreesitter --suffix .rs --json
.venv/bin/python scripts/parse-census.py tree_sitter_rust /path/to/itoa --suffix .rs --json
.venv/bin/orchestrator pkg accuracy --scoreboard
.venv/bin/pytest -q tests/pkg/test_rust_extractor.py tests/pkg/test_default_extractors.py tests/catalog/test_profile.py
```

The public repositories must be checked out at the exact SHAs above; do not follow Synaptreesitter's default branch implicitly. The Rust-only graph check uses `RepoCodeExtractor([RustExtractor()]).extract(root)` followed by `verify_batch(batch, root)`.

## Open release gates

1. Distribute the tested grammar fix as a versioned wheel or pin an upstream release that includes it, then repeat the census under the installed `[rust]` extra. The source patch and corpus recheck are complete; the stock install still has the error.
2. Compare the static workspace/target index with `cargo metadata` on a host with the required Rust toolchain. Run the pinned repository's `make lint` and `make test` baseline there.
3. Run the whole suite in a test environment with a writable package cache and mocked Jira transport. Review the resulting diff before calling P5 complete.

P2X's cross-module/workspace `CALLS` experiment is deferred per the roadmap's bounded fallback. The three missing corpus edges are counted as recall gaps, not silently excluded.
