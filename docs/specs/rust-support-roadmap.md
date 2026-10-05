# Design + Plan: adding Rust to the PKG — comprehension, then codegen

**Status:** Part A implementation in progress on `codex/rust-comprehension` (2026-10-04). The parser and Cargo baseline gates remain open; see [validation evidence](../evals/rust-comprehension-validation.md).
**Base:** spine `v3.52.0`.
**Branch A:** `codex/rust-comprehension` off `origin/develop` (implementation branch).
**Branch B:** `feat/rust-codegen` after comprehension merges.

**Delivery model**

1. **Part A — Rust comprehension** merges first and is independently release-worthy.
2. **Part B — Rust codegen** starts only after Part A is merged and measured.
3. Both remain described by this design record, but they are separate PR/MR boundaries.
4. Rust framework-specific endpoint/data-layer enrichment is not silently implied by “Rust support”; it is explicitly separated from the base language track unless added as a follow-on phase.

## Synaptreesitter validation baseline — reviewed 2026-10-03

The public validation repository is now accessible and has been inspected directly.

**Repository:** `https://github.com/synaptixs/Synaptreesitter`

**Important branch rule:** GitHub currently reports `leaf-error-cost` as the default branch, but that branch is **1 commit ahead and 1,375 commits behind `master`**. Its reviewed head is `1a99bfd9ff2819f428c8c935857f56b5f9fec2c2`; `master` was `867aa6d14418163560bf89f12c48107098b1ec8f` at roadmap review. Therefore validation must **never blindly follow the repository default branch**. Use an explicitly named `master` (or intentionally selected release) branch and pin the exact commit SHA for every evidence run.

At the reviewed `master` state, Synaptreesitter is a substantial mixed Rust/C/web Cargo workspace:

- 9 explicit workspace members: `crates/cli`, `crates/config`, `crates/generate`, `crates/highlight`, `crates/loader`, `crates/tags`, `crates/xtask`, `crates/language`, and `lib`.
- `default-members = ["crates/cli"]`.
- Rust edition **2024**.
- Workspace `rust-version` **1.90**.
- Workspace version **0.28.0**.
- A committed `Cargo.lock`.
- No root `rust-toolchain` or `rust-toolchain.toml` at the reviewed commit.
- Workspace-level Clippy policy in `Cargo.toml`.
- Authoritative Rust lint entry point: `make lint`.
- Authoritative host-native test entry point: `make test`.
- Multiple custom `[lib].path` values rather than conventional `src/lib.rs`.
- `crates/cli` has library + binary + benchmark targets.
- `lib` has `build = "binding_rust/build.rs"` and compiles native C via `cc`.
- `crates/language` also has a build script.
- CLI tests use a path-based proc-macro dev dependency.
- Workspace feature paths include WASM.
- The repo contains substantial non-Rust code, making it a useful mixed-language auto-detection test.

These observed shapes make Synaptreesitter the primary brownfield target for Cargo metadata fidelity, target-aware identity, custom source paths, workspace dependency impact, build-script boundaries, feature awareness, mixed-language profiling, and false-green detection.

The principal Rust-specific problem is not implicit interface matching as it was for Go. `impl Trait for Type` makes ordinary trait implementation explicit.

The real design problems are:

- Cargo **package vs target/crate identity**.
- Rust's **semantic module tree**, which is not equivalent to the filesystem.
- Name resolution across `use`, aliases, re-exports and explicit paths.
- Multiple implementations with the same method name.
- Configuration-dependent source through `cfg`.
- Macro-generated declarations and generated source.
- Choosing the correct Cargo package(s) to build/test after a generated change.

---

# Delivery scope decision — fixed before kickoff

**Decision: the initial funded/committed delivery is Core Rust support only: P0-P10.**

P11-P14 are a separately estimated **Extensive Rust hardening** tranche and are **not part of the initial kickoff scope, staffing commitment, or release date**. They may start only after P10 is DONE and a separate go/no-go review approves the additional investment.

This is deliberate:

- P0-P5 delivers release-worthy Rust comprehension.
- P6-P10 delivers Cargo-aware Rust codegen/build/test for supported host environments.
- P11-P14 broadens ecosystem evidence (async, proc-macro boundary, `no_std`, non-default features) but does not change the core architecture required to ship Rust.
- R5 rust-analyzer enrichment and R6 specialized embedded/cross execution remain later tracks and are not pulled into P11-P14 implicitly.

**Release language is therefore fixed up front:**

- after P1-P5: **Rust comprehension**;
- after P6-P10: **Core Rust support — comprehension + Cargo-aware codegen/build/test**;
- only after separately approved P11-P14: **Extensive Rust support**.

If P11-P14 are not funded, the project still has a complete, releasable Core Rust outcome rather than an unfinished “extensive” track.

## Staffing envelope

Planning unit: **engineer-days (ED)**, assuming one engineer familiar with Spine's language-front-end and SDLC registries. Review/CI wait time is not counted as hands-on ED. Parallelism is limited because P1→P2 and P6→P7 are dependency chains.

| Delivery tranche | Estimated effort | Staffing interpretation |
|---|---:|---|
| **Part A — comprehension (P0-P5)** | **18-24 ED** | ~4-5 engineer-weeks |
| **Part B — codegen (P6-P10)** | **17-24 ED** | ~3.5-5 engineer-weeks |
| **Core total (P0-P10)** | **35-48 ED** | ~7-10 engineer-weeks for one primary engineer |
| **P2X cross-module CALLS extension** | **0-3 ED time-box** | Included only if it clears the stop/go gate; otherwise deferred |
| **Extensive tranche (P11-P14)** | **17-24 ED** | Separate approval; ~3.5-5 additional engineer-weeks |

For scheduling, use **Core = 35-48 ED** as the committed estimate. Do not put P11-P14 on the same critical path unless the scope decision is explicitly reopened.

---

# 0. Pre-implementation proof

Before writing `rust_extractor.py`, perform two bounded spikes.

## P0-A — grammar census

Run:

```bash
python scripts/parse-census.py tree_sitter_rust <validation-repo>
```

against both validation repositories.

The census must explicitly include:

- structs, tuple structs, unions and enums;
- traits and trait default methods;
- inherent `impl`;
- `impl Trait for Type`;
- generic impls;
- blanket impls;
- associated functions;
- `Self::`;
- fully qualified paths;
- `use` trees;
- aliases and `pub use`;
- inline modules;
- outlined modules;
- `#[path]`;
- `cfg`/`cfg_attr`;
- macro invocations;
- async functions;
- Rust 2021 and 2024 syntax represented in the validation set.

No parser version is committed until this passes.

## P0-B — Cargo/module identity prototype

Before corpus labels are written, build a tiny prototype proving the ID scheme for:

1. package with `src/lib.rs`;
2. package with `src/main.rs`;
3. package containing both lib and bin targets;
4. package with two `[[bin]]` targets;
5. workspace with multiple packages;
6. inline `mod`;
7. `mod foo;`;
8. `foo.rs` vs `foo/mod.rs`;
9. `#[path = "..."]`;
10. integration-test crate under `tests/`.

This is the load-bearing design proof. Do not allow P1 to start until these cases produce unique, stable identities.

---

# 1. Decisions

