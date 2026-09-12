# Design + Plan: adding Perl to the PKG — comprehension (codegen is its own track)

**Status:** In progress — P1–P6 landed with evidence (full-repo validation on both validation
repos still pending, see each phase's own caveat). All six phases done except the MR itself,
blocked on a GitHub PAT scope issue — see P6's evidence. **Date:** 2026-09-10 · spine
v3.33.2.
**Branch:** `feat/perl-support` off `develop` at `d84e666`. **Delivery: one MR** to `develop` when
every phase in §4 is done and tested (the Kotlin track's rule — `kotlin-support-roadmap.md` D19;
named, not linked, since no Kotlin track has landed in this checkout yet).
Adds a Perl front-end to the PKG extractor as one self-contained track of four phases, the same
cadence as C#/C/C++ ([language-support-roadmap.md](language-support-roadmap.md)), SQL
([sql-support-roadmap.md](sql-support-roadmap.md)), Go ([go-support-roadmap.md](go-support-roadmap.md))
and PHP ([php-support-roadmap.md](php-support-roadmap.md)). **Codegen is in scope and is its own
track**, [perl-codegen-roadmap.md](perl-codegen-roadmap.md), opened when this one merges — the Java
and TypeScript split. Comprehension is the whole desirability win: when P1 lands, `understand` /
`state` / `design` / `investigate` / `localize` / `rca` / `regression` and codegen *grounding* work
on a Perl codebase.

> Perl is not in the expansion roadmap's four-language set
> ([language-expansion-roadmap.md](language-expansion-roadmap.md)); it is a demand-pulled addition
> under that document's first prioritisation criterion. The extractor *pattern* is the shipped
> tree-sitter one. **Three things are genuinely new** and are where the design effort goes:
> **the package is both the namespace and the class** (no other language collapses `Module` and
> `Type` onto one declaration — §0 D2 is the one real design decision), **inheritance is data,
> not syntax** (`@ISA`, `use parent`, `extends`, `use Mojo::Base 'X'` — five spellings of one
> edge), and **the fact that only `perl` can parse Perl** — the grammar is error-tolerant, and
> recall on legacy code will be lower than on any language shipped so far. Bound it honestly.

## Roadmap currency — the rule this document follows

Every phase row in §4 carries **Status · Started · Finished · Evidence**, updated in the same
commit as the work. A phase is DONE only when its evidence column links a commit, a test name, or
a pasted command result.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D1** | Parser | (a) `tree-sitter-perl` (standalone wheel), (b) `tree-sitter-language-pack`, (c) PPI via a `perl` subprocess | **(a).** Verified 2026-09-08: `tree-sitter-perl` **2.0.0** on PyPI, abi3 wheels for macOS arm64, manylinux x86/aarch64, musl, win amd64; `py>=3.8`; its only pin (`tree-sitter~=0.24`) is behind an optional `core` extra, so it coexists with the 0.26 in `uv.lock`. Probed: packages (statement **and** block form), `use`/`require`, `use parent`/`use base`, `@ISA`, Moo `has`/`extends`/`with`, every call shape in §3.2, and Perl 5.38 `class`/`method`, all with `has_error == False`. (b) is a 20+ MB dependency for one grammar; (c) needs a CPAN install on every machine. **As built (P1):** `tree_sitter_perl.language()` returns a bare pointer-sized `int` (`PyLong_FromVoidPtr`) rather than a `PyCapsule` (unlike `tree-sitter-php`'s `language_php_only()`), and tree-sitter's **Windows** binding parses a bare int via a 32-bit `unsigned long` format code — a real 64-bit pointer overflows it (`OverflowError`). Linux/macOS (8-byte `unsigned long`; CI's `ubuntu-latest`) are unaffected. `perl_extractor.py`'s `_perl_language()` wraps the pointer in a `PyCapsule` itself as a fallback so Windows-based development isn't blocked; `scripts/parse-census.py` carries the same fallback. Not a grammar defect — a third-party binding gap this track worked around rather than filed upstream (time-boxed). |
| **D2** | What a `package` is in the vocabulary | (a) `Module` = **file** (path-keyed, `perl:lib/Shop/Cart.pm`), `Type` = **every package** (`perl:Shop.Cart`), subs `perl:Shop.Cart.total`; (b) `Module` = package, no `Type` nodes; (c) `Module` = package and `Type` = package with a parallel id | **(a).** A Perl package is the only type unit the language has — `bless` makes any package a class. Under (b) `state` reports 0 components and `IMPLEMENTS` lands module→module. (c) invents a second id for one declaration. Under (a) the path-keyed module is the C/C++ precedent already in the corpus vocabulary table, and a `use Shop::Tax` import's placeholder `perl:Shop.Tax` is **upgraded by `FactBatch` dedup** when the grounded `Type` exists, so cross-file joins cost nothing. Functions in an implicit `main` (a script with no `package`) key on the file: `perl:bin/report.pl.usage`. |
| **D3** | Separator in ids | (a) dots: `perl:Shop.Cart.total`, (b) native `::` | **(a).** C++ keeps `::`, but its ids are bare symbols; Perl's are qualified, and the dotted form is what `import_link._DOTTED_PREFIXES`, the `docs.py` doc→symbol binder, and the registry URLs consume. One `::`→`.` conversion site. |
| **D4** | Roles (`with 'Shop::Role::Loggable'`, `Role::Tiny`, `Moose::Role`) | (a) `IMPLEMENTS` class→role, role is a `Type`, (b) skip | **(a).** Same reasoning as PHP traits: a mixin is the behavioural claim `IMPLEMENTS` already documents, and it makes blast radius reach every consumer of a role method. |
| **D5** | Which inheritance spellings resolve to `IMPLEMENTS` | (a) all five: `use parent`/`use base` lists, `our @ISA = (...)` / `push @ISA`, Moo/Moose `extends`, `use Mojo::Base 'Parent'`, 5.38 `:isa(...)`; (b) syntax-only | **(a), literal-only.** Inheritance in Perl is *data*; reading only the syntactic forms misses the majority of CPAN-era code (`@ISA` + `Exporter`). A computed `@ISA` yields nothing. |
| **D6** | `Field` in classic (hash-based) objects | (a) declared accessors only: Moo/Moose/Mojo::Base `has`, `Class::Accessor` `mk_accessors`, 5.38 `field`; (b) also infer from `$self->{key}` | **(a).** `$self->{items}` is a hash access, not a declaration; inferring fields from it fabricates a schema the author never wrote. |
| **D7** | Suffixes | (a) `.pl`, `.pm`, `.t`; (b) `.pm` only; (c) plus shebang sniffing | **(a).** `.t` files are Perl and are the tests. `.pl` collides with Prolog by name; a Prolog file parses to ERROR nodes and yields nothing, the right degradation. (c) needs a dispatcher change; document the gap. `.pod` and `.xs` are not Perl source. |
| **D8** | Invention oracle (`pkg/scope.py`) | (a) `NOT_APPLICABLE["perl"]` with a reason, (b) a `_Perl` walker | **(a).** Variables carry a sigil; `f()` and `$f->()` / `&$f` are different CST nodes. A `my $f` cannot shadow a bare call — the Java/PHP argument. |
| **D9** | Codegen | (a) its own track, opened when this one merges; (b) inside this track | **(a)** — [perl-codegen-roadmap.md](perl-codegen-roadmap.md): `cpanm --installdeps .` → `prove -l t/`, proven green **and** red, one MR of its own. `"perl"` stays out of `SUPPORTED_LANGUAGES` until that track's first commit adds the whole machinery, so `sdlc feature --language perl` exits 2 here rather than scaffolding Python. |
| **D10** | Default `@EXPORT` resolution for bare calls | (a) a whole-repo `finalize` pass in P2, (b) never | **(a), P2, verified-only.** A bare `fmt()` after `use Shop::Util;` (no list) resolves only when the repository's own `Shop::Util` declares `fmt` in a literal `@EXPORT`. Anything else is skipped — the `exporter_default` corpus case keeps it honest. **Amended in P6 review**: this decision originally also resolved a bare call through the owner's `@ISA` chain ("an inherited `total()` resolves only through a literal `@ISA` chain to a first-party package"). That is not how Perl works — `@ISA` is consulted for *method dispatch* (`$self->m()`/`Class->m()`) only; an unqualified `f()` is a plain sub-name lookup in the current package (or an explicit import), and Perl raises "Undefined subroutine" at runtime if neither exists. The `@ISA` half of D10 fabricated a `CALLS` edge to a sub the program cannot actually reach this way — removed from the code and from this decision, not kept as a documented gap, since nothing about it was ever true of the language. |

---

## 1. Where Perl is today — nowhere

| Fact | File | Consequence |
|---|---|---|
| `.pl` / `.pm` / `.t` are not in the profiler's suffix map | [`catalog/profile.py`](../../src/orchestrator/catalog/profile.py) | A Perl repo profiles as `languages=∅` |
| `cpanfile` / `Makefile.PL` / `Build.PL` / `dist.ini` are not read as markers | `catalog/profile.py` | No framework (`mojolicious` / `catalyst` / `dancer2`) or test-runner (`prove`) detection |
| No `perl_extractor.py`, no `perl` extra, no `tree_sitter_perl` probe, no `_GRAMMAR_MODULES` entry | `pkg/`, `pyproject.toml`, `doctor.py`, `persistence.py` | **Zero graph nodes**; a warm cache would not notice the extra |
| `--language perl` is rejected (exit 2) | `feature_runner.py` | Correct; the codegen track adds the machinery before the flag (D9) |

---

## 2. Why Perl is cheaper than its reputation, and where it is not

```
CHEAP
· One grammar, prebuilt for every CI platform; parses the block-form package,
  Moo/Moose declarations, SUPER::, __PACKAGE__->, and 5.38 class syntax with no ERROR.
· Qualified names resolve DETERMINISTICALLY: `Shop::Util::fmt()` and
  `Shop::Log->new` are exact ids with no symbol table. Bare `f()` resolves to the
  same-file sub or a name in an explicit `use Foo qw(f)` import list.
· `.pl`/`.pm`/`.t` collide with no registered suffix.
NOT CHEAP
· "Only perl can parse Perl." tree-sitter is error-tolerant, so a hard file still
  yields the declarations that parse — but sub recall on legacy code WILL be below
  1.00. State it, measure it on the classic validation repo (§10), and do not tune
  labels to hide it.
· Inheritance is data (D5). Five spellings of one edge, all read literally.
· A package is both container and class (D2). One decision; get it right in P1.
```

**Reused verbatim:** lazy parser factory + `TYPE_CHECKING`-guarded `TSNode`; gated append in
`default_extractors()`; the two-pass CALLS pattern; `finalize` repoint (C#/PHP); the corpus
method; the per-front-end freshness test; the C-style path-suffix import join for `require "file.pl"`.

---

## 3. Design

### 3.1 Fact mapping (P1)

| Perl construct (CST node) | Fact | Notes |
|---|---|---|
| file | `Module` `perl:lib/Shop/Cart.pm` | Path-keyed (D2) |
| `package_statement` (statement form — scopes to the next `package` or EOF; block form — its block) | `Type` `perl:Shop.Cart` | Every package (D2). A file with several packages yields several `Type`s under one `Module` |
| `use_statement` with a `package` child | `IMPORTS` module→`perl:Shop.Tax` | Placeholder, upgraded by dedup when first-party. The `qw()` list feeds the resolver. Pragmas (`strict`, `warnings`, `utf8`, `feature`, `v5.36`) skipped by a lowercase-initial rule |
| `require_expression` with a `bareword` / a string literal | `IMPORTS` | Bareword → package; `require "lib/x.pl"` → path-keyed `perl:lib/x.pl`, joined by path suffix (the C rule). Computed → nothing |
| `use parent` / `use base` / `our @ISA = (...)` / `push @ISA` / `extends` / `use Mojo::Base 'X'` / `class Foo :isa(X)` | `IMPLEMENTS` type→`perl:X` | D5, literal-only |
| `with 'Role'` / `with qw(A B)` | `IMPLEMENTS` type→role | D4 |
| `subroutine_declaration_statement`; 5.38 `method_declaration_statement` | `Function` `perl:Shop.Cart.total` | Owner = the package in scope at that line; implicit `main` → `perl:<path>.total`. Anonymous subs → no node |
| `has name => (...)` / `has [qw(a b)]` · `__PACKAGE__->mk_accessors(qw(a b))` · 5.38 `field $x` | `Field` `perl:Shop.Cart.items` | D6; sigil stripped. The `Field` only — the CALLS pass resolves `$self->items` to it |
| `CONTAINS` | module→type, module→sub (main), type→sub/field | As every front-end |
| `our $VERSION`, `@EXPORT`, `@EXPORT_OK`, file-scoped `my`, POD, `__END__`/`__DATA__` | — | `@EXPORT` is read by D10's pass, not emitted |

`is_public`: a leading underscore is Perl's convention — add `"perl"` to `_UNDERSCORE_LANGS` in
`insights.py`.

### 3.2 CALLS (P2) — precision-first

| Call shape | Resolution | Emit |
|---|---|---|
| `$self->m()` / `$class->m()` / `__PACKAGE__->m` / `shift->m` | sibling sub **or `has` field** of the enclosing package | `CALLS` → `perl:Shop.Cart.m` |
| `$self->SUPER::m()` | the first D5-resolved parent, in default (depth-first, left-to-right) MRO order, that actually declares `m` | `CALLS` → the declaring parent's `perl:Parent.m`; with exactly one resolved parent that doesn't declare it, falls back to the `Type` itself (row 3's policy); with several parents and none declaring it, skip rather than guess which chain resolves it; skip outright when unresolved. **Known limitation:** `use mro 'c3'` changes the linearisation — this always follows the default DFS order (found in P6's second review: `use parent qw(A B)` + `SUPER::bmeth()` resolved to `A` even though only `B` declares `bmeth`) |
| `Shop::Log->new` | qualified name | `CALLS` → the sub when declared; otherwise → the `Type` (instantiation is a call to the type) |
| `Shop::Util::fmt(...)` | qualified name | `CALLS` → `perl:Shop.Util.fmt` — exact id, placeholder if third-party |
| `f()` bare | same-package sub in this file, else a name in a `use X qw(... f ...)` list, else (D10) a literal `@EXPORT` of a first-party package imported without a list | `CALLS` → the sub; **otherwise skip** — never a guess. **Not** an `@ISA`-inherited sub (amended in P6 review — a bare call never dispatches through inheritance in real Perl; that was only true of method calls) |
| `&f` / `&$code` / `$self->$m()` / `$obj->can('m')->()` / `goto &f` / string `eval` / `AUTOLOAD` | — | never — fabrication |
| `$obj->m()` where `$obj` was assigned a literal constructor in the same sub (`my $log = Shop::Log->new`) | receiver type from the assignment | **P3** — the typed-receiver rule, file-local |

### 3.3 Framework edges (P3)

| Framework | Source shape | Fact |
|---|---|---|
| Mojolicious (full app) | `$r->get('/orders')->to('orders#index')` · `->to(controller => 'Orders', action => 'index')` · `$r->under('/api')` groups | `Endpoint` + `EXPOSES` → `perl:App.Controller.Orders.index`. `any` → nothing (no `ANY` verb); computed paths → nothing |
| Mojolicious::Lite / Dancer2 | `get '/x' => sub {…}` | `Endpoint`, no `EXPOSES` (closure rule); `get '/x' => \&handler` → `EXPOSES` |
| Catalyst | `sub index :Path('/x') :Args(0)` | verb-less → nothing (D2 of [endpoints-typescript-go.md](endpoints-typescript-go.md)) |

### 3.4 Data layer (P4)

| Framework | Source shape | Fact |
|---|---|---|
| DBIx::Class | `__PACKAGE__->table('orders')`; `belongs_to` / `has_many` / `might_have`; `add_columns(...)` | `Entity` `perl:entity:App.Schema.Result.Order` + `REFERENCES`; columns → `Field`s on the entity |
| Rose::DB::Object / Class::DBI | `__PACKAGE__->meta->setup(...)` | Same shape, second reader |
| DBI raw SQL strings | — | a cross-language pass, its own spec |

### 3.5 Testing the phases with Spine itself

Every phase ends by running Spine on both validation repositories (§10) and pasting the numbers
into §4 — the Kotlin track's rule (`kotlin-support-roadmap.md` §4, named not linked per the
note above):

```bash
# via scripts/validate-frontend.py (§8.2) — forces refresh=True itself, so no commit-keyed
# cache is trusted regardless of whether the shallow clone still carries its own .git/
uv run --frozen orchestrator pkg extract <copy> --json | jq .summary
uv run --frozen orchestrator pkg verify <copy>
uv run --frozen orchestrator state <copy> --lens developer | grep -E "^- Stack|^- Size|Call graph"
uv run --frozen orchestrator understand <copy>
```

and through the MCP tools a user meets: `profile_repo`, `map_repo`, `blast_radius` on a Mojolicious
controller sub, `explain_symbol` on a package, `docs_for` on a POD-derived claim, `pkg_joins` against a
small consumer fixture in P3. Before each phase touches Spine's own code, `blast_radius` on the
registration symbols (§7) is re-run and the table refreshed; each phase's design goes through
`design_change` with its D-rows as the intent, attached as evidence. **The Perl-specific measurement**
is the parse census (§8.3): on the classic repository, `sub` declarations counted by `grep -c '^sub '`
beside `Function` nodes from the graph, per file, so the recall ceiling the grammar imposes is a
number in §4, not a caveat.

---

## 4. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **P1 Comprehension** | `scripts/parse-census.py` first (§8.3 — D1's number); `perl_extractor.py` (§3.1, D2–D7); package-scope tracking; registry: `default_extractors`, `FRONT_ENDS`, `EXTRA_PROBES`, `_GRAMMAR_MODULES`, profiler markers/needles, `scope.NOT_APPLICABLE` (D8), `import_link.py` path-suffix matcher for D7, `insights._UNDERSCORE_LANGS`, `docs.py`/`doc_link.py` `pl`/`pm`; packaging (`perl` extra, `languages` meta-extra, mypy override, `ci.yml`); tests per [docs/reviewing/language-frontend-checklist.md](../reviewing/language-frontend-checklist.md); docs per [docs/reviewing/docs-matrix.md](../reviewing/docs-matrix.md) | ~4–6 d | `pkg extract` on both validation repos yields Module/Type/Function/Field + IMPORTS/CONTAINS/IMPLEMENTS from 0; `pkg verify` 0 errors; **sub recall on the classic repo measured** against `grep -c '^sub '` and written here; `scripts/docs_audit.py` clean; gate green with `--extra perl` | 🟡 | 2026-09-10 | | **Parse census** (§8.3, `.git`-less copies): ExifTool — 355 files, **1** with a parse `ERROR` (99.7% clean), 1/361,351 lines inside an `ERROR` span, `subroutine_declaration_statement` count **1,727** vs `grep -c '^sub '` **1,726** (grammar recall ≈ parity with the naive count — legacy CPAN Perl parses cleanly here). Mojolicious — 274 files, 4 with `ERROR` (8/72,444 lines), sub count **1,313** vs grep **1,305**. **Real `pkg extract` runs** (this sandbox's tree-sitter binding needed a Windows capsule workaround — see D1 "As built"): a 20-file ExifTool tag-table subset → 293 nodes / 311 edges, `CALLS`=0 as designed; `Nikon.pm` alone (14,282 lines, the largest file in either repo) → extracts cleanly in ~5.2s. `orchestrator pkg capabilities --format markdown` confirms the registered shape: Module/Type/Function/Field + IMPORTS/CONTAINS/IMPLEMENTS, `CALLS`/`Endpoint`/`Entity` correctly absent. `blast_radius` (post-change): `default_extractors` 14 callers (was 13 pre-change, +1 for the new registry test), `extractor_fingerprint` 8, `link_imports` 3 — all match §7's refreshed table. `design_change` run against the P1 intent, grounded, attached above. **Full whole-repo `pkg extract`/`verify`/`state`/`understand` on the complete validation repos did not finish in this session** — this Windows sandbox hit severe, unrelated resource contention (confirmed via process inspection, not a code defect: the same extractor logic ran correctly at subset and single-largest-file scale). Needs a clean-environment or CI re-run before this row can read DONE. **Tests:** 167 passed, 0 failed across `test_perl_extractor.py` (14), `test_capabilities.py` (15), `test_verifier.py` (22), `test_default_extractors.py`+`test_scope.py` (42), `test_profile.py`+`test_insights.py`+`test_doctor.py` (74); `test_persistence.py` not completed in-session (same environment issue) but its registration fact (`tree_sitter_perl` in `_GRAMMAR_MODULES`) is independently confirmed via `blast_radius` and code inspection. Two real bugs found and fixed while verifying: a test helper not creating nested parent dirs, and — the substantive one — `"lib/$x.pl"` (an interpolated, computed require target) was misread as a literal path, because tree-sitter-perl nests an interpolated `scalar` *inside* the `string_content` node rather than as a sibling; `_plain_string_literal_text` now checks the content node's own children too. `mypy src tests` clean (4 pre-existing, unrelated errors in `gateway/tools/run_python_analysis.py` — a Windows `resource`-module gap, not touched by this track). `ruff check`/`format` clean. `scripts/docs_audit.py --strict` 0 STALE/MISSING (fixed 9, incl. one pre-existing PHP-era gap found in passing). `scripts/state-numbers.py --check` OK, 11/11 gated claims match (fixed 4, incl. a line-wrap bug introduced and caught in the same session). `scripts/matrix-count.py --check` OK. `scripts/render_architecture_svg.py --check` now current (SVG regenerated; **PNG not regenerated — `rsvg-convert` unavailable in this sandbox**, a packaging gap for whoever runs this next, not a code gap). `scripts/render_knowledge_foundation_svg.py --check` unaffected. |
| **P2 Corpus + CALLS** | `corpus/perl/{plain,instance_calls,exporter_default,isa_spellings,legacy_main,multi_package}` labelled from source first; §3.2 rows 1–6; D10's `finalize` pass written on the shared helper (§8.5); `--scoreboard`; freshness test | ~4–5 d | precision 1.00 on every kind; CALLS recall stated with predicted `known_gaps`; invention `NOT_APPLICABLE` with reason; `state` "Call graph: available" | 🟡 | 2026-09-11 | | All six cases labelled from source before the extractor ran on them (ground rule 6). Cross-checked node-for-node, edge-for-edge against real extraction (bypassing the CLI, same Windows workaround as D1): `plain`, `exporter_default`, `isa_spellings`, `multi_package` — **exact match**. `instance_calls` — the extractor produces exactly the edges NOT in `known_gaps` (the 2 predicted P2/P3-boundary misses are the only ones missing), confirming both the resolved edge (`Shop::Log->new`, row 3) and the two deliberately-unresolved ones (row 7) are exactly right. `legacy_main` found a real, unrelated bug in passing: its fixture originally used `bin/*.pl`, and `extractor.DEFAULT_IGNORE_DIRS` treats `bin` as ".NET build output" — silently dropped for every language, not just C#. Fixed by moving the fixture to `scripts/`; the gap itself is flagged as a standalone follow-up (§12), out of this track's scope. `blast_radius` post-change: `score_corpus` 8 callers, `find_invented_calls` 14 callers (both confirmed via MCP, evidence in the design record below). `design_change` run against P2's intent, grounded. Perl's own D10 fallback is 38 lines on top of `finalize_names.py` (well under the "80 lines" exit bar) — `finalize_names.py` itself is ~25 lines (`declared_ids` + `resolve_or_drop`), reusable as-is by C#/PHP when next touched. **Tests:** 29 passed in `test_perl_extractor.py` (14 P1 + 15 new P2, covering every §3.2 row and the row-6 exclusions explicitly). `mypy src tests` and `ruff` clean for every touched file. **CALLS recall: 0.67** (4 of 6 labelled edges — the corpus's own honest number, not tuned). **Not yet run in this environment** (same resource-contention issue P1's evidence flagged): the real `orchestrator pkg accuracy --language perl` CLI path end-to-end (my direct `score_corpus()` call and the manual cross-check above cover the same code path but not the CLI wrapper itself), `--scoreboard`, and a fresh full-repo `pkg verify`/`state`/`understand` pass on both validation repos with real `CALLS` counts. Needs a clean-environment or CI re-run before this row reads DONE. **Update (P5):** that full-repo run happened, via `validate-frontend.py` — and found a real row-2 (`SUPER::m()`) precision bug this corpus's own cases never exercised (no case has a `SUPER::new` into a base that doesn't itself declare `new`). Fixed; see P5's evidence for the root cause and the fix. |
| **P3 Routes + typed receivers** | `perl_routes.py` (Mojolicious full + Lite, Dancer2); §3.2 row 7; `corpus/perl/mojo_routes` | ~3–5 d | `Endpoint`s on the Mojolicious repo with `EXPOSES` to real controller subs; a Perl service joins as a provider in `pkg joins` | 🟡 | 2026-09-11 | | `perl_routes.py` built: Mojolicious full-app route chains (`$r->get(path)->to(...)`, both the `'controller#action'` string and `controller=>/action=>` hash forms) scanned per sub body; `under('/api')` groups tracked as a **variable-keyed prefix dict in source order** (Go's `r.Group()` shape, not Laravel's closure recursion — Mojolicious's `under()` binds a variable). **Found and fixed a real bug in dev**: the first implementation walked the sub body with a LIFO stack, so a `my $api = $r->under(...)` binding could be visited *after* routes that used it, silently dropping them — rewritten as pre-order source-order recursion; `test_mojo_under_group_composes_prefix` pins it. Mojolicious::Lite/Dancer2's shared bareword DSL (`get '/x' => sub{}` / `\&handler`) scanned at statement scope. §3.2 row 7 (typed receivers): `_collect_local_constructor_types` builds a file-local `{varname: type_id}` map from literal same-sub constructor assignments; a receiver resolves through it only when the target type declares the method (no `->new`-style Type fallback for an arbitrary method name). **Corpus**: `mojo_routes` added (string/hash `to()`, closure-no-EXPOSES, `any`-excluded, `under()`-composed path) and `instance_calls` updated (the literal-constructor edge moved from `known_gaps` to `edges` — now resolved; the untyped-parameter edge stays a permanent `known_gap`). All 7 corpus cases cross-checked node-for-node against real extraction: 6 exact match, `instance_calls` matches exactly minus its one remaining predicted gap. `blast_radius`/`design_change` run against the P3 intent, evidence attached. `orchestrator pkg capabilities --format markdown` confirms `Endpoint`/`EXPOSES` now ✓ for `perl` (regenerated via the project venv directly — a Bash-invoked `python` on this machine resolves to an unrelated global install, not `.venv`, and silently produced a stale/wrong capabilities list before this was caught). **Tests**: 40 passed (37 in `test_perl_extractor.py` — 29 prior + 8 new P3 — plus 3 in `test_finalize_names.py`). `mypy`/`ruff` clean. `scripts/docs_audit.py --strict` 0 STALE/MISSING. **Not yet run in this environment** (same flagged issue as P1/P2): `pkg joins` end-to-end against a real second repo, and the full-repo validation-target numbers. |
| **P4 Data layer** | DBIx::Class `Entity`/`REFERENCES`/`Field` (§3.4); `corpus/perl/dbic` | ~2–4 d | entities linked; `data_layer_link` reconciles against a `.sql` schema; zero invented `REFERENCES` | 🟡 | 2026-09-11 | | New `perl_orm.py` (finalize()-deferred, like CALLS — a relation target's local-vs-external classification needs every file's declarations known first, not just the declaring file's). Marker is read literally: `__PACKAGE__->table('orders')`, never guessed from base class or file shape. `add_columns(...)` becomes `Field`s on the `Entity` (`perl:entity:Dotted.Path`, a parallel id beside the package's own `Type` node) — both the bareword-list (`qw(id customer_id)`) and `name => {...}` hash forms, keys only from the hash form. `belongs_to`/`has_many`/`might_have` become `REFERENCES`, `src` always the declaring entity; a relation to a class this repo never declares still gets the edge, to an external placeholder `Entity` (D2's own precedent, not a new rule). **Bug found and fixed in dev**: `add_columns`'s hash-form key extraction first reused the shared `_string_or_wordlist_targets` helper, which doesn't recognize tree-sitter-perl's `autoquoted_bareword` node type (only string/quoted-word-list/list/paren forms) — hash-form keys silently extracted as nothing. Fixed with explicit hash-form detection local to the handler (not the shared helper, to avoid changing its other call sites); `test_dbic_columns_hash_form_reads_only_keys` pins it. A `dict`-invariance mypy error (`emit_entities`'s `Protocol`-typed param) fixed by taking `Mapping` instead of `dict`. **Corpus**: `corpus/perl/dbic` added — two DBIx::Class Result classes, `Order`→`OrderItem` (`has_many`) and `OrderItem`→`Order` (`belongs_to`, the reverse direction) plus `Order`→`Customer`, a class this fixture never declares. Cross-checked against real extraction: exact match on every labelled node and edge; the one difference (`perl:entity:App.Schema.Result.Customer` produced but not in `expected.json`'s `nodes`) is exactly the documented, correct behavior — an external node is excluded from the node ratio while the `REFERENCES` edge to it still counts (`corpus/README.md`'s decided rule; my labelling script's own diff doesn't implement that exclusion, the real scorer does — recorded in `expected.json`'s `excluded[]` rather than silently treated as a mismatch). All 8 corpus cases now cross-checked: 6 exact match, `instance_calls` matches exactly minus its one permanent known_gap, `dbic` matches exactly modulo the external-node exclusion above. `blast_radius` post-change: `emit_entities` 1 caller (`PerlExtractor.finalize`, by design — the same single-caller shape as PHP's `php_orm.emit_entities`). `data_layer_link` needed **zero** Perl-specific wiring, confirming §3.4's own premise: it reconciles generically against any language's already-emitted `Entity`/`REFERENCES`, not per-front-end. **Tests:** 44 passed (40 prior + 4 new P4) across `test_perl_extractor.py` + `test_finalize_names.py`. `mypy`/`ruff` clean, full repo. `scripts/docs_audit.py --strict` 0 STALE/MISSING. `scripts/state-numbers.py --check` OK (fixed 2 stale claims: source modules 357→358, test functions 3,133→3,137, both from the new `perl_orm.py` module and its 4 tests). **Skipped, and why:** Rose::DB::Object/Class::DBI as a second ORM reader — never in scope, §3.4/§5 name only DBIx::Class for P4. **Not yet run in this environment** (same flagged issue as P1–P3, never resolved this session): a real `pkg extract` pass against full checkouts of either validation repo specifically exercising DBIC — ExifTool has no ORM code at all (it's a tag-table library, ExifTool's own `Nikon.pm`-style modules have no `__PACKAGE__->table()`) so 0 entities is the *expected*, not missing, result there; Mojolicious's own comprehension pass in P1–P3 never surfaced DBIx::Class usage either, so this phase's real-world validation surface is the corpus, not a repo-measured recall number — stated honestly rather than fabricated. The `bin/` `DEFAULT_IGNORE_DIRS` gap flagged in P2 remains open, unrelated to this phase. |
| **P5 Generic work** (§8) | whichever of §8.1 (`roadmap-status.py --check`), §8.2 (`validate-frontend.py`) and §8.4 (language-track template) the Kotlin track has not already landed; **§8.3 `parse-census.py` is Perl's to build** and lands in P1 because D1's recall number depends on it; §8.5 the shared `finalize` name-resolution helper if P2's D10 pass is the second implementation of it | ~2–3 d | each item's own exit in §8; this table passes §8.1 | 🟡 | 2026-09-11 | | **No Kotlin track exists in this checkout** — confirmed before building anything: no `kotlin-support-roadmap.md` under `docs/specs/`, no local or remote `kotlin`-named branch (`git ls-remote --heads origin` lists only `conflict`, `feat/g3-media-ingestion`, `feat/perl-support`, `main`). Perl is first to reach §8, so it builds rather than reuses — §7's own note that this run's blast-radius numbers "hold for both until either lands" turned out to describe a track that has not landed. **§8.1** `scripts/roadmap-status.py`: six checks (a sixth added in P6, see its evidence) over every `docs/specs/*.md` phase table found by its exact header (not any bold-looking row — a decisions table's rows also start with a bold phase code, e.g. `**C1**`) — a DONE row missing Evidence/Finished; a codegen-track phase Started before its named comprehension-track dependency phase is DONE (`perl-codegen-roadmap.md`'s "Depends on... P2" is the first real case, currently unarmed since no C-phase has Started); a document's own top `**Status:**` line still saying "no code written"/"plan for review" while its phase table already carries Evidence; every phase-table document linked from `SPEC-INDEX.md`; every relative link in a phase-table document resolving. **Deliberately does not** attempt the general "does `SPEC-INDEX.md`'s free prose about a spec agree with that spec's own free prose" classifier — `STATE-OF-SPINE.md` §8 ("CI gate on spec-status drift") tried exactly that across all ~80 specs and measured 33% precision, withdrawn as "not gate-worthy... the class stays human"; the four checks here read structured table cells or compare a document against itself, not one document's prose against another's. **Dogfooded immediately, and found real bugs on the first run**: this document's own top Status line had gone stale (still "Proposed... no code written" after four phases of real evidence — fixed); two links to `kotlin-support-roadmap.md` resolved nowhere in this checkout (de-linked to plain filenames with a note, since no Kotlin track has landed here); and, found while re-reading §7 for the template, not by any automated check — a leftover git-merge artifact from the P2 collision (a bare `=======` line followed by a stale, pre-P1 duplicate of the blast-radius table, undetected because `docs_audit.py`'s link/staleness checks don't scan `docs/specs/*.md` roadmaps at all, only the fixed `USER_DOCS` list) — removed. 16 tests in `tests/test_roadmap_status.py`. **§8.2** `scripts/validate-frontend.py <language> <git-url> [<git-url> ...]`: reuses `resolve_repo_source`/`materialize_repo_source` directly (the same SSRF-guarded, host-allow-listed shallow-clone-then-delete the CLI's `_repo_arg` wraps), then extract + verify + the `state` stack-line fields + node counts by (language, kind) + top unresolved `IMPORTS` targets — exactly §8.2's stated scope, not `understand`/episteme synthesis (a separate manual step in this document's own §3.5, out of §8.2's definition). 4 tests in `tests/test_validate_frontend.py` against a local fixture (no network in tests). **§8.4** `docs/specs/templates/language-track.md`: the skeleton generalized from this document's own section shape (currently the most complete of the shipped tracks — decisions table, roadmap-currency rule, phases living table, corpus, files-to-change incl. §6.1's per-document-per-phase table, blast-radius, generic-work, packaging, validation targets, out-of-scope, risks), with every Perl-specific fact replaced by bracketed guidance; excluded from `roadmap-status.py`'s scan by construction (`docs/specs/*.md` is non-recursive, so `docs/specs/templates/*.md` is never mistaken for a real roadmap needing a `SPEC-INDEX.md` entry). **§8.3 and §8.5 already shipped** in P1 and P2 respectively, as their own rows say. **§8.6 stays unbuilt**, as designed — "not in this track's phases, recorded so it is not lost." `mypy`/`ruff` clean on the new files (full repo). `docs_audit.py --strict` 0 STALE/MISSING. `state-numbers.py --check` OK (fixed test-function/test-file counts: +20 tests, +2 files, from the two new test suites, +2 more from the SUPER:: fix below). **`validate-frontend.py perl https://github.com/mojolicious/mojo` — the first real-repo run this whole track has completed** (P1–P4's own evidence all flagged full-repo validation as blocked by this sandbox's environment issue; this one went through, live network clone included): 2,273 grounded nodes, 4,786 edges (CALLS 1,965, IMPLEMENTS 103, IMPORTS 918, EXPOSES 2), `state` reports `framework=mojolicious`, `call_graph=available`. **It found a real precision bug on the first run**: `pkg verify` failed with 10 dangling `CALLS` edges — `perl:Mojo.Log.new -> perl:Mojo.EventEmitter.new` and 9 more, all `SUPER::new` chains into `Mojo::Base`-style classes that never declare an explicit `sub new` (Mojo::Base injects a constructor further up the chain, invisible to static analysis). Root cause: P2's row 2 (`SUPER::m()`) fabricated `<base>.<method>` unconditionally from `owner.bases[0]`, unlike row 3's own already-verified `Shop::Log->new` handling, which falls back to "call the type" when the target doesn't declare the method — exactly the "guessed method id has no backstop" violation `docs/reviewing/language-frontend-checklist.md` names. **Fixed in `perl_extractor.py`**: row 2 now checks whether the resolved base actually declares the method; if not, it edges to the base `Type` itself (external only when the base is never declared anywhere in this repo), reusing row 3's policy rather than inventing a second one. Two new tests (`test_calls_super_falls_back_to_type_when_base_doesnt_declare_it`, `test_calls_super_falls_back_to_external_type_when_base_undeclared`); `test_perl_extractor.py` + `test_finalize_names.py` re-run clean, 46/46 (43 + 3). `validate-frontend.py perl https://github.com/mojolicious/mojo` re-run against the same live URL after the fix: `pkg verify: OK — 0 errors` — the fix closed all 10 dangling edges on the exact repo that surfaced them, not just the two unit-test shapes. ExifTool not attempted live this session (Mojolicious alone surfaced the bug this row exists to report; time-boxed rather than open-ended). **`corpus/perl/super_calls` added** (§5) — no case had exercised `SUPER::` at all before this, the gap that let the bug through the corpus undetected in P2: one resolved (`SUPER::helper` into a base that declares it), one falling back to a locally-declared base's `Type` (`SUPER::new`, the exact Mojolicious shape), one falling back to an external `Type` (a base never declared anywhere in-repo). `orchestrator pkg accuracy --language perl`: **9 cases scored, precision 1.00 on every node and edge kind across the whole corpus** — `super_calls` itself P 1.00 / R 1.00 on every kind. |
| **P6 Review + MR** | `/review-pr` on the branch; fix; one MR to `develop` with this table and every validation number as its body | ~1 d | verdict "mergeable"; every §6.1 row updated; CI green; no `episteme/` in the diff | 🟡 | 2026-09-11 | | **No `/review-pr` skill/subagent is available in this session** — CONTRIBUTING.md is explicit that it is "a maintainer's personal, untracked local setup," and neither `.claude/skills/` nor `.claude/agents/` exist anywhere reachable from this checkout. Substituted with an equivalent fan-out review: a general-purpose review agent given the full `origin/main...feat/perl-support` diff (74 files, ~4,400 insertions), the roadmap's own precision invariant, and `docs/reviewing/language-frontend-checklist.md`'s rules, instructed to verify correctness, test quality, corpus honesty, script robustness, registration completeness, and doc currency — explicitly told not to re-litigate D1-D10 or flag the already-documented ExifTool validation gap. **It found one confirmed high-severity precision bug**: `_handle_class_accessor_call`'s `belongs_to`/`has_many`/`might_have` branch took "the first string literal in the argument list" as the relation target — correct only when the relation name is a bareword (`belongs_to(customer => 'App::Schema::Result::Customer', ...)`, the corpus's only spelling); a quoted name (`belongs_to('customer', 'App::Schema::Result::Customer', ...)`, equally valid DBIx::Class) made it pick the *name* as the target, fabricating a `REFERENCES` edge to a node the source never declares — exactly the invariant this whole track is built around ("never emit a node or edge the source doesn't prove"). **Fixed**: reads DBIx::Class's own positional signature (`$rel_name, $related_class, ...`) — target is always position 1, regardless of how position 0 is spelled. `corpus/perl/dbic/OrderItem.pm`'s `belongs_to` switched to the quoted spelling so the corpus itself would have caught this; `test_dbic_relation_with_quoted_name_resolves_the_target_not_the_name` added. **A second confirmed finding**: `validate-frontend.py`'s clone/extract/verify/state block had no exception handling, so a clone timeout or an extractor edge case on one URL would crash the whole multi-repo run with a raw traceback — contradicting its own purpose (§8.2, unattended runs across a list). Fixed: wrapped as a per-repo boundary that reports and returns `False` rather than raising; `test_validate_one_reports_and_continues_when_extraction_raises` added. **A third**: `roadmap-status.py`'s table parser silently dropped a whole document from every check if its very first data row had the wrong column count, with zero diagnostic. Fixed: a 6th check, `check_header_found_but_unparsed`, reports exactly that condition instead of staying silent; 2 new tests added. **Doc staleness the review's `docs_audit`-adjacent pass caught** (prose numbers no mechanical gate reads): `USER_GUIDE.md`, `CLI_REFERENCE.md`, `EXAMPLE.md` all still said "44 fixture cases (40 single-language)" and omitted Perl from the `CALLS` recall table entirely — last updated at P2's count of 6 and never revisited across P3/P4/P5's three further corpus additions; `FEATURES.md` and `CLAUDE_GUIDE.md` both still said Perl's corpus "lands... in a later phase," contradicted by this same branch's own P4/P5 evidence rows; `STATE-OF-SPINE.md` §2 still quoted Perl's stale 0.67-on-6-edges figure. All fixed to the real, current numbers (47 fixture cases, 43 single-language; Perl `CALLS` recall 0.89, 8 of 9). **Drive-by, pre-existing and unrelated to this track** (found adjacent to an edit already touching the file, not counted against this track): `KNOWLEDGE_GRAPH.md` §10 still said "19 hand-labelled fixture repositories across all 8 front-ends" — stale since before PHP even shipped; corrected to 47/10 in the same pass. **Lower-confidence, not fixed**: bareword HTTP-verb route detection (`scan_lite_route`) has no `use Mojolicious::Lite`/`use Dancer2` presence gate, so a plain script defining its own `sub get {...}` could misfire as a route — the same bounded-convention approach `php_routes.py`/`go_routes.py` already take elsewhere in this codebase, so left as a documented, accepted limitation rather than a blocking fix. **Re-verified after every fix**: `test_perl_extractor.py`+`test_finalize_names.py` 47/47; `test_roadmap_status.py`+`test_validate_frontend.py` 23/23; `orchestrator pkg accuracy --language perl` — 9 cases, **precision 1.00 on every node and edge kind including `REFERENCES` at the exact case the bug was in**; `mypy`/`ruff` clean on every touched file; `docs_audit.py --strict` 0 STALE/MISSING; `state-numbers.py --check` OK; `roadmap-status.py --check` OK (now 6 checks). **The rest of CI's own steps, run locally since no PR has ever triggered a real Actions run** (`ci.yml` only fires on push/PR to `main`/`develop`, and this branch has neither): `orchestrator pkg verify .` on Spine's own repo — 0 errors, 1 pre-existing unrelated warning; `scripts/sdlc_shapes.py` — all 4 repository shapes hold; `orchestrator understand .` — builds cleanly (91 files), then fully reverted (`git checkout -- episteme/` + deleted 2 new untracked module docs) since a contributor branch must never carry it; `render_architecture_svg.py --check`, `render_knowledge_foundation_svg.py --check`, `matrix-count.py --check` — all clean; the README-absolute-links check — clean. §6.1 walked row by row: every document current. **No `episteme/` in the diff** — confirmed via `git diff origin/main...feat/perl-support --stat`. **A fourth finding, caught after the fact**: this document's own P5 evidence cell quoted an example table row (a bold phase code flanked by two pipe characters, inside backticks) — those literal pipe characters split that one row into 10 cells, and `roadmap-status.py`'s parser treated *any* non-8-cell row as end-of-table, silently dropping P5 and P6 from `phase_tables()` entirely. `--check` never caught it (every check it ran was against the 4 rows it *was* handed, all genuinely clean); found only when asked to display the table and P5/P6 were missing from the output. Fixed two ways: the literal pipe characters in this document's own P5 prose rewritten to not need them, and `phase_tables()` changed to *skip* a malformed row rather than stop at it — later valid rows are no longer swallowed by an earlier bad one — with the skip itself now reported by `check_header_found_but_unparsed`. Two more tests added (a genuine skip-and-continue case, and the "zero rows" case re-asserting it now reports both facts). Re-verified: `test_roadmap_status.py` 19/19; `phase_tables()` now returns all 6 rows for this document. **A second, deeper maintainer review followed** (11 blocking findings B1-B11, plus should-fix items), every one confirmed against the actual code — several against real Perl semantics probed with tree-sitter-perl directly, not assumed — and fixed on this branch: **B1** `PerlExtractor` reused across repos in a multi-repo run (`load_or_extract_repos`'s own loop) never reset `_types`/`_subs` — repo A's facts leaked into repo B's graph. `finalize()` now takes local references and resets both before returning, matching Go's own accumulator-reset pattern; every finalize-chain method threads `types` explicitly instead of reading `self._types` (which is now empty by the time they run). **B2** `use X qw(f)` is package-scoped in real Perl (`Module->import` runs at whichever package `caller()` names), not file-scoped — a later `package Y;` in the same file used to inherit an earlier package's imports. `_Ctx.use_func_map`/`bare_use_targets` are now properties resolving a shared, file-wide, *package-keyed* dict (so a reopened package still sees its own earlier imports). **B3** D10 had a second sub-step resolving a bare `f()` through the owner's `@ISA` — real Perl never dispatches a bare call through inheritance, only `$self->m()`/`Class->m()` do. Removed; D10's own decision text and §3.2 row 5 amended in the same commit. **B4** `&Shop::Util::fmt()` (qualified, old calling convention) parsed with the same `&`-prefixed `function` node as bare `&f` (confirmed via direct CST probe) and fell through into the qualified-call branch, embedding the literal `&` in the emitted id — now excluded as a class, matching row 6's own promise. **B5** `sub Shop::Elsewhere::baz {}` (a fully-qualified sub name, valid Perl for reopening another package without a `package` block) kept the lexically-current package as owner and left `::` inside the function name — now resolves the real owner from the qualified name itself, with its own `Type`/`CONTAINS` edge. **B6** Dancer2's `del` (DELETE, since `delete` is a builtin) uppercased to the endpoint name literally as "DEL" — normalized to the real HTTP verb "DELETE" at the one place an endpoint's name is built. **B7** A Mojolicious route registered from a helper/plugin method (`MyApp::Routes::install`, a real pattern) resolved its controller target against *that method's own* package, not the app class — now gated on the enclosing sub being literally `startup` (Mojolicious's own required entry-point name) unless an explicit `namespace => 'X'` override is given, which is always honoured regardless of sub name. **B8** `_camelize('foo-bar')` produced `Foo::Bar`, spliced into an already-dotted id — never matched the real declaration's own fully-dotted id. Now joins with `.`. **B9** Five of nine corpus cases listed an external placeholder as an expected node (against corpus/README.md's own rule) — `plain`'s own control case scored 0.33/0.75 against its stated "1.00/1.00 by design" as a direct result. All five relabeled; **every corpus case now scores exactly 1.00/1.00 on every node and edge kind** except `instance_calls`'s one permanent, predicted known_gap. **B10** Perl was never in `scoreboard.json`, so `compare_scoreboard`'s own per-language loop never checked it — a regression to 0.00 would not have failed a build. `--scoreboard` run now that the corpus is fully clean; `pkg accuracy --check` confirms 0 regressions, no other language's block moved. **B11** `exporter_default`'s `Shop::Util` set `@EXPORT` without ever inheriting `Exporter` — `@EXPORT` is inert unless `Exporter`'s own `import()` reads it, so the labelled edge wasn't true of the fixture's own code. D10 now also verifies the exporting package's `bases` includes `Exporter`; the fixture fixed to actually inherit it, and a second test pins the without-Exporter case correctly refusing. **Should-fix items also closed**: a role consumed via `with 'Role'` used to feed the same `bases` list `SUPER::` reads as real inheritance — `SUPER::` never dispatches through a role in real Perl, so `_emit_implements` now takes `is_base` and only real inheritance (`extends`, not `with`) populates `bases`. `has_one` was missing from `DBIC_RELATIONS` (`many_to_many` deliberately still excluded — different signature). `use parent`/`use base`/`use Mojo::Base` genuinely load the named class (Perl's pragma `require`s it) and now also emit `IMPORTS`, not just `IMPLEMENTS` — except `-norequire` (probed via CST: `use parent -norequire, 'X'` is the documented way to skip the load), which correctly suppresses just the IMPORTS edge, not the real inheritance. `resolve_or_drop`'s `declared_ids(batch)` was rebuilt on every single bare-call resolution attempt — genuinely quadratic in repo size; now computed once per `finalize()` and threaded through, via a new optional keyword on the shared helper that defaults to the original per-call behavior for any other caller. A unit test claiming to exercise the `require`-path-join end-to-end placed the requiring script under `bin/` (walker-ignored) and passed by accident, for a reason unrelated to what it claimed — moved to `scripts/`, assertion strengthened to check the actual edge. `roadmap-status.py` gained the Started-≤-Finished date check it was missing (now 7 checks); `docs/reviewing/language-frontend-checklist.md` now names `parse-census.py` as the D1 step per §8.3's own exit criterion; §8.2's own spec text corrected to match what `validate-frontend.py` actually does (reuses the shared `materialize_repo_source` clone, no separate `.git`-strip step — nothing in the pipeline reads a checkout's own top-level `.git` specially, and `load_current_state`'s `refresh=True` already bypasses any commit-keyed cache regardless). **Re-verified after this whole pass**: `test_perl_extractor.py`+`test_finalize_names.py` 61/61; `test_roadmap_status.py`+`test_validate_frontend.py` 27/27; `mypy`/`ruff` clean full-repo (18 pre-existing, unrelated errors confirmed untouched by this branch — Windows `resource`/`opentelemetry`/`coverage`/`asgi_lifespan` gaps); `docs_audit.py --strict` 0 STALE/MISSING; `state-numbers.py --check` OK; `roadmap-status.py --check` OK; `orchestrator pkg accuracy --check` OK, 0 gated regressions. **Blocked on a maintainer decision**: the draft MR itself. `gh pr create` fails identically to every earlier attempt this track made — `GraphQL: Resource not accessible by personal access token (createPullRequest)`. This is a token-scope issue (the PAT lacks `pull_requests: write`), not something fixable from inside the repo. Everything the MR would need is ready: branch `feat/perl-support` pushed, target `main` (this fork's only base branch), and a title/body drafted from this table. Needs either a PAT with pull-request write scope, or the maintainer opening it by hand from this branch. |

All phases land on `feat/perl-support`; delivery is **one MR**. **Rough total: ~16–24 days**, one
engineer familiar with the PKG; codegen's ~8–12 days are in its own roadmap. Phase order is fixed:
P2's D10 pass needs P1's declarations; P3's typed receivers need P2's resolver table. No net-new *algorithm*; P1 is a day longer than PHP's because of
package-scope tracking and the five inheritance spellings.

---

## 5. Corpus cases (P2–P5)

Vocabulary row for `corpus/README.md`: `perl` · module `perl:lib/Shop/Cart.pm` *(a path)* · type
`perl:Shop.Cart` · separator `.`.

| Case | Exercises | The finding it is built to catch |
|---|---|---|
| `plain` | one `.pm` with `package`, `use parent`, `use X qw(f)`, three subs, `$self->` and qualified calls | the control |
| `instance_calls` | `my $log = Shop::Log->new; $log->write` and `$thing->write` on a parameter | P2 skips both; P3 resolves the first only |
| `exporter_default` | `use Shop::Util;` whose literal `@EXPORT` supplies `fmt`, then a bare `fmt()`; `croak()` from `Carp` | the first-party edge is emitted by D10's pass; the `Carp` edge is invented and must be absent |
| `isa_spellings` | five packages, one per D5 spelling, plus one computed `@ISA` | five `IMPLEMENTS`; the computed one yields nothing |
| `legacy_main` | two scripts with no `package`, both defining `usage`; `require "lib/common.pl"` | distinct path-keyed ids; the `require` joins by path suffix |
| `multi_package` | statement-form and block-form packages in one file, a sub after the second `package` | the sub belongs to the second package |
| `mojo_routes` (P3) | `->to('orders#index')`, the hash form, an `under` group, a closure, `->any` | closure → endpoint without `EXPOSES`; `any` → nothing |
| `dbic` (P4) | two Result classes, `belongs_to`/`has_many`, one relation outside the tree | `REFERENCES` between the two; the outside target stays external |
| `super_calls` (P5) | `SUPER::helper()` into a base that declares it; `SUPER::new()` into one that doesn't; `SUPER::build()` into an undeclared external base | the declared method resolves; the other two fall back to "call the type" (row 3's policy) rather than a fabricated method id — the shape that was invented and fixed live against Mojolicious |

---

## 6. Files to change

**New:** `src/orchestrator/pkg/perl_extractor.py`, `perl_routes.py`; `tests/pkg/test_perl_extractor.py`,
`test_perl_routes.py`; `corpus/perl/*`; this file.

**Modified (P1):** `pkg/extractor.py`, `pkg/capabilities.py`, `pkg/persistence.py`, `doctor.py`,
`catalog/profile.py`, `pkg/scope.py`, `pkg/import_link.py`, `knowledge/insights.py`, `pkg/docs.py`,
`pkg/doc_link.py`, `pyproject.toml` (+ `uv.lock` one line), `.github/workflows/ci.yml`, the registry
tests (`test_default_extractors`, `test_capabilities`, `test_verifier`, `test_scope`, `test_profile`,
`test_doctor`, `test_insights`); docs per §7.1.

**Untouched:** everything under `sdlc/` — the codegen track's files (D9).

### 6.1 User-facing documentation — what changes, in which phase

Updated in the phase that makes each row true, in the same commit as the code;
`scripts/docs_audit.py` reports nothing STALE or MISSING before that commit.

| Document | What must change | Phase |
|---|---|---|
| `README.md` | every language list (intro, "Works across", "Add a language" count and next-language list); "What's new" at the release cut | P1 · release |
| `FEATURES.md` | a Perl capability row; the "all N front-ends" accuracy row count | P1 |
| `USER_GUIDE.md` | the extras list (`[perl]`), the "Multi-language" blockquote (`.pl`/`.pm`/`.t`, codegen "in its own track"), the corpus-results line | P1 |
| `KNOWLEDGE_GRAPH.md` | node/edge matrices, language table row, "Parser coverage", a fact-mapping note on D2 (package = namespace **and** class, module = file) | P1, P4 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | the language sentence and "N front-ends"; the toolchain table gets no Perl row yet and says why | P1 |
| `CLI_REFERENCE.md`, `EXAMPLE.md`, `BENCHMARK.md` | corpus-results counts | P2 |
| `SETUP.md` | the `[perl]` extra | P1 |
| `corpus/README.md` | the id-vocabulary row (path-keyed module, dotted type) | P2 |
| `plugins/spine/skills/*/SKILL.md` | the language line | P1 |
| `docs/specs/STATE-OF-SPINE.md` | front-end count, precision row count, `CALLS` recall row with Perl's number, source-module and test counts | P1, P2 |
| `docs/specs/SPEC-INDEX.md`, `language-expansion-roadmap.md` | this spec's row, updated to the phase reached — never ahead of it | every phase |
| `CHANGELOG.md` | one entry under Unreleased per phase, in the house voice | every phase |
| `assets/spine-architecture.svg` (+ `.png`) | "across N language front-ends", re-rendered by its script | P1 |

---

## 7. Blast radius — measured from the PKG, per registration site

`blast_radius` on Spine's own graph at `d84e666`, this branch's base — the same measurement the
Kotlin track took at the same commit, so the numbers hold for both until either lands. Re-run before
each phase; a count that moved is a reason to re-read the callers, not to skip them.

| Symbol | Callers | Touches | What the phase must respect |
|---|---|---|---|
| `pkg.extractor.default_extractors` | **14** (re-measured post-P1, `blast_radius`) — `RepoCodeExtractor.__init__`, `accuracy.score_corpus`, `verifier.GroundingVerifier._extractor_for`, 10 registry tests (incl. the new `test_default_includes_perl_when_available`), the capability superset test | 30 | the gated append is read by the corpus scorer, the freshness verifier and the matrix test — the biconditional test for `perl` is not optional |
| `pkg.persistence.extractor_fingerprint` | **8** (confirmed post-P1) — `_cache_path` + 7 tests | 10 | `tree_sitter_perl` in `_GRAMMAR_MODULES`; the cross-check test from #336 enforces it |
| `pkg.import_link.link_imports` | **3** (confirmed post-P1) — `RepoCodeExtractor.extract` + 2 tests | 12 | **As built:** `"perl"` is *not* in `_DOTTED_PREFIXES` — a `use`/bareword-`require` target names a package, which is a `Type` under D2, so it already joins via the exact dotted id (same reasoning as PHP, D2 vs D7). Only a literal `require "path.pl"` (a path-keyed `Module`) needs the new `_match_perl_path` C-style matcher |
| `sdlc.feature_runner._resolve_language` | 4 — `run_feature`, `autorun._require_plan`, 2 tests | 9 | **not touched** (D9); the codegen track owns it — confirmed: no edit made here |

The lesson the numbers carry, again: the site with the fewest callers (`extractor_fingerprint`, all
tests) is the one the PHP track missed, and its failure was silent. Few callers is not low risk.

---

## 8. Generic work — built here or by the Kotlin track, reused by every later one

The Kotlin roadmap's §9 lists six items; the two tracks share them rather than build them twice.
**Rule:** whichever track reaches its generic-work phase first builds the shared item; the other
rebases onto it and records "reused" in its evidence column. Perl adds two of its own.

### 8.1 `scripts/roadmap-status.py --check` — the roadmap-currency gate *(shared)*
Fails when a phase marked DONE has no Evidence or Finished date, or when a spec's Status line
disagrees with its `SPEC-INDEX.md` row. Perl's §4 is the second table it reads; the
`perl-codegen-roadmap.md` dependency line ("depends on support merged") is the first cross-spec
check it enforces — a codegen phase cannot be Started before the support track's P2 is DONE.

### 8.2 `scripts/validate-frontend.py <language> <git-url>` *(shared)*
The real-repository smoke test as a script: shallow clone, extract + verify + the `state`
stack line, node kinds by language, top unresolved import targets, delete. Perl runs it on
two repositories per phase (§10), so it takes a list of URLs, not one. **As built**: reuses
`resolve_repo_source`/`materialize_repo_source` directly — the same shallow-clone-then-delete
mechanism `pkg`/`state`/`understand` already use for any git-URL argument — rather than a
separate manual clone-and-strip-`.git` routine this row originally specified; found in
review that the two don't match. No `pkg extract`/`verify`/`state` code path in this
codebase reads or treats a checkout's own top-level `.git` directory specially (`is_nested_repo`
looks for one in a *child* directory, to find submodules — a different question), so a
`.git`-less copy was never load-bearing here the way it is for `corpus/`'s own `.repo/`
convention; reusing the already-tested shared mechanism was the better call than adding a
step for a benefit nothing in the pipeline actually needs.

### 8.3 `scripts/parse-census.py <grammar-module> <dir>` — **Perl builds this, in P1**
What the Kotlin D1 decision was made from and the Perl D1 caveat depends on: parse every file of a
language with its grammar and report files with an ERROR node, lines inside ERROR spans, and
declaration counts by CST kind — the recall ceiling the grammar imposes before any extractor runs.
Stdlib + `tree_sitter`; the grammar is passed by module name so every future track uses it for its
D1 row. **Exit:** the classic Perl repository's number is in §4 P1's evidence, and
`docs/reviewing/language-frontend-checklist.md` names the script as the D1 step.

### 8.4 A language-track template *(shared)*
`docs/specs/templates/language-track.md` — the skeleton this document, Kotlin's and PHP's share.

### 8.5 A shared whole-repo name-resolution `finalize` helper — **Perl builds this, in P2**
C# repoints guessed bases, PHP repoints guessed `new X()` targets, and Perl's D10 resolves bare calls
through literal `@EXPORT` lists and `@ISA` chains — three copies of "once every declaration is
known, fix the per-file guess". Lift the shape into `pkg/finalize_names.py` (declared-id index,
repoint-or-drop policy per edge kind, an `external` placeholder factory) and make D10 the first
front-end written against it; C# and PHP migrate when next touched. **Exit:** D10 is under 80 lines
on top of the helper; the C# and PHP tests pass unchanged after migration.

### 8.6 Client-side HTTP as a cross-language pass *(shared, Kotlin's P3 builds it)*
Perl's `Mojo::UserAgent` / `HTTP::Tiny` / `LWP` calls become the third scanner once Kotlin's
Retrofit generalises `python_client.py`; not in this track's phases, recorded so it is not lost.

---

## 9. Packaging

- `tree-sitter-perl` 2.0.0: `language()` is the single export. **P1 step 1: confirm the parse in
  the project venv** (`uv run --frozen`), not only in the isolated probe used for D1.
- Base install stays stdlib-only: `find_spec("tree_sitter_perl")` before a function-local import;
  `tree_sitter_perl` in `persistence._GRAMMAR_MODULES` (the cross-check test fails without it).
- One CST detail to verify in P1: 5.38 `field $x :param` parses as a `variable_declaration` with
  an `attrlist` — key on the `field` keyword token.

## 10. Validation targets (ephemeral, docs-only names)

Two poles, both public, shallow-cloned to a scratch dir, extracted on a `.git`-less copy, deleted
after; names live in `docs/` only, never in `src/` or `tests/`:

- **Mojolicious** (`mojolicious/mojo`) — modern OO Perl: `use Mojo::Base 'Parent'`, `has`, roles,
  a router, ~200 `.pm` + `.t` files. Exercises D2/D4/D5, P3 routes, and `.t` as source; the
  codegen track uses it again for brownfield.
- **ExifTool** (`exiftool/exiftool`) — classic CPAN Perl: `@ISA = qw(Exporter)`, hash-based
  objects, huge table-driven `.pm` files, no Moose. Exercises the `@ISA` spelling, D6, D10, and
  **parse recall on hard Perl**: record the `sub` count from `grep` beside the `Function` count
  from the graph, per file, in this document.

**Baseline to record before P1:** `pkg extract` on each yields 0 Perl nodes and the profiler
reports no language.

## 11. Out of scope — not Perl

- **XS / C extensions** (`.xs`, `.c` under a Perl dist) — C, already covered by the C front-end.
- **Extensionless scripts** (shebang-only `bin/` files) — a dispatcher change; measure demand.
- **Catalyst attribute routing** without an HTTP method — verb-less, the no-`ANY` rule.
- **Embedded-SQL strings** in DBI calls — a cross-language pass, its own spec.
- **Codegen and preflight** — the codegen track (D9).

## 12. Risks and gotchas

- **D2 is load-bearing.** Every id keys on it; changing it after P2 invalidates the corpus.
- **Package scope is positional.** A sub emitted under the wrong package is a wrong id `pkg verify`
  cannot see; the `multi_package` case exists for this.
- **Recall below 1.00 on legacy Perl is the honest number.** Write it down; never add labels that
  agree with the extractor.
- **Pragmas look like imports.** The lowercase-initial skip is the rule; a first-party lowercase
  package name is the accepted false negative.
- **Untracked `docs/specs/*.md` changes the spec count** and, as this document once did, can be
  lost — it is tracked on its branch from its first commit.
- **Never commit `episteme/`.**
- **`bin/` is silently skipped, for every language.** `extractor.DEFAULT_IGNORE_DIRS` treats
  `bin` as ".NET build output" and `RepoCodeExtractor` never walks into it — found while
  building the `legacy_main` corpus case (P2): a fixture under `bin/*.pl` extracted to zero
  nodes with no error. Real Perl repos commonly keep executable scripts in `bin/`. Not this
  track's fix (shared, cross-language infra) — the corpus case moved to `scripts/` instead,
  and the gap is flagged as a standalone follow-up.

## 13. Sequence

```
0. Decide D1–D10 (this document)               — review; D2 first, it keys everything
1. P1 comprehension  → recall on ExifTool recorded
2. P2 corpus + CALLS → corpus 1.00 precision, exporter_default green, @EXPORT/@ISA pass
3. P3 routes + typed receivers → Perl provider in the multi-repo joiner
4. P4 DBIx::Class entities
5. P5 generic work → parse-census (P1 already), finalize helper (P2 already), the shared items not yet landed
6. /review-pr, then one MR to develop
7. open feat/perl-codegen — perl-codegen-roadmap.md
```
