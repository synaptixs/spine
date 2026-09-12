# Design + Plan: Perl codegen — `sdlc feature --language perl`, built and tested with `prove`

**Status:** Proposed — plan for review, no code written. **Date:** 2026-09-10 · spine v3.33.2.
**Depends on:** [perl-support-roadmap.md](perl-support-roadmap.md) merged (P1 for grounding, P2 for
the call graph the generated code is grounded on). **Branch:** `feat/perl-codegen` off `develop`,
opened when that dependency lands. **Delivery: one MR** to `develop` when every phase in §3 is done
and tested. Same split as Java ([multi-language-java.md](multi-language-java.md) →
[java-codegen.md](java-codegen.md)) and TypeScript ([typescript-codegen.md](typescript-codegen.md)):
comprehension is its own track, codegen is its own track, both in scope.

> The toolchain is the whole story: `cpanm --installdeps .` then `prove -l t/`. One dependency
> installer that may be absent, one test runner that is always present with `perl`. No build
> step, no build-system detection, no TFM probing. Cheaper than C, Go or PHP; the design effort
> goes into **where generated code lands in an existing distribution** and into proving green
> **and** red against the real toolchain, the Go 4.2 / 4.5 lessons.

## Roadmap currency

Every phase row in §3 carries **Status · Started · Finished · Evidence**, updated in the same
commit as the work. DONE means the evidence column links a commit, a test, or a pasted result.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **C1** | Greenfield layout | (a) `lib/<Dist>/…pm` + `t/*.t` + `cpanfile`, no build tool; (b) ExtUtils::MakeMaker (`Makefile.PL`); (c) Dist::Zilla | **(a).** `prove -l t/` needs nothing else, and a `cpanfile` is what `cpanm --installdeps .` reads. (b)/(c) are packaging for CPAN release, not for building and testing; a repo that has one keeps it (brownfield never adds or removes a build tool). `tests_dir` is `t/`, `source_dir` is `lib/` — distinct, unlike Go. |
| **C2** | Test runner | `ProveTestRunner`: `perl -c` on each changed `.pm`/`.pl`, then `prove -l t/` (whole suite before green); early-return on the first non-zero; `_clip`-ed output as the refine signal | The two-step shape of `CTestRunner`/`GoTestRunner`. A brownfield run targets the `t/` directory that owns the changed package's tests first, then the whole suite, so a change cannot pass untested (the Go 4.5 false-green). |
| **C3** | Dependency install | `PerlToolEnvironment.ensure` runs `cpanm --installdeps . --notest` when a `cpanfile` exists **and** `cpanm` is on PATH; otherwise it says so and continues | `cpanm` is not core Perl. `perl_toolchain_available()` requires `perl` and `prove` only; a missing `cpanm` is a warning in the run log, never a silent pass and never a hard stop for a repo whose deps are already installed. |
| **C4** | Brownfield placement | the target package is read from the neighbouring `.pm` files' `package` lines and the `lib/` tree; the new module goes at `lib/<Package/Path>.pm` and its test at `t/<name>.t` | The Go 4.5 lesson (placement by an existing package clause, not by directory name). A repo with several `lib/` roots (monorepo of distributions) targets the one whose `cpanfile`/`Makefile.PL` is nearest the grounded landing site. |
| **C5** | Conventions | `perl-conventions` skill reads the repo: Moo/Moose when the repo `use`s it, else classic `bless`; `use strict; use warnings;` always; `Test::More` (or `Test2::V0` when present); a leading underscore for private subs; POD stub per public sub | Read, not assumed — the PHP conventions precedent. |
| **C6** | Preflight | `perl -c` per changed file, always; `perlcritic` only when a `.perlcriticrc` exists | `perl -c` is present wherever `perl` is; `perlcritic` is a CPAN install. Fills the Python-only preflight gap for Perl the way Go's `gofmt`/`go vet` was proposed to. |
| **C7** | `SUPPORTED_LANGUAGES` | `"perl"` added in the **first** commit that also adds layout, scaffold, environment and runner — never earlier | The silent-Python-scaffold trap the Go track closed: `--language perl` exits 2 until the whole set exists. `_resolve_language auto` → Perl when `.pm`/`.pl` are present and Python is not. |
| **C8** | Live proof | greenfield with a real model against a spec; brownfield into the Mojolicious validation repo | Both independently re-run (`prove` from a clean checkout) before a phase is DONE — the Go 4.4 false-green is the precedent this rule exists for. |

---

## 1. What is reused