| # | Decision | Recommendation |
|---|---|---|
| **D1** | Parser | Use `tree-sitter-rust`, lazy-loaded exactly like other tree-sitter front-ends. Run `parse-census.py` before implementation. Pin the **validated minimum**, not an assumed `>=0.21`. |
| **D2** | Module model | **`Module` = semantic Rust module, not file.** A source file is provenance. Inline `mod x {}` creates a Module even though no file exists; outlined `mod x;` links the same semantic Module to its source file. |
| **D3** | Crate/target identity | Every Rust ID must be scoped by **Cargo package + Cargo target**, because `lib.rs`, `main.rs`, multiple binaries and integration tests are independent crates and can otherwise collide. Preserve `::` for the semantic Rust path after that scope. |
| **D4** | Cargo discovery | Build a deterministic, stdlib-only Cargo index with `tomllib` for comprehension. Understand `[workspace]`, `members`, `exclude`, globs, path dependencies, `[package]`, `[lib]`, `[[bin]]`, conventional targets and workspace-inherited edition/MSRV. **Comprehension must not require Cargo to be installed.** |
| **D5** | `IMPLEMENTS` | Read concrete `impl Trait for Type` directly. No Go-style method-set inference. Emit only when the self type has a concrete named constructor/path. Skip pure blanket targets such as `impl<T> Trait for T`, negative impls and forms that cannot identify one type without inference. |
| **D6** | Method identity | Inherent and trait-implementation methods must have distinct IDs. `impl Foo { fn run }` and `impl Trait for Foo { fn run }` are different functions and must never collapse onto one `Foo::run` node. |
| **D7** | `CALLS` | **Core guarantee:** resolve only precision-safe local cases that do not require a new whole-workspace symbol resolver: same semantic-module free functions (after lexical-shadow checks), `self.method()` inside a concrete inherent impl, and `Self::`/`Type::` associated calls where the target is locally exact. **Cross-module/workspace CALLS (`use`-bound functions, `crate::`/`self::`/`super::` paths that leave the current module, workspace-crate-qualified calls) are a time-boxed P2X extension, not a P2 release blocker.** P2X gets at most 3 ED; if a reusable exact symbol index is not proven at precision 1.00 inside that budget, record the shapes as `known_gaps` and defer them to post-Core semantic work. Receiver/type-inference cases remain deferred regardless. |
| **D8** | `use` / re-export resolution | Parse nested use trees, aliases, `self`, `super`, `crate`, workspace crate aliases, and `pub use`. Exact re-exports should repoint to the defining symbol. Glob imports resolve only when the exported set and binding are unambiguous. |
| **D9** | Macros/generated code | No macro expansion. Macro-generated declarations, derive-generated impls and proc-macro output are a permanent source-level recall ceiling. `include!` is also opaque in v1, even when literal. |
| **D10** | Conditional compilation | Build a **configuration-agnostic source graph**: recognize declarations under `cfg` but do not evaluate target/features during comprehension. When mutually exclusive cfg variants create the same canonical symbol ID, keep one canonical node but suppress call-resolution that would depend on choosing a variant. Record the ambiguity. |
| **D11** | Source admission | In a Cargo project, extract `.rs` files that belong to a discovered Cargo target/module tree. Do not automatically treat every random `.rs` fixture as compiled source. With no Cargo manifest, fall back to standalone-file comprehension. |
| **D12** | Types/fields | Include structs, tuple structs, enums, unions, traits and type aliases as `Type`; module/associated `const` and `static` as `Field`; real struct/tuple fields as `Field`; enum variants as named `Field` members. Do not invent synthetic source entities. |
| **D13** | Framework scope | Base Rust support includes stack detection only. Axum/Actix/Rocket `Endpoint`/`EXPOSES` extraction is a follow-on enrichment phase unless explicitly added to this track. Tokio is a runtime, **not** the value of `ProjectProfile.framework`. |
| **D14** | Codegen execution boundary | Comprehension never executes repository code. Codegen may run Cargo and therefore may execute `build.rs`; that happens only inside the existing governed SDLC execution path and its write/tool guards. |
| **D15** | Codegen verification scope | Test changed workspace packages **and affected reverse workspace dependents**. A workspace/root manifest, lockfile, shared `.cargo` config or toolchain change escalates to workspace-level verification. |
| **D16** | Rust preflight | Reuse the existing `make_preflight_runner(language)` infrastructure. Add `RustPreflightRunner`; do not rebuild the dispatcher. Rustfmt is the canonical formatting check. Clippy runs when available/configured without injecting a new `-D warnings` policy the repository did not already have. |

---

# 2. Canonical identity

## 2.1 Why the original file-based mapping changes

Rust's source relationship is:

```text
Cargo package
  └─ Cargo target / crate
       └─ semantic module
            ├─ semantic child module
            ├─ Type
            └─ Function
```

not:

```text
directory
  └─ file
       └─ declarations
```

A file may contain several modules, and a module may be loaded through a declaration whose physical file does not directly encode its logical namespace.

## 2.2 ID scheme

Exact punctuation should be pinned by P0-B, but the identity model must be equivalent to:

```text
rust:<package>@<target-kind>/<target-name>::<module>::<symbol>
```

Examples:

```text
rust:billing@lib/billing::service::Invoice
rust:billing@lib/billing::service::Invoice::new
rust:billing@bin/server::http::main
rust:billing@test/api::helpers::fixture
```

Trait implementation method example:

```text
rust:billing@lib/billing::service::Invoice::<Display>::fmt
```

versus the distinct inherent method:

```text
rust:billing@lib/billing::service::Invoice::fmt
```

The exact trait-implementation separator may change during P0-B, but the collision must not.

---

# 3. Cargo workspace and target index

Add one shared, stdlib-only Rust/Cargo helper rather than rediscovering Cargo structure independently in comprehension, profiling and codegen.

Suggested module:

```text
pkg/rust_cargo.py
```

Responsibilities:

- locate Cargo workspace root;
- parse all relevant `Cargo.toml` files with `tomllib`;
- expand workspace `members` globs;
- apply `exclude`;
- include path dependencies that are workspace members;
- identify virtual workspaces;
- resolve package names;
- resolve workspace-inherited `edition` and `rust-version`;
- identify library targets, including custom `[lib].path` values;
- identify main/default binaries and explicit `[[bin]]` targets;
- identify `[[bench]]`, examples and tests where declared;
- recognize conventional `src/bin/*`;
- identify `build.rs` / custom package `build = "..."` as build-script targets;
- identify conventional integration-test crate roots;
- record normal, dev and build path dependencies, including nested path crates used only by tests;
- record workspace `default-members` separately from all workspace members;
- record dependency aliases and package names for workspace path dependencies;
- record feature definitions/default features without activating them during comprehension;
- provide source-file → package → target mapping.

**Synaptreesitter acceptance probes for this index:** locate all 9 reviewed `master` workspace members, map `crates/cli/src/tree_sitter_cli.rs` and `lib/binding_rust/lib.rs` correctly despite their non-default paths, identify the CLI lib + bin + bench targets, recognize native build scripts, and distinguish `default-members` from the full workspace.

For codegen, where Cargo is already required, compare this static interpretation with:

```bash
cargo metadata --format-version 1 --no-deps
```

during tests. A disagreement is a test failure in Spine's Cargo model, not something to silently ignore.

---

# 4. Rust module index

`RustExtractor.module_name()` cannot derive identity from the current filename alone.

The extractor therefore maintains a root-scoped module index built before IDs are emitted.

It resolves:

```text
crate root
mod foo;
mod foo { ... }
foo.rs
foo/mod.rs
nested modules
#[path = "..."]
```

The preferred implementation is a Rust-specific pre-index initialized lazily on first use for a repository, or a small generic `prepare(root, paths)` lifecycle hook if adding that hook proves cleaner and does not disturb existing front-ends.

Do **not** emit file-path IDs and attempt to rename the graph in `finalize`; by then every child symbol and edge already embeds the wrong identity.

Nested semantic modules use `CONTAINS`:

```text
Module → Module
Module → Type
Module → Function
Type   → Function
Type   → Field
```

Before adopting Module→Module, add a compatibility test over every consumer that traverses `CONTAINS`; no consumer may assume the destination can only be a symbol.

---

# 5. Fact mapping

## 5.1 Declarations

| Rust construct | PKG fact |
|---|---|
| Cargo target/crate root | `Module` |
| `mod foo {}` | child `Module` |
| `mod foo;` | child `Module`, resolved to the declared source file |
| `struct` | `Type` |
| tuple struct | `Type` + ordinal `Field`s (`0`, `1`, …) where retained |
| `enum` | `Type` |
| enum variant | `Field` owned by enum |
| named variant payload fields | `Field` using stable `Variant::field` identity if represented |
| `union` | `Type` |
| `trait` | `Type` |
| `type Alias = ...` | `Type` |
| free `fn` | `Function` |
| inherent impl method | `Function` owned by concrete Type |
| trait impl method | distinct trait-qualified `Function` owned by concrete Type |
| trait required/default method | `Function` owned by trait Type |
| module/associated `const` | `Field` |
| module/associated `static` | `Field` |

Associated types may be added as `Type` only after corpus examples show that this representation improves rather than distorts the graph.

