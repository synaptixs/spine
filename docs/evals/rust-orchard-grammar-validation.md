# Rust Orchard grammar validation — 2026-10-05

The [comment on upstream grammar PR #319](https://github.com/tree-sitter/tree-sitter-rust/pull/319#issuecomment-5995878192) identified the maintained [Grammar Orchard fork](https://codeberg.org/grammar-orchard/tree-sitter-rust-orchard). Its published Python package, `tree-sitter-rust-orchard==0.16.8`, already accepts attributes on struct pattern fields. Merged Spine [PR #512](https://github.com/synaptixs/spine/pull/512) replaced the `[rust]` grammar dependency and adapted module path attributes to Orchard's CST: Orchard nests them in the `mod_item` under `attributes`, whereas the previous grammar emitted preceding sibling `attribute_item` nodes.

## Pinned validation

| Check | Result |
|---|---|
| Pinned Synaptreesitter `master` SHA `867aa6d14418163560bf89f12c48107098b1ec8f` | All **109 tracked Rust files** parse without `ERROR`; the stock `tree-sitter-rust 0.24.2` has one file with a four-line error. A checkout containing generated fixture files has 141 Rust files; Orchard parses all 141, while stock still has the same one error. |
| Pinned itoa SHA `1577ed901354d0d7448ac162328f9dbf5183124c` | All **5 Rust files** parse without `ERROR`. |
| Rust corpus | Full `orchestrator pkg accuracy --check`: **0 gated regressions**. The three predeclared cross-module/workspace `CALLS` gaps remain. |
| Pinned Synaptreesitter extraction | Rust-only graph: **4,703 nodes, 8,186 edges**, matching the previous patched-grammar baseline; `verify_batch` reports **0 errors and 0 warnings**. |
| Focused tests | **151 passed** for extractor, scope, verifier, grounding, and doctor checks using the installed Orchard grammar. The dedicated syntax regression also passes. |
| Lock and installation | `uv lock --check --offline` passes. The locked `uv sync` with CI's language extras built and installed Orchard on macOS. |

The package is [published on PyPI](https://pypi.org/project/tree-sitter-rust-orchard/) as a source distribution only. Installing `[rust]` therefore needs a C compiler. On this Mac, the initial build failed against the Command Line Tools SDK's malformed `arm64e.x1` target; Homebrew LLVM `clang` and the Xcode 26.2 SDK built it successfully. PR #512 passed its Linux CI and macOS/Windows source-only installation checks before merging. This removes dependence on the stalled upstream PR without claiming broader Rust syntax or platform coverage than the tests establish.