| Built in… | Reused here |
|---|---|
| codegen language-branch pattern (C#, Go, PHP) | verbatim: layout / scaffold / testenv / testrunner / prompts / conventions |
| build-then-test runner shape (`CTestRunner`, `GoTestRunner`) | `perl -c` → `prove` |
| module-owning test targeting (Go 4.5) | the `t/` directory nearest the changed package |
| `--language` validation (Go) | `"perl"` enters the set with the machinery, not before |
| PKG grounding (`grounding.py`) | the Perl graph from the support track; the grounding fence language is Perl |

## 2. Design

| Piece | What |
|---|---|
| `sdlc/layout.py` | `_SOURCE_EXT["perl"] = "pm"`; `detect_perl_layout` (existing `lib/` + `t/`, `cpanfile`/`Makefile.PL`/`Build.PL`/`dist.ini` as markers); `_resolve_perl_layout` — greenfield = `lib/` + `t/` + `cpanfile` (C1) |
| `sdlc/scaffold.py` | `_perl_files`: `cpanfile`, a stub `lib/<Dist>.pm` (`package`, `use strict; use warnings;`, `1;`), `t/00-load.t` (`use_ok`), README, `.gitignore` — an empty distribution is a green `prove` |
| `sdlc/testenv.py` | `PerlToolEnvironment` (C3); `perl_toolchain_available()` |
| `sdlc/testrunner.py` | `ProveTestRunner` (C2) |
| `sdlc/codegen.py` | Perl variants of the implement / tests / refine prompts; a `layout.language == "perl"` guidance block (package name, `t/` naming, Moo vs classic per C5) |
| `catalog/catalog.py`, `skills.py` | `perl-conventions` capability + skill |
| `sdlc/preflight.py` | the Perl branch of the preflight dispatcher (C6) |
| `sdlc/feature_runner.py` | `SUPPORTED_LANGUAGES`, `_resolve_language`, the toolchain guard with a `FeatureRunError` hint naming `perl` and `prove` |

### 2.1 Testing the phases with Spine itself

Each phase ends with Spine's own surfaces, not only `prove`: `pkg_grounding` (MCP) on the
generated module's landing site to record how many characters of PKG context the run used and which
symbols it named; `blast_radius` on the generated sub after the run, which must list its new test as a
caller; `sdlc_run_result` / the build document for the evidence cell; and `regression_gaps` on the
brownfield repository before and after C-3, which must not grow. Before C-1 touches `sdlc/`,
`blast_radius` on the symbols in §2.2 is re-run and the table refreshed.

### 2.2 Blast radius — measured from the PKG

At `d84e666` (this and the Kotlin track's base). `_resolve_language`: **4 callers** (`run_feature`,
`autorun._require_plan`, two tests), 9 touches — the `auto` branch gains a Perl line, and `autorun`
is the second production caller that must see it. To measure in C-1 step 0, with the command in the
Kotlin roadmap §8: `resolve_layout`, `scaffold`, `make_test_runner` / the runner selection in
`activities.py`, `SUPPORTED_LANGUAGES` readers in `cli/sdlc.py`, and `preflight.py`'s dispatcher.
The PHP codegen merge (`fae8c30`) touched 38 files across `sdlc/`; its diff is the map of every site
a language must reach, and the registry in §5.1 exists so the next language touches one.

---

## 3. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **C-1 Machinery** | §2 in full; unit tests: `test_scaffold_perl_*` (+ idempotency), `test_perl_toolchain_available` (monkeypatched `which`), layout detection, runner argv, the `FeatureRunError` hint; `tests/sdlc/test_perl_integration.py` gated on `perl_toolchain_available()` — scaffold → real `prove` **green and red** | ~3–4 d | integration test green and red against real `perl`/`prove`; `--language perl` validated; gate green | ⬜ | | | |
| **C-2 Greenfield live-proven** | `sdlc feature --language perl` from a spec with a real model, `--safe`; `perl-conventions` selected; grounded on the Perl graph | ~1–2 d | `prove` green, independently re-run from a clean checkout; the run's build document names the grounding used | ⬜ | | | |
| **C-3 Brownfield on the Mojolicious validation repo** | placement per C4 into an existing `lib/` tree; the owning `t/` targeted first, then the suite | ~2–3 d | `prove` green on the changed package **and** the whole suite, independently re-run; no package clause mismatch; grounding measured (chars of PKG context) | ⬜ | | | |
| **C-4 Generic work** (§5.1, §5.2) | the toolchain registry, built while adding Perl's row; the preflight dispatcher if the PHP track left an if-chain | ~1–2 d | `feature_runner`, `activities` and `preflight` select by one table; adding a language is one row + its classes; every existing language's codegen tests pass unchanged | ⬜ | | | |
| **C-5 Preflight + docs + MR** | C6; every row of §6; `/review-pr`; one MR to `develop` | ~1–2 d | preflight runs `perl -c` on a changed file and fails on a syntax error (tested); docs audit clean; verdict "mergeable" | ⬜ | | | |

**Rough total: ~8–12 days.** Delivery is one MR.

---

## 4. Validation

- **Greenfield:** a small spec (a `Shop::Cart` distribution with `subtotal`/`total` and a tax
  rate) — the same shape the corpus `plain` case uses, so the generated code is checkable against
  the graph the support track already labelled.
- **Brownfield:** `mojolicious/mojo` (ephemeral, docs-only name): add a helper to an existing
  package under `lib/Mojo/` with a `t/` test; `prove -l t/mojo/<name>.t` then `prove -l t/`.
  Hypothesis to confirm, the Go-style one: the Mojolicious suite is hermetic and green from a
  clean clone in under a minute, so the brownfield loop needs no environmental wall.

## 5. Generic work — built here, reused by every later codegen track

### 5.1 `sdlc/toolchains.py` — one registry instead of five if-chains *(Perl builds it, C-4)*
Today a language's codegen is wired by `elif lang == "go"` branches in `feature_runner.py`
(`_resolve_language`, the toolchain guard), `activities.py` (runner selection), `layout.py`,
`scaffold.py`, `preflight.py` and `codegen.py`'s prompt maps — the PHP merge touched 38 files to add
one language. A `Toolchain` record per language (`source_ext`, `layout`, `scaffold`, `environment`,
`runner`, `available()`, `preflight`, prompt set, conventions skill id) in one table, with the
call sites reading the table, makes the next language one row plus its classes. Perl's row is the
first written against it; the existing languages migrate in the same phase, with their tests as the
regression net. **Exit:** `grep -c 'lang == "' src/orchestrator/sdlc/*.py` drops to zero in the
dispatch paths; `test_language_validation` proves an unknown language still exits 2.

### 5.2 `make_preflight_runner(language)` *(if the PHP track did not already land it)*
The Go roadmap's pre-existing gap: preflight was Python-only and skipped-as-pass elsewhere. If
`preflight.py` after `fae8c30` dispatches by if-chain, it becomes a row in §5.1's registry; if it
already dispatches by table, Perl adds `perl -c` to it and this item is "reused".

### 5.3 The build-then-test runner template *(shared, exists)*
`CTestRunner` → `GoTestRunner` → `PhpUnitTestRunner` → `ProveTestRunner` all have the shape
"step 1 must pass, then step 2, early-return, `_clip`-ed output". Record it in the codegen section
of `docs/reviewing/language-frontend-checklist.md` as the template with its four exit tests
(green, red, missing toolchain hint, idempotent scaffold), so a runner PR is reviewed against it.

### 5.4 Live-proof evidence format *(shared)*
Every codegen track's "live-proven" claim carries the same four fields in its evidence cell:
model, command, the independent re-run's command and result, and the grounding size. The Go 4.4
false-green is the reason the second field is not optional.

## 6. User-facing documentation — updated in the phase that makes each row true

| Document | What must change | Phase |
|---|---|---|
| `FEATURES.md` | a Perl codegen row (`sdlc feature --language perl`; `prove`; `cpanm` optional) | C-1 |
| `USER_GUIDE.md` | the toolchain passage (Perl codegen needs `perl` and `prove`; `cpanm` optional); the "Multi-language" blockquote's codegen sentence | C-1 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | a Perl row in the toolchain tables | C-1 |
| `SETUP.md` | toolchain prerequisites | C-1 |
| `CLI_REFERENCE.md` | `--language perl` in the `sdlc feature` reference | C-1 |
| `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md`, [perl-support-roadmap.md](perl-support-roadmap.md) D9 | status lines updated to the phase reached | every phase |
| `docs/reviewing/language-frontend-checklist.md`, `CONTRIBUTING.md` | the codegen section: the toolchain registry row a new language adds, the runner template and its four exit tests (§5.1, §5.3) | C-4 |
| `CHANGELOG.md` | one entry under Unreleased per phase | every phase |

## 7. Risks and gotchas

- **`cpanm` absent or offline** — best effort, logged, never a silent pass (C3).
- **`@INC` and `-l`** — `prove -l` adds `lib/`; a repo with `blib/` or a custom `-I` in its
  test harness needs `PERL5LIB` read from `.proverc`/`dist.ini` — read it, do not guess.
- **Test naming** — `t/` files are numbered by convention (`00-load.t`, `10-cart.t`); generated
  tests follow the repo's existing numbering or, greenfield, start at `00-load.t`.
- **Never an XS build** — a distribution with `.xs` sources is compiled by `make`, which this
  track does not run; the run says so and stops.
- **Never commit `episteme/`;** the validation repository's name stays in `docs/`.

## 8. Sequence

```
depends on perl-support-roadmap.md merged (P1 + P2 at least)
C-1 machinery        → prove green + red proven against real perl
C-2 greenfield       → live-proven, independently re-run
C-3 brownfield       → into the Mojolicious repo, owning t/ then the suite
C-4 generic work     → the toolchain registry; every language one row
C-5 preflight + docs → /review-pr, then one MR to develop
```