## 5.2 Structural edges

| Construct | Edge |
|---|---|
| parent module → child module | `CONTAINS` |
| module → declaration | `CONTAINS` |
| type → method/field | `CONTAINS` |
| trait impl | `IMPLEMENTS` |
| supertrait (`trait Child: Parent`) | `IMPLEMENTS` from Child → Parent |
| exact `use` / `pub use` | `IMPORTS` |
| same-crate field type reference | `REFERENCES` |

For generic field types:

```rust
Option<Box<Customer>>
```

the containers are not themselves emitted as first-party references, but the extractor may recursively peel known wrappers and emit:

```text
Field/owner → Customer
```

when `Customer` resolves unambiguously inside the crate/workspace.

---

# 6. `IMPLEMENTS`

Resolve directly:

```rust
impl Display for Invoice
impl<T> Repository<T> for SqlRepository<T>
unsafe impl Send for Worker
```

when both paths can be normalized to concrete type constructors.

Do not infer structural satisfaction.

Do not emit a concrete `IMPLEMENTS` fact for:

```rust
impl<T> Trait for T
```

because `T` is a type parameter rather than one concrete Type node.

Do not invert a negative impl into a positive fact:

```rust
impl !Send for Something
```

Derive-generated implementations remain absent:

```rust
#[derive(Clone)]
struct X;
```

does not produce an `IMPLEMENTS Clone` edge without macro expansion.

Never manufacture a grounded Type merely because an impl block mentions it. If the concrete type definition is outside the scanned graph, represent the owner as an **external Type placeholder** and keep the impl method itself grounded at its real source location.

---

# 7. Name resolution and `CALLS`

## 7.1 Core P2 `CALLS` — guaranteed scope

These shapes are required for the Core comprehension release because they can be resolved from local semantic context without building a new whole-crate/workspace symbol service:

| Call shape | Core resolution |
|---|---|
| `foo()` | Same semantic-module free function, after lexical-shadow check |
| `self.method()` inside a concrete inherent `impl` | Unique method on that concrete type within the locally known impl/type table |
| `Self::new()` | Unique associated function in the applicable concrete impl scope |
| `Type::new()` | Unique associated function when `Type` resolves locally and exactly |
| `<Type as Trait>::method()` | Resolve only when Type, Trait and implementation/default method are already exact in the local/collected impl tables; otherwise defer |

`IMPORTS` / `pub use` facts still land in Core P2. Their existence does **not** require `CALLS` through those bindings to resolve in Core.

## 7.2 P2X — time-boxed cross-module/workspace `CALLS` extension

Rust's static paths make these attractive, but no shipped Spine front-end currently proves a general whole-crate/workspace call resolver. Treat this as an optimization opportunity, not a hidden prerequisite.

Candidate shapes:

| Call shape | P2X target |
|---|---|
| imported `foo()` after `use crate::x::foo;` | Exact defining function through import/export index |
| aliased `run()` after `use crate::x::foo as run;` | Exact defining function through alias table |
| `crate::x::foo()` | Exact crate/module function |
| `self::foo()` / `super::foo()` leaving current module | Exact semantic-module path |
| `workspace_crate::x::foo()` | Exact workspace function when dependency alias/package mapping is known |

### P2X budget and stop/go gate

**Budget: maximum 3 engineer-days.**

Proceed only if the implementation can reuse the Cargo/module/import indexes already built for P1/P2 and demonstrate all of the following inside the time-box:

1. deterministic resolution independent of file-walk order;
2. no new guessed IDs;
3. precision **1.00** on dedicated corpus cases;
4. no regression to existing language import linking;
5. bounded lookup cost (index-based, not repeated O(nodes²) scans);
6. a design that is reusable rather than Rust-repository-specific.

**Fallback:** if any condition is not met by the end of the 3-ED budget, stop. Ship Core comprehension with these shapes explicitly listed as `known_gaps`; do not extend P2. The roadmap then moves to P3 on schedule. A later semantic-resolution track may revisit them, potentially with `rust-analyzer`.

The fallback is considered a successful P2 outcome, not a phase failure.

## 7.3 Explicitly deferred regardless of P2X

Do not guess:

```rust
obj.method()
```

when the receiver requires type inference.

Also defer:

- autoderef/autoref method selection;
- trait-object dispatch through `dyn Trait`;
- generic receiver dispatch;
- trait method lookup requiring inferred bounds;
- function pointer/closure targets;
- call operators implemented through traits;
- ambiguous glob imports;
- calls whose candidate declaration is cfg-ambiguous.

## 7.4 Lexical shadowing

A bare function name is not enough.

Example:

```rust
fn run() {}

fn f() {
    let run = || {};
    run();
}
```

The second call must **not** emit `CALLS → fn run`.

Rust needs a scope walker or equivalent local-binding table covering at least:

- parameters;
- `let` bindings;
- closure parameters;
- match bindings;
- loop bindings;
- nested blocks.

This also satisfies the `pkg/scope.py` language-registration obligation instead of reporting an unmeasured clean zero.

---

# 8. Imports and re-exports

Handle:

```rust
use crate::foo::Bar;
use crate::foo::Bar as B;
use crate::foo::{Bar, baz};
use crate::foo::{self, baz};
use super::foo;
use self::foo;
pub use crate::foo::Bar;
extern crate old_name as new_name;
```

Rules:

1. Exact source paths are normalized against the semantic module tree.
2. Workspace dependency aliases come from Cargo manifests.
3. Third-party crates remain external.
4. `pub use` participates in a crate-level export map so imports through a public façade can land on the defining symbol.
5. A glob import may point to its source module with `IMPORTS`, but a symbol-level binding/CALLS resolution is emitted only when the export set proves one target.
6. No environment-dependent or feature-dependent guess.

---

# 9. Conditional compilation

Rust source is heavily conditioned:

```rust
#[cfg(unix)]
fn platform() {}

#[cfg(windows)]
fn platform() {}
```

Initial Rust comprehension is deliberately **configuration agnostic**.

It records syntax that exists in the repository but does not claim to represent one selected Cargo feature/target build.

Rules:

- retain cfg-gated declarations as source facts;
- do not evaluate target triples or Cargo features;
- do not run `rustc --print cfg` during comprehension;
- canonical duplicate symbol IDs are allowed to coalesce;
- when multiple cfg variants define the same callable symbol, suppress resolution of calls whose target would require choosing one variant;
- record the ambiguity as a measured known gap.

Add a corpus case specifically for this.

---

# 10. Macro and generated-code boundary

No macro expansion.

That excludes facts generated by:

- `macro_rules!`;
- function-like procedural macros;
- derive macros;
- attribute procedural macros;
- `include!`.

Important distinction:

```rust
#[tokio::main]
async fn main() {
    foo();
}
```

The attribute macro's generated transformation is invisible, but the source-level `foo()` inside the visible function body can still be extracted if the CST exposes that body normally.

Build-script output is also not part of comprehension.

`build.rs` itself **is** Rust source and should be modeled as a Cargo custom-build target, but files generated into `OUT_DIR` are not walked.

`target/` becomes a global ignored directory.

---

