# Adding or changing a language front-end — every site that must move together

A front-end is "one module", but registering it touches all of these. A PR that misses one
ships a language that works on the author's machine and nowhere else, or a cache that never
notices the language exists. Check each with `file:line` in the report.

**Starting a new language track:** copy `docs/specs/templates/language-track.md` rather than
re-deriving the roadmap shape from an existing one. Its living phase table is checked by
`scripts/roadmap-status.py --check`; the real-repository smoke test below is
`scripts/validate-frontend.py <language> <git-url> [<git-url> ...]`.

**D1 (parser choice) — run `scripts/parse-census.py <grammar-module> <dir>` before writing
the extractor**, not after: it parses every file of the language with the candidate grammar
and reports the recall ceiling (files with a parse `ERROR`, lines inside `ERROR` spans,
declaration counts by CST kind) independent of any extraction logic, so the D1 recall number
in the roadmap's own evidence is measured before a line of `pkg/<lang>_extractor.py` exists,
not reverse-engineered from it afterward.

## Registration (src)

| Site | What | Failure if missed |
|---|---|---|
| `pkg/<lang>_extractor.py` | `language`, `suffixes`, `module_name`, `extract`, optional `finalize`; lazy `_<lang>_parser()`; `TYPE_CHECKING`-guarded `TSNode` | — |
| `pkg/extractor.py` `default_extractors()` | gated append: `find_spec` **before** a function-local import | base install imports the grammar |
| `pkg/capabilities.py` `FRONT_ENDS` | entry in registry order | capability matrix omits the language; `test_capabilities` fixtures |
| `doctor.py` `EXTRA_PROBES` | extra → probe module | `doctor` cannot report the extra |
| `pkg/persistence.py` `_GRAMMAR_MODULES` | the grammar module | **cache key ignores the extra: a warm cache serves a graph without the language forever** |
| `catalog/profile.py` | suffix → language; manifest marker file; framework needles; test runner | repo profiles as no language; `state` "Stack" line wrong |
| `pkg/scope.py` | `WALKERS[lang]` or `NOT_APPLICABLE[lang]` with a reason | invention oracle reports an unmeasured zero as clean; `test_scope` roster test |
| `pkg/import_link.py` | a matcher, or a documented reason none is needed | orphan-rate / external-ratio errors in `pkg verify` |
| `pkg/docs.py`, `pkg/doc_link.py` | source extension in the drift/ident sets | doc binder treats `Foo.php` as a symbol |
| `knowledge/insights.py` | visibility rule or explicit `None` | public/private split guesses |
| `sdlc/feature_runner.py` `SUPPORTED_LANGUAGES` | **only** with layout/scaffold/testenv/testrunner/prompts | `--language x` silently scaffolds Python |

## Packaging and CI

| Site | What |
|---|---|
| `pyproject.toml` | `<lang> = [...]` extra; `languages` meta-extra; mypy `ignore_missing_imports` module |
| `uv.lock` | relocked |
| `.github/workflows/ci.yml` | `--extra <lang>` on the sync line (else the biconditional test proves nothing) |

## Tests

`tests/pkg/test_<lang>_extractor.py` (module-level `importorskip`); `test_default_extractors.py`
biconditional + end-to-end; `test_capabilities.py` `_FIXTURES`; `test_verifier.py` per-front-end
freshness; `tests/catalog/test_profile.py`; `test_scope.py` roster; `test_doctor.py` if probes are
enumerated; a persistence test that the fingerprint changes with the grammar present.

## Corpus

`corpus/<lang>/<case>/{expected.json,.repo/…}` — the dot on `.repo/` is load-bearing; labels
from source; `known_gaps` predicted; a case per invention shape the spec forbids, containing the
shape whose wrong answer would score; `corpus/README.md` vocabulary row; `scoreboard.json`
regenerated with `--scoreboard` and the PR saying so.

## Docs

See `docs-matrix.md`, row "New language front-end".

## Precision rules every front-end must honour (findings if violated)

- Emit nothing for a computed path, prefix, name, or target. A wrong grounded fact is worse
  than a missing one.
- Never treat `self` / `static` / `parent` / `this` / `__PACKAGE__` as a class name.
- Never emit a `CALLS` edge whose method name came from a variable.
- No `ANY`-verb endpoints (`endpoints-typescript-go.md` D2).
- A closure handler yields an `Endpoint` and no `EXPOSES`.
- A guessed namespace is repointed or dropped in `finalize`; a guessed **method** id has no
  backstop and must not be emitted unverified.
- Templates, vendored trees, generated code, and build output are skipped by name, not
  parsed and hoped for.