# 11. Part A phases — comprehension

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---:|---|---|---|---|---|
| **P0 Grammar + identity proof** | Parser census plus Cargo-target/module-ID prototype; pin Synaptreesitter baseline | **2-3 ED** | Grammar ceiling measured; lib/bin/test/module cases have non-colliding IDs | ⬜ | | | |
| **P1 Cargo/module index + core extraction** | `rust_cargo.py`; `rust_extractor.py`; semantic Module tree; Type/Field/Function; `CONTAINS`; target-aware source admission; `target/` ignore; all mandatory front-end registrations | **5-7 ED** | Validation repos go from zero Rust nodes to valid graphs; every Cargo member/target expected by hand inspection appears once; `pkg verify` 0 errors | ⬜ | | | |
| **P2 Core imports + local `CALLS` + `IMPLEMENTS`** | `use` trees/re-exports as graph facts; lexical shadowing; **local guaranteed CALLS only per §7.1**; inherent/trait method IDs; direct trait impl; supertraits; cfg ambiguity suppression | **4-5 ED** | Precision 1.00 for emitted CALLS/IMPLEMENTS in corpus; no method-ID collisions; no shadowed-call false positive; cross-module CALLS may remain documented gaps | ⬜ | | | |
| **P2X Cross-module/workspace `CALLS` experiment** *(non-blocking)* | Attempt exact import/path-qualified CALLS using P1/P2 indexes; stop at gate in §7.2 | **0-3 ED max** | Either precision-1.00 indexed resolver lands **or** evidence records deferral; **P3 starts either way** | ⬜ | | | |
| **P3 Corpus + accuracy hardening** | Rust corpus described below; scoreboard; known gaps predicted before scoring | **3-4 ED** | 1.00 precision for every emitted node/edge kind; recall measured and written down | ⬜ | | | |
| **P4 Profiling/detection** | `.rs`; bounded Cargo manifest reading; `cargo` test runner; framework detection from member manifests | **2 ED** | Rust profiles correctly in single package + virtual workspace; `test_runner="cargo"`; axum/actix/rocket detection works without calling Tokio a web framework | ⬜ | | | |
| **P5 Consumer integration + docs + review** | generic grounding fence fix; documentation/visibility/source-extension registration; all user docs/checklists; `/review-pr` | **2-3 ED** | Full docs audit/state-number checks green; Rust snippet renders as `rust`; comprehension PR mergeable | ⬜ | | | |

Implementation status and measured exceptions are maintained in the [Rust comprehension validation record](../evals/rust-comprehension-validation.md). The phase cells above remain open until every exit criterion has been independently verified; implementation work alone does not satisfy a release gate.

---

# 12. Corpus

Minimum cases:

| Case | Required coverage |
|---|---|
| `module-tree` | inline module, outlined module, nested module, `foo.rs`, `foo/mod.rs`, `#[path]` |
| `multi-target` | one package with lib + main bin + second bin; proves IDs never collide |
| `trait-impl` | inherent method and same-name trait method, default method, overridden method |
| `generic-impl` | `impl<T> Trait for Foo<T>` resolves; `impl<T> Trait for T` does not invent a concrete Type |
| `use-reexport` | grouped use, alias, `pub use`, call through re-export |
| `qualified-calls` | `crate::`, `self::`, `super::`, workspace dependency path |
| `shadowing` | local/parameter/closure binding shadows same-named free function |
| `workspace-multi-crate` | 3+ members, path dependency, dependency alias |
| `cfg-variants` | mutually exclusive declarations with same canonical path; no invented CALLS |
| `macro-boundary` | declaration generated by macro absent; call inside token-tree macro argument unresolved |
| `generated-include` | `include!(...)` does not silently pretend generated declarations were parsed |
| `external-impl` | local trait implemented for concrete external type; external owner stays external |

Labels are written from source before extractor output is inspected.

---

# 13. Profiling

Add:

```text
.rs -> rust
Cargo.toml -> Rust/Cargo marker
```

Do not only read the repository-root `Cargo.toml`; virtual workspaces often keep meaningful dependencies in member manifests.

Reuse the Cargo workspace index or add a bounded Cargo-manifest reader analogous to the existing .NET project scan.

Framework detection:

```text
axum       -> axum
actix-web  -> actix
rocket     -> rocket
```

Do **not** classify Tokio as the framework. Tokio may be recorded later as runtime/platform metadata if the profile grows such a field.

Rust test runner:

```text
Cargo project + Rust -> cargo
```

Framework detection is not endpoint extraction.

---

# 14. Mandatory registration sites

Part A must cover the entire current front-end checklist, not only the originally listed files.

At minimum:

```text
pkg/rust_extractor.py
pkg/rust_cargo.py
pkg/extractor.py
pkg/capabilities.py
pkg/persistence.py
doctor.py
catalog/profile.py
pkg/scope.py
pkg/import_link.py          # implementation or explicit documented no-op reason
pkg/docs.py
pkg/doc_link.py
knowledge/insights.py
pyproject.toml
uv.lock
.github/workflows/ci.yml
```

Tests:

```text
tests/pkg/test_rust_extractor.py
tests/pkg/test_default_extractors.py
tests/pkg/test_capabilities.py
tests/pkg/test_verifier.py
tests/pkg/test_persistence.py
tests/pkg/test_scope.py
tests/catalog/test_profile.py
tests/test_doctor.py
```

Plus corpus and documentation checks.

User-facing documentation obligations include the full current docs matrix rather than only the language roadmap:

```text
README.md
USER_GUIDE.md
KNOWLEDGE_GRAPH.md
AGENT_GUIDE.md
CLI_REFERENCE.md
EXAMPLE.md
BENCHMARK.md
SETUP.md
CHANGELOG.md
corpus/README.md
docs/specs/STATE-OF-SPINE.md
docs/specs/language-expansion-roadmap.md
docs/specs/SPEC-INDEX.md
plugin skill documentation
architecture SVG/generated counts where required
```

Run the repository's docs/state audit scripts rather than manually updating a remembered count.

---

# 15. Shared work

## 15.1 Grounding fence

The existing special case:

```python
"perl" if symbol.id.startswith("perl:") else "python"
```

must become a shared language → Markdown-fence mapping based primarily on:

```text
node.language
```

with an ID-prefix fallback only for legacy data.

Rust then renders:

````markdown
```rust
...
```
````

This is generic work benefiting every non-Python front-end.

## 15.2 Preflight

The generic preflight dispatcher already exists in `v3.52.0`.

Do not implement another `make_preflight_runner(language)`.

Part B only adds:

```text
RustPreflightRunner
```

and registers it in the Rust `Toolchain`.

---

# 16. Packaging

Use a Rust parser extra:

```toml
rust = [
    "tree-sitter>=<current compatible minimum>",
    "tree-sitter-rust>=<validated minimum>",
]
```

Do not freeze the grammar minimum until P0's census and ABI test.

At planning time the current `tree-sitter-rust` release should be tested directly rather than assuming the much older `0.21` floor.

Also:

- add Rust to `languages`;
- add `tree_sitter_rust` to the mypy optional-import override if required;
- add the extra to CI parser installation;
- add `doctor.EXTRA_PROBES`;
- add grammar fingerprinting;
- regenerate `uv.lock`.

Parser tests belong in the ordinary Python CI job.

Real Cargo integration tests should use a dedicated Rust-capable job when necessary rather than making every Python-only CI job install the full compiler stack.

Do not introduce the obsolete `actions-rs/toolchain` pattern. Use a currently maintained Rust-toolchain action or the runner's supported Rust setup.

---

# 17. Part B — codegen

## P6 Toolchain, layout, scaffold, preflight

Implement:

```text
CargoToolEnvironment
cargo_toolchain_available()
_resolve_rust_layout()
_rust_files()
RustPreflightRunner
TOOLCHAINS["rust"]
```

`cargo_toolchain_available()` checks the commands actually needed, not only one executable.

Brownfield layout records:

```text
workspace root
package root
package name
target kind
target name
target source root
edition
rust-version/MSRV
source directory
tests arrangement
Cargo.lock presence
rust-toolchain / rust-toolchain.toml presence
```

For existing projects, `prefer_paths` should select the Cargo package **and target containing the designed landing site**, not merely the nearest `Cargo.toml`.

Where Cargo is available, verify layout with:

```bash
cargo metadata --format-version 1 --no-deps
```

### Greenfield

Default to one minimal crate.

Choose library vs binary from the feature intent/layout decision rather than always producing one shape.

Scaffold only what is required:

```text
Cargo.toml
src/lib.rs
```

or:

```text
Cargo.toml
src/main.rs
```

plus:

```text
README.md
.gitignore
```

Do not invent third-party dependencies in the empty scaffold.

Scaffold must be idempotent.

---

# 18. Rust preflight

`RustPreflightRunner` uses the existing Toolchain-driven preflight dispatch.

Baseline behavior:

### Formatting

Run the repository-equivalent Rustfmt check, normally:

```bash
cargo fmt --check
```

scoped to the affected package/workspace where supported.

Rustfmt availability is an explicit toolchain requirement for Rust codegen rather than an invisible pass.

### Clippy

Run:

```bash
cargo clippy ...
```

when Clippy is available.

Do **not** add:

```text
-D warnings
```

unless that is already the repository's policy.

If the repository explicitly configures Clippy (`clippy.toml`, workspace lint configuration, CI contract) but Clippy is missing, return an actionable failure.

If the repository does not configure/require Clippy and the component is unavailable, report an explicit skip rather than “clean”.

---

# 19. Test runner

Implement `CargoTestRunner`.

The original "changed crate only" policy is insufficient for a workspace.

If:

```text
app -> core
```

and `core` changes, testing only `core` can miss a source-incompatible break in `app`.

Determine:

```text
changed package(s)
+
reverse workspace dependents
```

for workspace path dependencies.

Policy:

| Change | Verification scope |
|---|---|
| ordinary `.rs` file in leaf crate | owning package |
| `.rs` in shared crate | owning package + reverse workspace dependents |
| package `Cargo.toml` | owning package + dependents |
| workspace `Cargo.toml` | workspace |
| `Cargo.lock` | workspace |
| `.cargo/config*` | workspace |
| `rust-toolchain*` | workspace |
| ambiguous ownership | workspace |

Use package targeting where possible:

```bash
cargo build -p <package>
cargo test -p <package>
```

or workspace equivalents.

A build is not a test pass.

An empty/no-test result must not be presented as equivalent to executing a meaningful generated test suite.

---

# 20. Dependency and lockfile policy

Brownfield codegen should prefer existing dependencies and the standard library.

If generated code does not change dependency declarations and a committed `Cargo.lock` exists, final verification should be reproducible against that lock.

If codegen intentionally changes `Cargo.toml` dependencies:

1. allow Cargo to resolve/update the lock under the existing governed execution environment;
2. include the resulting `Cargo.lock` change in the generated diff where appropriate;
3. perform the final verification with the updated lock fixed.

Do not run a redundant unconditional `cargo fetch` before every build.

Comprehension never fetches dependencies.

---

# 21. Rust toolchain/version behavior

Respect:

```text
rust-toolchain
rust-toolchain.toml
Cargo.toml edition
Cargo.toml rust-version
workspace.package.edition
workspace.package.rust-version
```

Prompt/guidance must tell codegen the actual edition/MSRV.

This prevents generating valid-current-Rust code that does not compile under the repository's declared minimum compiler.

Do not create or modify `rust-toolchain.toml` in greenfield unless explicitly part of the selected scaffold policy.

---

# 22. P7–P10

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---:|---|---|---|---|---|
| **P6 Toolchain/layout/scaffold/preflight** | Environment, Cargo-aware layout, scaffold, Rust preflight, complete `TOOLCHAINS["rust"]` row | **5-7 ED** | Greenfield scaffold builds; brownfield resolves correct package+target; `--language rust` becomes valid only now; idempotency green | ⬜ | | | |
| **P7 Runner + affected-package graph** | Cargo build/test runner, reverse workspace dependent selection | **4-5 ED** | Changing shared crate tests its dependents; root manifest change tests workspace; deliberately broken code produces red result | ⬜ | | | |
| **P8 Prompts/guidance/skill** | implement/tests/refine prompts; Rust layout guidance; rust-conventions skill/catalog entry | **2-3 ED** | Rust skill selected; generated code obeys edition/layout; idiomatic error handling and tests | ⬜ | | | |
| **P9 Live proof** | greenfield plus Synaptreesitter targeted + reverse-dependent brownfield runs; compile-red/test-red; clean rerun | **5-7 ED** | Native baseline and Spine-generated runs green; deliberate reds caught; affected-package selection correct; clean rerun reproduces result | ⬜ | | | |
| **P10 Review + docs + MR** | `/review-pr`, full CI/docs gates, evidence table | **1-2 ED** | mergeable; no stale counts/docs; no unrelated graph changes | ⬜ | | | |

---

# 23. Prompts and Rust guidance

Rust codegen guidance should include:

- match the repository's edition and MSRV;
- match its existing module/file organization;
- prefer existing dependencies;
- use `Result`/`Option` appropriately;
- avoid panic/`unwrap()` for recoverable production errors;
- `unwrap()`/`expect()` remain acceptable where neighboring tests or proven invariants make them idiomatic;
- follow ownership/borrowing patterns in neighboring code rather than cloning reflexively;
- preserve async runtime already in use;
- do not introduce unsafe code unless explicitly required and supported by surrounding code;
- use co-located `#[cfg(test)] mod tests` or integration tests according to the repository's existing convention;
- never create an unrelated second Cargo package just to land a feature;
- preserve feature gates;
- edit `Cargo.toml` only when a dependency/config change is actually necessary.

Add:

```text
rust-conventions
```

to both the capability catalog and native skill set.

---

# 24. Validation targets

The synthetic Rust corpus remains the precision oracle. The **primary public brownfield validation target is `synaptixs/Synaptreesitter` on an explicitly pinned `master` commit**.

Repository: `https://github.com/synaptixs/Synaptreesitter`

## Target A — Synaptreesitter `master` (mandatory)

### A.0 Explicit branch selection

Do not use an implicit default-branch clone while GitHub still reports `leaf-error-cost` as the default.

At roadmap review:

```text
default branch:        leaf-error-cost
leaf-error-cost HEAD:  1a99bfd9ff2819f428c8c935857f56b5f9fec2c2
master HEAD:           867aa6d14418163560bf89f12c48107098b1ec8f
relationship:          leaf-error-cost is 1 ahead / 1375 behind master
```

Use:

```bash
git clone --depth 1 --branch master https://github.com/synaptixs/Synaptreesitter.git
cd Synaptreesitter
git rev-parse HEAD
```

Refresh and pin the exact `master` SHA at execution time. The reviewed SHA above documents this plan review; it is not a moving target.

**Repository-maintenance note:** if `leaf-error-cost` is not intentionally the desired default checkout, change the GitHub default branch to `master` or the intended current release branch. The Spine test harness remains explicit even after that is corrected.

### A.1 Observed workspace profile

The reviewed `master` contains:

```text
default member: crates/cli

workspace members:
  crates/cli
  crates/config
  crates/generate
  crates/highlight
  crates/loader
  crates/tags
  crates/xtask
  crates/language
  lib

edition:      2024
rust-version: 1.90
version:      0.28.0
Cargo.lock:   committed
root Rust toolchain file: none observed
```

Required real-world shapes include:

- custom library source paths;
- CLI lib + bin + benchmark targets;
- internal path dependencies;
- nested test/proc-macro dependency;
- build scripts;
- native C compilation from Rust build logic;
- feature-gated WASM;
- workspace lints;
- mixed Rust/C/web source.

### A.2 Cargo metadata cross-check

Run:

```bash
cargo metadata --format-version 1 --locked --no-deps > /tmp/synaptreesitter-metadata.json
```

Compare Cargo's result with Spine's stdlib-only `rust_cargo.py` model.

Required invariant:

> Spine may omit resolver details that require Cargo, but it must not disagree with Cargo about package membership, default members, target source paths, target kinds, package names, or source-file ownership.

Record:

| Item | Spine static model | Cargo metadata | Match |
|---|---:|---:|---|
| workspace members | | | |
| default members | | | |
| custom lib targets | | | |
| bin targets | | | |
| bench targets | | | |
| build scripts | | | |
| internal path dependencies | | | |

### A.3 Rust-2024 parser census

Before implementing the extractor:

```bash
python scripts/parse-census.py tree_sitter_rust /path/to/Synaptreesitter
```

Because the reviewed workspace declares Rust 2024, this is the mandatory real-world D1 grammar proof.

Record:

- `.rs` files parsed;
- files with CST `ERROR`;
- lines inside error spans;
- declarations by CST kind;
- trait/generic surface;
- macro/attribute surface;
- cfg surface.

A grammar that cannot cleanly parse the pinned Synaptreesitter Rust source does not pass D1 even if synthetic fixtures succeed.

### A.4 Comprehension validation

Before P1, capture the current no-Rust-front-end baseline. The validation path must explicitly pin `master`/commit instead of following the repository default branch.

After P1-P5, against the same SHA, record:

- files admitted/skipped;
- source file → Cargo package/target ownership;
- semantic Module count;
- Type/Function/Field counts;
- `IMPORTS`, `CONTAINS`, `CALLS`, `IMPLEMENTS`, `REFERENCES`;
- unresolved calls/imports;
- parser census;
- `pkg verify`;
- sampled manual precision.

Mandatory hand-checks:

1. `crates/cli/src/tree_sitter_cli.rs` mapped as the CLI library target.
2. `crates/cli/src/main.rs` mapped as a distinct binary target.
3. `lib/binding_rust/lib.rs` mapped to package `tree-sitter`.
4. `lib/binding_rust/build.rs` recognized as build-script target.
5. One custom-path crate such as `crates/config`.
6. One generic symbol.
7. One trait implementation.
8. One re-export/import path.
9. One cfg-gated item.
10. One macro/derive boundary.
11. One cross-crate internal dependency.
12. The nested test/proc-macro dependency as reported by Cargo metadata.

### A.5 Mixed-language auto-detection

Synaptreesitter contains meaningful Rust, C and web code, so run both:

```text
--language rust
--language auto
```

Acceptance:

- explicit `--language rust` works after P6 registration;
- `auto` is measured, not assumed;
- if `auto` selects another language under Spine's existing file/node-count policy, record that as a profiling design finding rather than adding a Synaptreesitter special case.

### A.6 Native baseline — repository-authoritative commands

Use the repository's own host-native gates:

```bash
cargo metadata --format-version 1 --locked --no-deps
make lint
make test
```

At the reviewed `master`, `make lint` runs:

```text
cargo update --workspace --locked --quiet
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
```

`make test` runs the project's `cargo xtask` fixture-fetch/generate/test flow.

Package-local `cargo build/test -p ...` is useful for fast P7 feedback, but P9 is not green until the relevant repository-level integration path passes.

Do **not** require Spine P9 to reproduce the full upstream cross-platform matrix. The repository CI covers Linux, Windows, macOS, illumos and wasm32 plus native C/CMake/WASM flows. Spine's initial validation is host-native.

### A.7 Preflight proof

For this repository, `make lint` is the authoritative comparison.

Spine's generic `RustPreflightRunner` may execute equivalent commands internally, but its result must agree with `make lint`.

Because Synaptreesitter explicitly uses `-D warnings`, preserve that policy here. Do not generalize it to repositories that do not declare it.

No `rust-toolchain.toml` should be invented. The selected compiler must satisfy the manifest's Rust 1.90 minimum.

### A.8 Brownfield run 1 — targeted custom-path crate

Use a disposable worktree and a small Rust-only change in a custom-path crate, preferably `crates/config`.

Purpose:

- prove custom Cargo target-path layout;
- prove correct package/target ownership;
- prove code lands in the existing source structure;
- prove Rust 2024/MSRV 1.90 reaches codegen guidance;
- prove package-local build/test;
- prove Rust preflight;
- minimize native/fixture noise in the first codegen run.

Evidence:

```text
selected package
selected target
selected source path
generated diff
package-local build/test
preflight result
relevant integration result
clean-checkout rerun
```

### A.9 Brownfield run 2 — reverse-dependent package

Run a second disposable change in a shared internal crate to prove P7 dependency fan-out.

Candidates after metadata inspection:

- `tree-sitter-config` → CLI consumer;
- `tree-sitter-highlight` → CLI and loader/default-feature consumers;
- `tree-sitter-language` / `tree-sitter` → broad fan-out.

Derive the expected affected set before the run, then compare it to the packages actually exercised by `CargoTestRunner`.

Start with a moderate fan-out package; do not make the first runner proof depend on the native FFI-heavy core crate.

### A.10 Build-script boundary

The `tree-sitter` package declares:

```text
build = "binding_rust/build.rs"
```

and that build script compiles C.

Acceptance:

- comprehension may parse `build.rs` but never execute it;
- profiling/grounding perform no Cargo execution;
- codegen/test may execute build scripts only inside the governed test environment;
- missing compiler/native prerequisites produce an actionable environment error, not an extraction error.

### A.11 Feature-aware follow-up

The workspace contains WASM feature paths.

Initial P9 uses the default host-native feature set. Do not automatically run `--all-features`.

After the default path is green, add one explicit feature-aware validation:

```text
package: a package exposing `wasm`
feature: wasm
identity expectation: unchanged
execution expectation: feature explicitly selected
```

Cargo features can change build configuration, not source identity.

### A.12 Deliberate-red proof

Create disposable failures for two layers:

1. compile-time type/signature error;
2. deterministic failing assertion.

Both must be reported red.

This protects against:

- only testing `default-members`;
- selecting the wrong package;
- build-only false greens;
- empty test selection;
- skipped preflight;
- missing reverse-dependent coverage.

### A.13 Synaptreesitter acceptance gate

DONE only when:

```text
explicit master/release branch selected
exact commit SHA recorded
no implicit stale-default checkout
Cargo metadata captured
Spine static Cargo model matches package/target ownership
all reviewed workspace-member shapes accounted for
custom lib paths resolve correctly
build-script targets identified without execution during comprehension
Rust-2024 parser census recorded
make lint baseline passes
make test baseline passes
Rust PKG extraction succeeds
pkg verify passes
sampled graph facts hand-checked
explicit --language rust works
--language auto measured/documented
targeted custom-path brownfield run passes
reverse-dependent brownfield run tests expected affected set
Rust preflight agrees with repository policy
compile-red caught
test-red caught
clean-checkout rerun reproduces green
no Synaptreesitter-specific production special case exists
```

## Target B — independent complement

Synaptreesitter now covers enough complexity that Target B should be **small and diagnostic**, selected only for a missing shape after the census, such as:

- `#[path]` module overrides;
- multiple explicit `[[bin]]` targets in one package;
- a compact trait-default-method case;
- heavy duplicate `cfg` declarations;
- an async service if Synaptreesitter lacks useful async coverage;
- workspace `members` globs / `exclude` if absent here.

Pin Target B's exact commit as well.

## Reproducibility rule

Every real-repository result records:

```text
repository URL
explicit branch
exact repository commit SHA
Spine commit SHA
Cargo metadata summary
selected package/target
commands executed
green/red result
known skipped checks
```

Production Rust extraction/codegen remains generic. Synaptreesitter appears only in validation documentation/evidence.


# 24A. Coverage-expansion plan — bridge what Synaptreesitter does not exercise

Synaptreesitter remains the **anchor brownfield repository**. Do not try to mutate it into a universal Rust testbed or add Synaptreesitter-specific production behavior to Spine.

Bridge missing Rust ecosystem shapes through a layered validation portfolio:

```text
Synthetic corpus
    ↓
language precision / edge correctness

Synaptreesitter
    ↓
real Cargo workspace / codegen / build-test robustness

Targeted public repos
    ↓
ecosystem gaps Synaptreesitter does not exercise

Optional rust-analyzer enrichment
    ↓
type-aware semantic depth
```

The objective is not “one repository covers Rust.” The objective is:

> every major Rust risk has at least one controlled corpus case and at least one realistic validation path appropriate to that risk.

## 24A.1 Gap matrix

| Capability / risk | Synaptreesitter coverage | Bridge mechanism | Initial-release requirement |
|---|---|---|---|
| Cargo workspaces / path dependencies | **Strong** | Keep Synaptreesitter as anchor | **Required** |
| Custom Cargo target paths | **Strong** | Synaptreesitter | **Required** |
| Build scripts / native C | **Strong** | Synaptreesitter | **Required** |
| Rust 2024 / MSRV | **Strong** | Synaptreesitter | **Required** |
| Traits / generics | **Good** | Corpus + Synaptreesitter sampling | **Required** |
| `use` / re-export / aliases | **Good** | Corpus + Synaptreesitter | **Required** |
| `cfg` / feature-dependent source | **Partial** | Corpus + one feature-aware Synaptreesitter run | **Required** |
| Declarative/proc macros | **Partial** | Macro-heavy corpus + one proc-macro public repo | **Required for boundary validation**, not macro expansion |
| Async / Tokio-style code | **Limited** | Small async service repo | **Required before calling support “extensive”** |
| Axum/Actix/Tonic routes | **Limited** | Framework repo / follow-on endpoint phase | Optional for base language support |
| `no_std` source | **Weak** | Small `no_std` public repo + corpus | **Comprehension required** before “extensive”; build optional |
| Embedded/custom target/linker | **Weak** | Environment-detection test repo | Post-v1 build support; comprehension-only initially |
| Heavy FFI / bindgen | **Partial** | Add FFI fixture or repo if native build path proves insufficient | Optional hardening |
| Multi-feature matrices | **Partial** | Feature-aware targeted tests | Required at least for one explicit non-default feature |
| Nightly-only Rust | **Not covered** | Explicit capability detection | Out of initial scope |
| Non-Cargo build systems (Bazel/Buck/etc.) | **Not covered** | Separate future track | Out of initial scope |
| Type-aware receiver dispatch | **Not covered by tree-sitter** | Optional rust-analyzer semantic pass | Post-v1 enrichment |

## 24A.2 Validation portfolio

Use four complementary validation assets.

### V1 — Synthetic Rust corpus — precision oracle

Purpose:

- exact expected nodes/edges;
- intentionally difficult language shapes;
- regression tests independent of external repository churn.

Must include:

```text
semantic modules
custom module paths
trait impl vs inherent impl method collision
generic impls
use aliases / pub use
lexical shadowing
cfg duplicate declarations
macro boundary
async syntax
no_std
unsafe/extern declarations
feature-gated modules
```

The corpus is where precision must remain `1.00` for every emitted fact kind.

### V2 — Synaptreesitter — primary brownfield SDLC benchmark

Purpose:

- Cargo workspace topology;
- custom target paths;
- build scripts;
- native compilation;
- dependency fan-out;
- codegen;
- package-targeted build/test;
- lint/preflight;
- false-green detection.

Synaptreesitter is mandatory for P0-P10 and remains the long-term regression anchor.

### V3 — Small async/service repository — async semantic stress

Select one public repository or maintain one small public validation repository with:

```text
Tokio
async fn / .await
spawn
channels
Arc / Send / Sync
traits
select! or similar macro surface
integration tests
```

Prefer a small repository that can be fully hand-audited. It should not be another very large workspace.

Exit criteria:

- parser census green;
- no invented CALLS around async receiver chains;
- async functions remain ordinary `Function` facts;
- same-module / explicit-path calls still resolve correctly;
- macro boundaries stay honest;
- codegen can make one small async feature and pass native tests.

### V4 — Small `no_std` / embedded-style repository — environment boundary stress

Select one public repository with:

```text
#![no_std]
core / alloc usage
cfg(target_*)
custom target or embedded target metadata if available
linker/build configuration
```

Initial scope is **comprehension + environment detection**, not universal firmware compilation.

Exit criteria:

- PKG extraction works without assuming `std`;
- `core` / `alloc` symbols remain external rather than first-party;
- no panic from missing hosted-runtime assumptions;
- toolchain/environment detector identifies unavailable cross-target prerequisites clearly;
- no attempt to install or execute target toolchains during comprehension.

## 24A.3 Macro gap strategy

Do not attempt to “fix” macro coverage by partially expanding a few macros.

Instead:

1. Keep macro invocations syntactically visible.
2. Extract source declarations/bodies that exist in the CST.
3. Do not invent declarations generated by derive/proc macros.
4. Add explicit corpus expectations for missing generated symbols.
5. Add one public proc-macro-heavy validation repo to prove Spine remains stable around the boundary.
6. Record macro-related recall separately from non-macro recall.

This keeps the graph deterministic and avoids an inconsistent “some macros expand, others do not” model.

## 24A.4 Async gap strategy

Async Rust does not require a new PKG node kind.

Treat:

```rust
async fn fetch() -> Result<T, E>
```

as a normal `Function`.

Do not model compiler-generated futures/state machines.

The gap to bridge is call resolution and codegen quality:

- explicit function paths resolve normally;
- receiver calls requiring type inference remain unresolved in the tree-sitter-only tier;
- codegen follows the repository's runtime (`tokio`, `async-std`, etc.) rather than introducing one;
- generated tests use the repository's existing async test conventions.

Add async-specific tests to P2/P3 even before the external async repo is selected.

## 24A.5 `no_std` gap strategy

The extractor must not equate Rust with the standard library.

Add explicit comprehension tests for:

```rust
#![no_std]

use core::fmt;
extern crate alloc;
```

Rules:

- `std`, `core`, and `alloc` are external namespaces.
- Absence of `std` is not an extraction error.
- `no_std` changes codegen/environment guidance, not source identity.
- Initial codegen support may report “target/toolchain unavailable” rather than attempting a hosted build command that is known to be invalid.

## 24A.6 Feature / cfg gap strategy

Add a configuration model with two layers:

```text
source graph
    = configuration agnostic

build/test execution
    = one explicit Cargo feature/target configuration
```

For the initial release:

- parse all visible source declarations;
- do not evaluate every possible feature combination;
- suppress edges whose target depends on unresolved mutually exclusive cfg variants;
- run at least:
  1. default feature configuration;
  2. one explicit non-default feature configuration on Synaptreesitter or another validation target.

Record the feature set in every build/test evidence entry.

## 24A.7 Native/FFI gap strategy

Synaptreesitter already exercises a build script that compiles C. Use that as the primary native-build proof.

Only add a separate FFI target if P9 reveals an untested class such as:

- bindgen requiring `libclang`;
- system library discovery via `pkg-config`;
- CMake-driven native dependencies.

Do not make `libclang`, CMake, Ninja, or pkg-config unconditional Spine Rust dependencies.

They are project capabilities discovered by the environment layer.

## 24A.8 Semantic enrichment lane — rust-analyzer

Do not put rust-analyzer in the critical path for the first Rust release.

Design a later enrichment interface:

```text
tree-sitter facts
      ↓
unresolved semantic candidates
      ↓
rust-analyzer adapter
      ↓
only verified enrichments
      ↓
same canonical PKG IDs
```

Potential enrichments:

- receiver type resolution;
- go-to-definition;
- find-references;
- trait method selection;
- associated type resolution;
- selected macro-expanded symbol awareness.

Requirements before this lane starts:

- R1-R4 release is stable;
- canonical Rust IDs are frozen;
- enrichment cannot change already-grounded identities;
- running without rust-analyzer produces the same base graph as before.

## 24A.9 Support maturity levels

Track Rust support using explicit maturity levels:

| Level | Capability | Release expectation |
|---|---|---|
| **R1 Syntax** | tree-sitter-rust parses source | Required |
| **R2 Cargo topology** | workspace/package/target/feature model | Required |
| **R3 Source semantics** | modules/imports/traits/impl/CALLS precision-first | Required |
| **R4 SDLC** | codegen/build/test/fmt/clippy | Required |
| **R5 Semantic enrichment** | rust-analyzer/type-aware resolution | Follow-on |
| **R6 Ecosystem execution** | embedded/cross/native-specialized environments | Capability-driven follow-on |

The first production Rust release must complete **R1-R4**.

“Extensive Rust support” requires:

- R1-R4 complete;
- async validation green;
- macro-boundary validation green;
- `no_std` comprehension green;
- at least one non-default feature configuration validated;
- all remaining unsupported execution environments reported through explicit capability checks rather than generic build failures.

## 24A.10 New roadmap phases

Add a small breadth-hardening track after core Rust codegen rather than expanding P1-P10 indefinitely.

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---:|---|---|---|---|---|
| **P11 Ecosystem coverage census** | Compare Synaptreesitter + Rust corpus against the gap matrix; select only the minimum targeted repos needed for uncovered shapes | **2-3 ED** | Each gap has an owner: corpus, Synaptreesitter, targeted repo, or explicitly out-of-scope; validation repo count is bounded | ⬜ | | | |
| **P12 Async + macro hardening** | Async corpus/repo; proc-macro boundary corpus/repo; no macro expansion | **7-10 ED** | Async build/test green; zero invented macro-generated facts; macro recall documented | ⬜ | | | |
| **P13 `no_std` + feature hardening** | `no_std` corpus/repo; explicit non-default Cargo feature run; target/toolchain capability detection | **6-8 ED** | `no_std` comprehension green; feature run green; unavailable cross-target reported actionably | ⬜ | | | |
| **P14 Extensive-support review** | Re-run full matrix and docs; classify remaining limitations by R1-R6 | **2-3 ED** | R1-R4 complete; async/macro/`no_std` gates green; remaining R5/R6 limitations explicit | ⬜ | | | |

**Scope decision:** P11-P14 are **not in the initial Core Rust kickoff**. They are a separate **17-24 ED** tranche requiring a go/no-go after P10. They are mandatory only if the product/release objective is upgraded from **Core Rust support** to **Extensive Rust support**.

## 24A.11 Release labels

Use precise release language:

### After P1-P5

> **Rust comprehension available**

Do not claim codegen.

### After P6-P10

> **Rust comprehension + Cargo-aware codegen/build/test available for supported host environments**

Do not yet claim universal/extensive ecosystem coverage.

### After P11-P14

> **Extensive Cargo-based Rust support validated across workspace, async, macro-boundary, feature-gated and `no_std` source patterns**

Still do not claim “any Rust codebase.”

### After future R5/R6 work

Describe exact additional capabilities such as:

> Type-aware Rust semantic enrichment available through rust-analyzer.

or:

> Embedded target X supported when toolchain Y is installed.

Avoid an unbounded “all Rust” claim.

## 24A.12 Definition of done for gap bridging

Gap bridging is complete when:

```text
Synaptreesitter remains the primary anchor
synthetic corpus covers every precision-sensitive language shape
async public validation passes
macro boundary is measured and stable
no_std comprehension passes
one non-default feature configuration passes
native-build requirements are capability-detected
unsupported cross-target environments fail with actionable prerequisites
no production code contains validation-repo special cases
remaining semantic limitations are assigned to R5 or R6
documentation claims match the achieved maturity level
```


# 25. Framework enrichment — follow-on, not hidden scope

After core Rust support stabilizes, a separate enhancement may add:

### Axum

Recognize literal forms such as:

```rust
Router::new()
    .route("/users", get(list_users))
    .route("/users/:id", get(get_user))
```

### Actix Web / Rocket

Recognize literal route attributes when the handler and literal path are statically visible.

Emit:

```text
Endpoint
EXPOSES
```

only when handler/path are exact.

ORM/data-layer support for Diesel, SeaORM and SQLx should be a separate decision because heavy macro use changes the achievable recall ceiling.

---

# 26. Known permanent/initial gaps

Initial Rust support does not claim:

- procedural/declarative macro expansion;
- derive-generated methods or trait impls;
- arbitrary `include!` expansion;
- receiver method resolution requiring type inference;
- autoderef/autoref dispatch;
- dynamic trait-object dispatch;
- full generic/monomorphized call graphs;
- a selected Cargo feature/target configuration graph;
- compiler MIR/HIR semantics;
- borrow-checker reasoning;
- unsafe-code semantic analysis;
- FFI ABI modeling;
- async state-machine desugaring;
- external crate internals.

These are documented coverage boundaries, not extractor bugs.

---

# 27. Risk register

### R1 — wrong module identity

**Impact:** corpus and every symbol ID become unstable.
**Mitigation:** P0-B before extractor implementation.

### R2 — lib/bin/test collisions

**Impact:** unrelated functions silently deduplicate in `FactBatch`.
**Mitigation:** target-scoped IDs.

### R3 — trait/inherent method collisions

**Impact:** confidently incorrect CALLS.
**Mitigation:** trait-qualified implementation-method IDs.

### R4 — lexical shadowing

**Impact:** false-positive bare CALLS.
**Mitigation:** Rust scope walker + mandatory corpus case.

### R5 — cfg variants

**Impact:** graph pretends one platform's implementation is universal.
**Mitigation:** configuration-agnostic graph and ambiguous-call suppression.

### R6 — workspace false green

**Impact:** changed shared crate passes while consumer crate is broken.
**Mitigation:** reverse workspace-dependent testing.

### R7 — codegen executes build scripts

**Impact:** untrusted repository code executes.
**Mitigation:** only codegen/test path may run Cargo; comprehension remains read-only; reuse existing execution governance.

### R8 — macro ceiling mistaken for extractor defect

**Impact:** pressure toward partial, inconsistent macro expansion.
**Mitigation:** measured macro corpus case and explicit permanent boundary.

### R9 — MSRV/edition mismatch

**Impact:** generated code works on developer stable but not project compiler.
**Mitigation:** layout records edition/rust-version/toolchain and injects them into prompts.

---

# 28. Sequence

1. **P0** — grammar + identity proof; explicitly clone Synaptreesitter `master`, pin its SHA, capture Cargo metadata, run the Rust-2024 parser census, and record `make lint` / `make test` baselines.
2. **P1** — Cargo/module model and core facts; prove Synaptreesitter's custom target paths/package ownership before accepting the first real-repo extraction.
3. **P2** — imports/re-exports + guaranteed local CALLS + IMPLEMENTS.
4. **P2X (optional, max 3 ED)** — attempt exact cross-module/workspace CALLS; land only if §7.2 stop/go criteria pass. Deferral does not block Core.
5. **P3** — corpus and measured accuracy.
6. **P4** — profiling/detection.
7. **P5** — consumer integration, docs, review.
8. Merge Rust comprehension.
9. Open codegen branch.
10. **P6** — complete Rust Toolchain registration.
11. **P7** — build/test and affected-package selection; validate reverse-dependency fan-out against Synaptreesitter and distinguish full workspace membership from `default-members`.
12. **P8** — prompts/guidance/skill, including Rust 2024/MSRV 1.90 and custom Cargo target paths.
13. **P9** — two Synaptreesitter brownfield runs: targeted custom-path crate + reverse-dependent crate; repository-authoritative lint/test gates; compile-red + test-red proofs; clean-checkout reruns.
14. **P10** — review/docs/MR, with Synaptreesitter command/results evidence linked from the phase table.
15. **STOP / Core release gate.** Core Rust support is complete. Do not start breadth hardening automatically.
16. Conduct a separate **Extensive-support go/no-go** using P10 results, product need, staffing and the additional **17-24 ED** estimate.
17. Framework/ORM enrichment and rust-analyzer remain separate tracks unless explicitly promoted.

---
18. **P11** — ecosystem coverage census: map remaining Rust gaps to corpus/Synaptreesitter/targeted repos.
19. **P12** — async + macro hardening.
20. **P13** — `no_std` + non-default feature hardening.
21. **P14** — extensive-support review and documentation gate.
22. Extensive-support documentation/release only after P14; R5/R6 remain separate unless explicitly funded.

# 29. Definition of done

Rust comprehension is DONE only when:

```text
precision = 1.00 for every emitted fact/edge kind in the labelled corpus
known recall gaps are documented
Cargo multi-target identities do not collide
workspace/module resolution is deterministic
shadowed names produce no invented CALLS
Core P2 does not depend on cross-module/workspace CALLS
P2X either lands at precision 1.00 inside 3 ED or is documented as deferred
warm-cache fingerprint changes with the Rust grammar
all current front-end registration checks pass
docs/state/count gates pass
```

Rust codegen is DONE only when:

```text
greenfield build + tests pass
Synaptreesitter explicit branch + pinned commit and Cargo metadata are recorded
Synaptreesitter validation does not follow the stale default branch implicitly
Synaptreesitter static Cargo model matches custom target/package ownership
Synaptreesitter Rust-2024 parser census is recorded
Synaptreesitter `make lint` + `make test` baseline passes
Synaptreesitter targeted custom-path brownfield run passes
Synaptreesitter reverse-dependent brownfield run exercises the expected affected set
Synaptreesitter compile-red + test-red proofs are caught
brownfield build + tests pass
shared-crate change verifies affected downstream workspace members
deliberate compile/test failure is reported red
missing toolchain error is actionable
scaffold is idempotent
edition/MSRV are respected
final clean-checkout verification reproduces the result
Rust preflight actually executes
all Toolchain registry mutation tests remain green
```

## Extensive-support claim gate

Do not label Rust support **extensive** until P11-P14 are also DONE.

That claim additionally requires:

```text
async validation green
macro-boundary validation green
no_std comprehension green
one non-default feature configuration green
environment capability detection proven
remaining type-aware gaps explicitly assigned to rust-analyzer/R5
remaining cross-target gaps explicitly assigned to R6
```

The key principle is unchanged from the other Spine front-ends:

> Missing a hard-to-resolve edge is acceptable and measurable. Emitting a plausible but incorrect grounded edge is not.
